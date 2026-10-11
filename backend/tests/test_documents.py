"""phase 1: document intelligence — normalization, issue detection, and the review/confirm api."""

import pytest

from ai import AIError
from services import documents as docsvc
from tests.conftest import make_pdf

SYLLABUS_PAGES = [
    "COMP 330 Theory of Computation - Fall 2026\n"
    "Grading: Assignments 30%, Midterm exam 30%, Final exam 40%.\n"
    "Assignment 1 is due October 3, 2026.\n"
    "Assignment 2 is due October 24, 2026.",
    "Midterm exam: October 20, 2026 in class.\n"
    "Final exam: TBA during the exam period.\n"
    "Learning outcomes: design finite automata for regular languages.\n"
    "Prove languages are not regular using the pumping lemma.",
]


def syllabus_response():
    return {
        "assessments": [
            {"title": "Assignment 1", "kind": "assignment", "dueDate": "2026-10-03", "dueDateText": "October 3, 2026",
             "weightPercent": 15, "weightText": "Assignments 30%", "estimatedHours": None,
             "requirements": [], "milestones": [], "quote": "Assignment 1 is due October 3, 2026.", "page": 1},
            {"title": "Assignment 2", "kind": "assignment", "dueDate": "2026-10-24", "dueDateText": "October 24, 2026",
             "weightPercent": 15, "quote": "Assignment 2 is due October 24, 2026.", "page": 1},
            {"title": "Midterm exam", "kind": "exam", "dueDate": "2026-10-20", "dueDateText": "October 20, 2026",
             "weightPercent": 30, "estimatedHours": 10, "estimatedHoursIsGuess": True,
             "quote": "Midterm exam: October 20, 2026 in class.", "page": 2},
            {"title": "Final exam", "kind": "exam", "dueDate": None, "dueDateText": "TBA during the exam period",
             "weightPercent": 40, "quote": "Final exam: TBA during the exam period.", "page": 2},
        ],
        "learningObjectives": [
            {"text": "Design finite automata for regular languages", "quote": "design finite automata for regular languages",
             "page": 2, "relatedAssessments": ["Midterm exam"]},
            {"text": "Use the pumping lemma to prove non-regularity",
             "quote": "Prove languages are not regular using the pumping lemma.", "page": 2,
             "relatedAssessments": ["Midterm exam", "Final exam"]},
        ],
        "notes": [],
    }


# -----------------------------------------------------------------------------
# pure service tests
# -----------------------------------------------------------------------------
def test_normalize_valid_extraction():
    ex = docsvc.normalize_extraction(syllabus_response(), SYLLABUS_PAGES)
    titles = [a["fields"]["title"] for a in ex["assessments"]]
    assert titles == ["Assignment 1", "Assignment 2", "Midterm exam", "Final exam"]
    a1 = ex["assessments"][0]
    assert a1["fields"]["dueDate"] == "2026-10-03" and a1["fields"]["weight"] == 15
    assert a1["source"] == {"quote": "Assignment 1 is due October 3, 2026.", "page": 1, "verified": True}
    midterm = ex["assessments"][2]
    assert midterm["estimatedHoursOrigin"] == "ai_suggested"
    final = ex["assessments"][3]
    assert final["fields"]["dueDate"] is None
    assert any(i["code"] == "ambiguous_date" for i in final["issues"])
    assert len(ex["objectives"]) == 2 and ex["objectives"][0]["source"]["page"] == 2


def test_normalize_rejects_malformed_shape():
    with pytest.raises(docsvc.DocumentError):
        docsvc.normalize_extraction(["not", "an", "object"], SYLLABUS_PAGES)
    with pytest.raises(docsvc.DocumentError):
        docsvc.normalize_extraction({"assessments": "nope"}, SYLLABUS_PAGES)


def test_normalize_drops_and_flags_bad_items():
    raw = {"assessments": [
        {"kind": "exam"},  # no title → dropped
        "garbage",
        {"title": "Quiz 1", "kind": "weird", "dueDate": "2026-02-30", "weightPercent": 140,
         "quote": "this sentence is not in the document at all", "page": 1},
        {"title": "Lab 2", "kind": "lab", "dueDate": "2026-10-05", "dueDateText": "Oct 5"},
    ]}
    ex = docsvc.normalize_extraction(raw, SYLLABUS_PAGES)
    assert ex["dropped"] == 2
    quiz, lab = ex["assessments"]
    assert quiz["fields"]["kind"] == "other"
    assert quiz["fields"]["dueDate"] is None and quiz["fields"]["weight"] is None
    codes = {i["code"] for i in quiz["issues"]}
    assert {"invalid_date", "invalid_weight", "unverified_source"} <= codes
    lab_codes = {i["code"] for i in lab["issues"]}
    assert "year_inferred" in lab_codes and "no_source" in lab_codes
    assert "missing_weight" in lab_codes


def test_never_invents_missing_values():
    raw = {"assessments": [{"title": "Essay", "kind": "assignment"}]}
    ex = docsvc.normalize_extraction(raw, ["Essay instructions"])
    f = ex["assessments"][0]["fields"]
    assert f["dueDate"] is None and f["weight"] is None and f["estimatedHours"] is None
    codes = {i["code"] for i in ex["assessments"][0]["issues"]}
    assert "missing_date" in codes and "missing_weight" in codes


def test_detect_conflicting_and_duplicate_items():
    raw = {"assessments": [
        {"title": "Midterm", "kind": "exam", "dueDate": "2026-10-20", "weightPercent": 30},
        {"title": "Midterm", "kind": "exam", "dueDate": "2026-10-22", "weightPercent": 30},
        {"title": "Final", "kind": "exam", "weightPercent": 30},
    ]}
    ex = docsvc.detect_issues(docsvc.normalize_extraction(raw, ["x"]), [])
    m1, m2, _ = ex["assessments"]
    assert any(i["code"] == "conflicting_dates" for i in m1["issues"])
    assert any(i["code"] == "duplicate_in_document" for i in m2["issues"])
    assert ex["warnings"][0]["code"] == "weights_total"  # 90%


def test_detect_matches_existing_assessment():
    raw = {"assessments": [{"title": "Problem Set 1", "kind": "assignment", "dueDate": "2026-10-05"},
                           {"title": "Problem Set 2", "kind": "assignment", "dueDate": "2026-10-12"}]}
    existing = [{"id": "p1", "title": "problem set 1", "dueDate": "2026-10-04"}]
    ex = docsvc.detect_issues(docsvc.normalize_extraction(raw, ["x"]), existing)
    ps1, ps2 = ex["assessments"]
    assert ps1["action"] == "update" and ps1["targetId"] == "p1"
    assert any(i["code"] == "conflicts_existing" for i in ps1["issues"])
    # numbers must match: PS2 is not a duplicate of PS1
    assert ps2["action"] == "create" and ps2["targetId"] is None


def test_title_similarity_and_quote_location():
    assert docsvc.title_similarity("Assignment 1", "assignment 1") == 1.0
    assert docsvc.title_similarity("Assignment 1", "Assignment 2") == 0.0
    assert docsvc.locate_quote("assignment 2 is DUE october 24, 2026", SYLLABUS_PAGES) == 1
    assert docsvc.locate_quote("Midterm exam: October 20", SYLLABUS_PAGES, claimed_page=1) == 2
    assert docsvc.locate_quote("quantum chromodynamics lecture", SYLLABUS_PAGES) is None


def test_validate_review_fields():
    clean, errs = docsvc.validate_review_fields({"title": " A1 ", "kind": "assignment", "dueDate": "2026-10-04", "weight": "15"})
    assert errs == [] and clean == {"title": "A1", "kind": "assignment", "dueDate": "2026-10-04", "weight": 15.0, "estimatedHours": None}
    _, errs = docsvc.validate_review_fields({"title": "", "kind": "nope", "dueDate": "10/04", "weight": 101})
    assert len(errs) == 4


def test_sniff_kind():
    assert docsvc.sniff_kind(b"%PDF-1.4 ...", "x.pdf") == "pdf"
    assert docsvc.sniff_kind(b"hello", "notes.txt") == "text"
    assert docsvc.sniff_kind(b"MZ\x90\x00", "evil.pdf") is None
    assert docsvc.sniff_kind(b"hello", "x.exe") is None


# -----------------------------------------------------------------------------
# api flow
# -----------------------------------------------------------------------------
@pytest.fixture
def course(client):
    return client.post("/courses", json={"name": "COMP 330", "color": "#abc"}).get_json()


def upload_syllabus(client, course_id, fake_ai, response=None):
    fake_ai.queue(response or syllabus_response())
    r = client.upload(f"/courses/{course_id}/documents", "file", make_pdf(SYLLABUS_PAGES), "syllabus.pdf",
                      extra={"docType": "syllabus"})
    assert r.status_code == 202, r.get_json()
    return r.get_json()


def test_syllabus_review_correct_and_confirm(client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai)
    # nothing reaches the planner before review
    assert client.get(f"/projects?courseId={course['id']}").get_json()["projects"] == []

    d = client.get(f"/documents/{doc['id']}").get_json()
    assert d["status"] == "needs_review" and d["pageCount"] == 2
    items = d["extraction"]["assessments"]
    keys = {a["fields"]["title"]: a["key"] for a in items}
    obj_keys = [o["key"] for o in d["extraction"]["objectives"]]

    # the student corrects assignment 1's date, rejects the final (no date yet), confirms the rest
    payload = {
        "assessments": [
            {"key": keys["Assignment 1"], "decision": "confirm", "fields": {"dueDate": "2026-10-04"}},
            {"key": keys["Assignment 2"], "decision": "confirm"},
            {"key": keys["Midterm exam"], "decision": "confirm"},
            {"key": keys["Final exam"], "decision": "reject"},
            {"key": "not-a-real-key", "decision": "confirm", "fields": {"title": "injected"}},
        ],
        "objectives": [{"key": k, "decision": "confirm"} for k in obj_keys],
    }
    r = client.post(f"/documents/{doc['id']}/apply", json=payload)
    assert r.status_code == 200, r.get_json()
    res = r.get_json()
    assert len(res["created"]) == 3 and res["rejected"] == 1

    projects = {p["title"]: p for p in client.get(f"/projects?courseId={course['id']}").get_json()["projects"]}
    assert set(projects) == {"Assignment 1", "Assignment 2", "Midterm exam"}
    a1 = projects["Assignment 1"]
    assert a1["dueDate"].startswith("2026-10-04")
    assert a1["verification"] == "confirmed" and a1["source"]["documentId"] == doc["id"]
    assert a1["weight"] == 15 and a1["kind"] == "assignment"
    assert projects["Midterm exam"]["estimatedHoursOrigin"] == "ai_suggested"

    # verified deadlines show up in the planner
    deadlines = client.get("/calendar").get_json()["deadlines"]
    assert {(x["title"], x["date"]) for x in deadlines} >= {("Assignment 1", "2026-10-04"), ("Midterm exam", "2026-10-20")}

    objectives = client.get(f"/courses/{course['id']}/objectives").get_json()["objectives"]
    assert len(objectives) == 2
    pumping = next(o for o in objectives if "pumping" in o["text"])
    assert pumping["assessmentIds"] == [projects["Midterm exam"]["id"]]  # final was rejected

    # applying again is idempotent
    r2 = client.post(f"/documents/{doc['id']}/apply", json=payload).get_json()
    assert r2["created"] == [] and len(r2["updated"]) == 3
    assert len(client.get(f"/projects?courseId={course['id']}").get_json()["projects"]) == 3


def test_reprocessing_does_not_duplicate(client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai)
    d = client.get(f"/documents/{doc['id']}").get_json()
    payload = {"assessments": [{"key": a["key"], "decision": "confirm"} for a in d["extraction"]["assessments"][:3]]}
    client.post(f"/documents/{doc['id']}/apply", json=payload)
    before = client.get(f"/projects?courseId={course['id']}").get_json()["projects"]

    fake_ai.queue(syllabus_response())
    r = client.post(f"/documents/{doc['id']}/process", json={"force": True})
    assert r.status_code == 202
    d2 = client.get(f"/documents/{doc['id']}").get_json()
    applied = [a for a in d2["extraction"]["assessments"] if a["fields"]["title"] != "Final exam"]
    assert all(a["action"] == "update" and a["targetId"] for a in applied)
    payload2 = {"assessments": [{"key": a["key"], "decision": "confirm"} for a in applied]}
    res = client.post(f"/documents/{doc['id']}/apply", json=payload2).get_json()
    assert res["created"] == []
    after = client.get(f"/projects?courseId={course['id']}").get_json()["projects"]
    assert len(after) == len(before) == 3


def test_matches_manual_assessment_and_flags_conflict(client, course, fake_ai):
    manual = client.post("/projects", json={"courseId": course["id"], "title": "Midterm Exam", "dueDate": "2026-10-21"}).get_json()
    doc = upload_syllabus(client, course["id"], fake_ai)
    d = client.get(f"/documents/{doc['id']}").get_json()
    mid = next(a for a in d["extraction"]["assessments"] if a["fields"]["title"] == "Midterm exam")
    assert mid["action"] == "update" and mid["targetId"] == manual["id"]
    assert any(i["code"] == "conflicts_existing" for i in mid["issues"])
    client.post(f"/documents/{doc['id']}/apply", json={"assessments": [{"key": mid["key"], "decision": "confirm"}]})
    projects = client.get(f"/projects?courseId={course['id']}").get_json()["projects"]
    assert len(projects) == 1 and projects[0]["dueDate"].startswith("2026-10-20")


def test_invalid_confirmed_item_writes_nothing(client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai)
    d = client.get(f"/documents/{doc['id']}").get_json()
    a1, a2 = d["extraction"]["assessments"][:2]
    r = client.post(f"/documents/{doc['id']}/apply", json={"assessments": [
        {"key": a1["key"], "decision": "confirm"},
        {"key": a2["key"], "decision": "confirm", "fields": {"weight": 250}},
    ]})
    assert r.status_code == 400 and r.get_json()["details"]
    assert client.get(f"/projects?courseId={course['id']}").get_json()["projects"] == []


def test_missing_weights_are_saved_as_unknown(client, course, fake_ai):
    resp = {"assessments": [{"title": "Reading response", "kind": "assignment", "dueDate": "2026-10-10"}]}
    doc = upload_syllabus(client, course["id"], fake_ai, resp)
    d = client.get(f"/documents/{doc['id']}").get_json()
    item = d["extraction"]["assessments"][0]
    assert any(i["code"] == "missing_weight" for i in item["issues"])
    client.post(f"/documents/{doc['id']}/apply", json={"assessments": [{"key": item["key"], "decision": "confirm"}]})
    p = client.get(f"/projects?courseId={course['id']}").get_json()["projects"][0]
    assert p["weight"] is None


def test_ai_failure_marks_document_failed_and_retry_works(client, course, fake_ai):
    fake_ai.queue(AIError("the AI provider timed out", kind="timeout"))
    r = client.upload(f"/courses/{course['id']}/documents", "file", make_pdf(SYLLABUS_PAGES), "s.pdf", extra={"docType": "syllabus"})
    doc = r.get_json()
    d = client.get(f"/documents/{doc['id']}").get_json()
    assert d["status"] == "failed" and d["errorKind"] == "timeout" and d.get("extraction") is None
    fake_ai.queue(syllabus_response())
    client.post(f"/documents/{doc['id']}/process", json={})
    assert client.get(f"/documents/{doc['id']}").get_json()["status"] == "needs_review"


def test_malformed_ai_output_marks_failed(client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai, response=["what", "is", "this"])
    d = client.get(f"/documents/{doc['id']}").get_json()
    assert d["status"] == "failed" and d["errorKind"] == "malformed"


def test_identical_file_reuses_cached_extraction(client, course, fake_ai):
    upload_syllabus(client, course["id"], fake_ai)
    calls = len(fake_ai.prompts)
    r = client.upload(f"/courses/{course['id']}/documents", "file", make_pdf(SYLLABUS_PAGES), "copy.pdf", extra={"docType": "syllabus"})
    d = client.get(f"/documents/{r.get_json()['id']}").get_json()
    assert d["status"] == "needs_review" and d["extractionCachedFrom"]
    assert len(fake_ai.prompts) == calls


def test_prompt_contains_rules_and_pages(client, course, fake_ai):
    upload_syllabus(client, course["id"], fake_ai)
    prompt = fake_ai.prompts[-1]
    assert "[[page 2]]" in prompt and "Never guess deadlines" in prompt and "COMP 330" in prompt


def test_upload_validation(client, course):
    assert client.upload(f"/courses/{course['id']}/documents", "file", b"MZ\x90", "x.pdf").status_code == 400
    assert client.upload(f"/courses/{course['id']}/documents", "file", b"", "x.pdf").status_code == 400
    big = b"%PDF-" + b"0" * (10 * 1024 * 1024 + 10)
    assert client.upload(f"/courses/{course['id']}/documents", "file", big, "big.pdf").status_code == 413
    assert client.upload(f"/courses/{course['id']}/documents", "file", make_pdf(["x"]), "x.pdf",
                         extra={"docType": "bogus"}).status_code == 400
    assert client.upload("/courses/nope/documents", "file", make_pdf(["x"]), "x.pdf").status_code == 404


def test_text_upload_and_stored_only(client, course, fake_ai):
    r = client.upload(f"/courses/{course['id']}/documents", "file", b"Week 3 notes\n\nRegular languages", "notes.md",
                      extra={"docType": "lecture_notes", "extract": "0"})
    doc = r.get_json()
    assert doc["status"] == "stored" and doc["contentKind"] == "text"
    page = client.get(f"/documents/{doc['id']}/pages/1").get_json()
    assert "Regular languages" in page["text"]
    assert fake_ai.prompts == []


def test_file_download_and_delete_keeps_assessments(client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai)
    f = client.get(f"/documents/{doc['id']}/file")
    assert f.status_code == 200 and f.data.startswith(b"%PDF-") and f.headers["Cache-Control"] == "private, no-store"
    d = client.get(f"/documents/{doc['id']}").get_json()
    client.post(f"/documents/{doc['id']}/apply", json={"assessments": [{"key": d["extraction"]["assessments"][0]["key"], "decision": "confirm"}]})
    assert client.delete(f"/documents/{doc['id']}").status_code == 200
    assert client.get(f"/documents/{doc['id']}").status_code == 404
    assert len(client.get(f"/projects?courseId={course['id']}").get_json()["projects"]) == 1


def test_document_isolation(client, other_client, course, fake_ai):
    doc = upload_syllabus(client, course["id"], fake_ai)
    for path in (f"/documents/{doc['id']}", f"/documents/{doc['id']}/file", f"/documents/{doc['id']}/pages/1",
                 f"/courses/{course['id']}/documents", f"/courses/{course['id']}/objectives"):
        assert other_client.get(path).status_code == 404, path
    assert other_client.post(f"/documents/{doc['id']}/apply", json={}).status_code == 404
    assert other_client.post(f"/documents/{doc['id']}/process", json={}).status_code == 404
    assert other_client.delete(f"/documents/{doc['id']}").status_code == 404
    assert other_client.upload(f"/courses/{course['id']}/documents", "file", make_pdf(["x"]), "x.pdf").status_code == 404


def test_course_delete_removes_documents(client, course, fake_ai, db):
    upload_syllabus(client, course["id"], fake_ai)
    client.delete(f"/courses/{course['id']}")
    assert not any(p.startswith("users/alice/documents") for p in db.docs)


def test_assignment_upload_persists_pdf(client, course, fake_ai):
    proj = client.post("/projects", json={"courseId": course["id"], "title": "A1"}).get_json()
    fake_ai.queue([{"title": "Do it", "estimatedMinutes": 30}])
    r = client.upload(f"/projects/{proj['id']}/upload", "pdf", make_pdf(["Assignment one text"]), "a1.pdf").get_json()
    assert r["documentId"]
    assert client.get(f"/projects/{proj['id']}").get_json()["documentId"] == r["documentId"]
    assert client.get(f"/documents/{r['documentId']}/file").status_code == 200


def test_manual_objectives_crud(client, course, other_client):
    proj = client.post("/projects", json={"courseId": course["id"], "title": "Midterm", "kind": "exam"}).get_json()
    assert proj["kind"] == "exam"
    r = client.post(f"/courses/{course['id']}/objectives", json={"text": "Explain DFAs", "assessmentIds": [proj["id"]]})
    assert r.status_code == 201
    oid = r.get_json()["id"]
    assert client.post(f"/courses/{course['id']}/objectives", json={"text": "x", "assessmentIds": ["bogus"]}).status_code == 400
    assert client.patch(f"/objectives/{oid}", json={"text": "Explain NFAs"}).get_json()["text"] == "Explain NFAs"
    assert other_client.patch(f"/objectives/{oid}", json={"text": "pwned"}).status_code == 404
    assert client.delete(f"/objectives/{oid}").status_code == 200


def test_project_assessment_field_validation(client, course):
    proj = client.post("/projects", json={"courseId": course["id"], "title": "Quiz"}).get_json()
    assert client.patch(f"/projects/{proj['id']}", json={"weight": 120}).status_code == 400
    assert client.patch(f"/projects/{proj['id']}", json={"kind": "bogus"}).status_code == 400
    r = client.patch(f"/projects/{proj['id']}", json={"weight": 10, "estimatedHours": 3}).get_json()
    assert r["weight"] == 10 and r["estimatedHoursOrigin"] == "student"
