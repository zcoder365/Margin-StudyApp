import os
import sys

import pytest

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)

# never touch real firebase / gemini from tests
os.environ["MARGIN_SYNC_JOBS"] = "1"
os.environ.pop("GEMINI_API_KEY", None)

import core  # noqa: E402
import ai  # noqa: E402
from tests.fake_firestore import FakeFirestore  # noqa: E402


class FakeAI:
    """records prompts and returns queued responses (or raises queued errors)."""

    def __init__(self):
        self.responses = []
        self.prompts = []
        self.available = True

    def queue(self, response):
        self.responses.append(response)

    def generate_json(self, prompt, retries=1):
        self.prompts.append(prompt)
        if not self.responses:
            raise ai.AIError("no fake response queued", kind="error")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def fake_verify(token):
    # tokens look like "uid:<uid>" in tests
    if not token.startswith("uid:"):
        raise ValueError("bad token")
    uid = token.split(":", 1)[1]
    return {"uid": uid, "email": f"{uid}@example.com"}


@pytest.fixture
def db():
    fake = FakeFirestore()
    core.set_db(fake)
    yield fake
    core.set_db(None)


@pytest.fixture
def fake_ai():
    f = FakeAI()
    ai.set_ai(f)
    yield f
    ai.set_ai(None)


@pytest.fixture
def app(db, fake_ai):
    core.set_token_verifier(fake_verify)
    core.set_sync_jobs(True)
    import app as app_module
    app_module.app.config["TESTING"] = True
    yield app_module.app
    core.set_token_verifier(None)


class ApiClient:
    def __init__(self, flask_client, uid):
        self.c = flask_client
        self.uid = uid

    def _h(self):
        return {"Authorization": f"Bearer uid:{self.uid}"}

    def get(self, path, **kw):
        return self.c.get("/api" + path, headers=self._h(), **kw)

    def post(self, path, json=None, **kw):
        return self.c.post("/api" + path, json=json, headers=self._h(), **kw)

    def put(self, path, json=None):
        return self.c.put("/api" + path, json=json, headers=self._h())

    def patch(self, path, json=None):
        return self.c.patch("/api" + path, json=json, headers=self._h())

    def delete(self, path):
        return self.c.delete("/api" + path, headers=self._h())

    def upload(self, path, field, data, filename, extra=None):
        import io
        form = {field: (io.BytesIO(data), filename)}
        form.update(extra or {})
        return self.c.post("/api" + path, data=form, headers=self._h(), content_type="multipart/form-data")


@pytest.fixture
def client(app):
    c = ApiClient(app.test_client(), "alice")
    c.post("/users/init", json={"displayName": "Alice"})
    return c


@pytest.fixture
def other_client(app):
    c = ApiClient(app.test_client(), "mallory")
    c.post("/users/init", json={"displayName": "Mallory"})
    return c


def make_pdf(pages_text):
    """build a small text PDF in-memory (pypdf can read it back)."""
    objects = []
    page_ids = []
    font_id = 3
    next_id = 4
    content_objs = []
    for text in pages_text:
        lines = text.split("\n")
        ops = ["BT", "/F1 11 Tf", "14 TL", "50 750 Td"]
        for line in lines:
            safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({safe}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1", "replace")
        content_id, page_id = next_id, next_id + 1
        next_id += 2
        content_objs.append((content_id, b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"))
        content_objs.append((page_id, (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode()))
        page_ids.append(page_id)
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    objects.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append((2, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()))
    objects.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.extend(content_objs)
    objects.sort()
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for oid, body in objects:
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n0000000000 65535 f \n".encode()
    for oid in range(1, n):
        out += f"{offsets[oid]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
