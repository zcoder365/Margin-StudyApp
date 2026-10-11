"""
minimal in-memory stand-in for the firestore admin client.

it implements only the API surface margin uses: collection/document refs,
get/set/update/delete, where/order_by/limit/stream, batches, and the
SERVER_TIMESTAMP / Increment / ArrayUnion / DELETE_FIELD transforms. like real
firestore, naive datetimes come back as timezone-aware UTC values.
"""

import copy
import itertools
import re
from datetime import datetime, timezone

_ids = itertools.count(1)


def _now():
    return datetime.now(timezone.utc)


def _is_transform(value, name):
    return type(value).__name__ == name


def _check_storable(value, path="value"):
    """reject values the real firestore client can't encode (plain dates, sets, objects)."""
    from datetime import date as _date
    if value is None or isinstance(value, (bool, int, float, str, bytes, datetime)):
        return
    if isinstance(value, _date):
        raise TypeError(f"{path}: firestore can't store a date without a time — use a datetime")
    if isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _check_storable(v, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"{path}: map keys must be strings")
            _check_storable(v, f"{path}.{k}")
        return
    if type(value).__name__ in ("Sentinel", "Increment", "ArrayUnion", "ArrayRemove"):
        return
    raise TypeError(f"{path}: firestore can't store {type(value).__name__}")


def _normalize(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _apply_value(existing, value):
    if _is_transform(value, "Sentinel"):
        text = str(value)
        if "server timestamp" in text:
            return _now()
        if "delete a field" in text:
            return _DELETE
    if _is_transform(value, "Increment"):
        return (existing or 0) + value.value
    if _is_transform(value, "ArrayUnion"):
        base = list(existing or [])
        for v in value.values:
            if v not in base:
                base.append(v)
        return base
    if _is_transform(value, "ArrayRemove"):
        return [v for v in (existing or []) if v not in value.values]
    _check_storable(value)
    return _normalize(copy.deepcopy(value))


_DELETE = object()


def _set_path(data, path, value):
    parts = path.split(".")
    cur = data
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    existing = cur.get(parts[-1])
    new = _apply_value(existing, value)
    if new is _DELETE:
        cur.pop(parts[-1], None)
    else:
        cur[parts[-1]] = new


def _get_path(data, path):
    cur = data
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


class NotFound(Exception):
    pass


class FakeSnapshot:
    def __init__(self, ref, data):
        self.reference = ref
        self.id = ref.id
        self._data = data

    @property
    def exists(self):
        return self._data is not None

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None

    def get(self, field):
        return _get_path(self._data or {}, field)


class FakeDocumentRef:
    def __init__(self, db, path):
        self._db = db
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def collection(self, name):
        return FakeCollectionRef(self._db, f"{self.path}/{name}")

    def get(self, transaction=None):
        return FakeSnapshot(self, copy.deepcopy(self._db.docs.get(self.path)))

    def set(self, data, merge=False):
        current = self._db.docs.get(self.path) if merge else None
        base = copy.deepcopy(current) if current else {}
        for k, v in data.items():
            if merge and isinstance(v, dict) and isinstance(base.get(k), dict):
                merged = base[k]
                for kk, vv in v.items():
                    _set_path(merged, kk, vv)
            else:
                # set() treats keys literally (only update() interprets dotted paths)
                new = _apply_value(base.get(k), v)
                if new is _DELETE:
                    base.pop(k, None)
                else:
                    base[k] = new
        self._db.docs[self.path] = base

    def update(self, data):
        # like the real client, update() keys are field paths: each dotted part must be
        # a simple identifier unless it is `backtick-quoted`
        for key in data:
            for part in key.split("."):
                if not (re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part) or re.fullmatch(r"`[^`]+`", part)):
                    raise ValueError(f"invalid field path component: {part!r}")
        if self.path not in self._db.docs:
            raise NotFound(f"no document to update: {self.path}")
        base = copy.deepcopy(self._db.docs[self.path])
        for k, v in data.items():
            _set_path(base, k, v)
        self._db.docs[self.path] = base

    def delete(self):
        self._db.docs.pop(self.path, None)

    def __eq__(self, other):
        return isinstance(other, FakeDocumentRef) and other.path == self.path

    def __hash__(self):
        return hash(self.path)


class FakeQuery:
    def __init__(self, db, coll_path, filters=None, orders=None, limit_n=None):
        self._db = db
        self._coll = coll_path
        self._filters = filters or []
        self._orders = orders or []
        self._limit = limit_n

    def where(self, field=None, op=None, value=None, filter=None):
        if filter is not None:
            field, op, value = filter.field_path, filter.op_string, filter.value
        return FakeQuery(self._db, self._coll, self._filters + [(field, op, value)], self._orders, self._limit)

    def order_by(self, field, direction="ASCENDING"):
        return FakeQuery(self._db, self._coll, self._filters, self._orders + [(field, direction)], self._limit)

    def limit(self, n):
        return FakeQuery(self._db, self._coll, self._filters, self._orders, n)

    def _match(self, data):
        for field, op, value in self._filters:
            actual = _get_path(data, field)
            value = _normalize(value)
            if op == "==" and actual != value:
                return False
            if op == "!=" and actual == value:
                return False
            if op == "in" and actual not in value:
                return False
            if op == "array_contains" and (not isinstance(actual, list) or value not in actual):
                return False
            if op in ("<", "<=", ">", ">="):
                if actual is None:
                    return False
                if op == "<" and not actual < value:
                    return False
                if op == "<=" and not actual <= value:
                    return False
                if op == ">" and not actual > value:
                    return False
                if op == ">=" and not actual >= value:
                    return False
        return True

    def stream(self, transaction=None):
        prefix = self._coll + "/"
        rows = []
        for path, data in list(self._db.docs.items()):
            if not path.startswith(prefix) or "/" in path[len(prefix):]:
                continue
            if self._match(data):
                rows.append((path, data))
        for field, direction in reversed(self._orders):
            # like firestore, documents missing the order_by field are excluded
            rows = [r for r in rows if _get_path(r[1], field) is not None]
            rows.sort(key=lambda r: _get_path(r[1], field), reverse=str(direction).upper().startswith("DESC"))
        if self._limit is not None:
            rows = rows[: self._limit]
        return iter([FakeSnapshot(FakeDocumentRef(self._db, p), copy.deepcopy(d)) for p, d in rows])

    def get(self, transaction=None):
        return list(self.stream())


class FakeCollectionRef(FakeQuery):
    def __init__(self, db, path):
        super().__init__(db, path)
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def document(self, doc_id=None):
        if doc_id is None:
            doc_id = f"id{next(_ids):05d}"
        return FakeDocumentRef(self._db, f"{self.path}/{doc_id}")

    def add(self, data):
        ref = self.document()
        ref.set(data)
        return None, ref


class FakeBatch:
    def __init__(self, db):
        self._db = db
        self._ops = []
        self.committed = False

    def set(self, ref, data, merge=False):
        self._ops.append(("set", ref, data, merge))

    def update(self, ref, data):
        self._ops.append(("update", ref, data, False))

    def delete(self, ref):
        self._ops.append(("delete", ref, None, False))

    def commit(self):
        if len(self._ops) > 500:
            raise ValueError("a batch may contain at most 500 writes")
        if self._db.fail_next_commit:
            self._db.fail_next_commit = False
            raise RuntimeError("simulated commit failure")
        # validate updates first so the batch is all-or-nothing
        for kind, ref, _data, _merge in self._ops:
            if kind == "update" and ref.path not in self._db.docs:
                raise NotFound(f"no document to update: {ref.path}")
        snapshot = copy.deepcopy(self._db.docs)
        try:
            for kind, ref, data, merge in self._ops:
                if kind == "set":
                    ref.set(data, merge=merge)
                elif kind == "update":
                    ref.update(data)
                else:
                    ref.delete()
        except Exception:
            self._db.docs = snapshot
            raise
        self.committed = True
        return []


class FakeFirestore:
    def __init__(self):
        self.docs = {}
        self.fail_next_commit = False

    def collection(self, name):
        return FakeCollectionRef(self, name)

    def batch(self):
        return FakeBatch(self)

    # helpers for assertions
    def collection_dicts(self, path):
        prefix = path + "/"
        return {
            p[len(prefix):]: copy.deepcopy(d)
            for p, d in self.docs.items()
            if p.startswith(prefix) and "/" not in p[len(prefix):]
        }
