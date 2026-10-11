"""
shared backend plumbing: firestore access, auth, and small helpers.

firebase is initialized lazily so the app (and the pure services) can be
imported in tests without credentials. tests swap in a fake database and a
fake token verifier with set_db() / set_token_verifier().
"""

import os
import json
import time
import base64
import threading
from collections import deque
from datetime import datetime, date, timezone
from functools import wraps

from flask import request, jsonify, g

import firebase_admin
from firebase_admin import credentials, auth, firestore


# -----------------------------------------------------------------------------
# firebase admin SDK initialization (lazy)
# -----------------------------------------------------------------------------
def init_firebase():
    if firebase_admin._apps:
        return
    creds_value = os.environ.get("FIREBASE_CREDENTIALS")
    if not creds_value:
        raise RuntimeError("FIREBASE_CREDENTIALS env var is not set.")

    if os.path.isfile(creds_value):
        cred = credentials.Certificate(creds_value)
    else:
        try:
            decoded = base64.b64decode(creds_value).decode("utf-8")
            cred_dict = json.loads(decoded)
            cred = credentials.Certificate(cred_dict)
        except Exception as e:
            raise RuntimeError(f"FIREBASE_CREDENTIALS is neither a valid file path nor valid base64 json: {e}")

    firebase_admin.initialize_app(cred)


_db = None
_db_lock = threading.Lock()


def get_db():
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                init_firebase()
                _db = firestore.client()
    return _db


def set_db(db):
    """used by tests to inject an in-memory firestore."""
    global _db
    _db = db


def user_doc(user_id):
    return get_db().collection("users").document(user_id)


# -----------------------------------------------------------------------------
# auth
# -----------------------------------------------------------------------------
def _firebase_verify(id_token):
    init_firebase()
    return auth.verify_id_token(id_token)


_token_verifier = _firebase_verify


def set_token_verifier(fn):
    """used by tests to bypass firebase token verification."""
    global _token_verifier
    _token_verifier = fn or _firebase_verify


def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "missing or malformed Authorization header"}), 401

        id_token = auth_header.split("Bearer ", 1)[1].strip()

        try:
            decoded = _token_verifier(id_token)
            g.user_id = decoded["uid"]
            g.user_email = decoded.get("email")
        except auth.InvalidIdTokenError:
            return jsonify({"error": "invalid id token"}), 401
        except auth.ExpiredIdTokenError:
            return jsonify({"error": "id token expired, please re-authenticate"}), 401
        except Exception:
            return jsonify({"error": "authentication failed"}), 401

        return f(*args, **kwargs)
    return decorated


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def serialize_timestamps(data, fields):
    """convert firestore timestamp fields to ISO strings for JSON serialization."""
    for field in fields:
        if data.get(field):
            val = data[field]
            if hasattr(val, "isoformat"):
                data[field] = val.isoformat()
    return data


def to_jsonable(value):
    """recursively convert datetimes/dates so a firestore dict can be jsonify'd."""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return None
    return value


def as_date(value):
    """firestore timestamp / datetime / 'YYYY-MM-DD' string → date (or None).

    date-only fields are stored as UTC midnight, so the UTC calendar date is the
    intended date.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def date_to_storage(d):
    """date → naive datetime at midnight (firestore stores it as UTC midnight)."""
    if d is None:
        return None
    return datetime(d.year, d.month, d.day)


def parse_client_today(raw):
    """the client's local date, validated to be within a day of the server's UTC date.

    the server can't know the student's timezone, so date-sensitive endpoints
    accept ?today=YYYY-MM-DD and fall back to the UTC date.
    """
    server_today = datetime.now(timezone.utc).date()
    if not raw:
        return server_today
    try:
        d = date.fromisoformat(raw[:10])
    except ValueError:
        return server_today
    if abs((d - server_today).days) > 1:
        return server_today
    return d


def bad_request(message, status=400):
    return jsonify({"error": message}), status


def stream_dicts(query):
    """stream a query into a list of dicts with 'id' set."""
    out = []
    for doc in query.stream():
        d = doc.to_dict() or {}
        d["id"] = doc.id
        out.append(d)
    return out


def commit_in_chunks(ops, chunk_size=400):
    """apply a list of (kind, ref, data) write ops using batches of <500 writes."""
    db = get_db()
    for i in range(0, len(ops), chunk_size):
        batch = db.batch()
        for kind, ref, data in ops[i:i + chunk_size]:
            if kind == "set":
                batch.set(ref, data)
            elif kind == "merge":
                batch.set(ref, data, merge=True)
            elif kind == "update":
                batch.update(ref, data)
            elif kind == "delete":
                batch.delete(ref)
        batch.commit()


# -----------------------------------------------------------------------------
# background jobs
# -----------------------------------------------------------------------------
# long-running ai work (document extraction, question generation) runs in a
# daemon thread; status lives on the firestore document so the client can poll
# and a crashed job can be retried. tests run jobs synchronously.
_sync_jobs = os.environ.get("MARGIN_SYNC_JOBS") == "1"


def set_sync_jobs(value):
    global _sync_jobs
    _sync_jobs = bool(value)


def run_job(fn, *args, **kwargs):
    if _sync_jobs:
        fn(*args, **kwargs)
        return
    t = threading.Thread(target=fn, args=args, kwargs=kwargs, daemon=True)
    t.start()


# -----------------------------------------------------------------------------
# per-user limits on endpoints that trigger paid ai calls
# -----------------------------------------------------------------------------
# in-memory, per process: enough to stop runaway clients and accidental loops.
# a multi-instance deployment would need a shared store (see README).
AI_LIMITS_PER_HOUR = {
    "extract": int(os.environ.get("MARGIN_AI_EXTRACT_PER_HOUR", 20)),
    "tasks": int(os.environ.get("MARGIN_AI_TASKS_PER_HOUR", 20)),
    "concepts": int(os.environ.get("MARGIN_AI_CONCEPTS_PER_HOUR", 10)),
    "questions": int(os.environ.get("MARGIN_AI_QUESTIONS_PER_HOUR", 30)),
}
_rate = {}
_rate_lock = threading.Lock()


def ai_allowed(uid, kind):
    """record one use of an ai-backed action; False if the hourly limit is reached."""
    limit = AI_LIMITS_PER_HOUR[kind]
    now = time.monotonic()
    with _rate_lock:
        q = _rate.setdefault((uid, kind), deque())
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def reset_rate_limits():
    with _rate_lock:
        _rate.clear()


def ai_limited_response():
    return jsonify({"error": "you've hit the hourly limit for AI features — try again a bit later"}), 429
