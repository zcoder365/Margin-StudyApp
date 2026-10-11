"""phase 3: diagnostic practice — grading, question validation, mastery, practice planning, api flow."""

from datetime import date, datetime, timedelta, timezone

import pytest

from ai import AIError
from services import diagnostics as dx
from tests.conftest import make_pdf

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# deterministic answer checking
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("expr,value", [
    ("3/4", 0.75), ("0.75", 0.75), ("sqrt(9)/4", 0.75), ("2^3", 8), ("1,000", 1000), ("50%", 0.5),
    ("-2 + 5", 3), ("pi", 3.14159265), ("ln(e)", 1), ("1.5e3", 1500), (" 7 ", 7), ("2×3", 6),
])
def test_safe_eval_accepts_math(expr, value):
    assert dx.safe_eval(expr) == pytest.approx(value, rel=1e-6)


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')", "a + b", "2**999999", "1/0", "", "[1,2]", "sqrt(-1)", "open('f')", "x" * 200,
])
def test_safe_eval_rejects_everything_else(expr):
    with pytest.raises(dx.NumericParseError):
        dx.safe_eval(expr)


def test_grade_numeric_tolerance():
    assert dx.grade_numeric("0.333", 1 / 3, abs_tol=0.001)[0] is True
    assert dx.grade_numeric("0.3", 1 / 3, abs_tol=0.001)[0] is False
    assert dx.grade_numeric("1/3", 1 / 3, abs_tol=0)[0] is True
    assert dx.grade_numeric("101", 100)[0] is True       # default 1% relative
    assert dx.grade_numeric("103", 100)[0] is False


# -----------------------------------------------------------------------------
# question validation
# -----------------------------------------------------------------------------
ALLOWED = {"c1", "c2"}
PAGES = {0: {"id": "doc1", "filename": "notes.pdf", "pages": ["A DFA accepts a string if it ends in an accepting state."]}}


def mcq(**kw):
    q = {"type": "mcq", "conceptIds": ["c1"], "prompt": "Which state must a DFA end in to accept?",
         "choices": ["start", "accepting", "trap"], "answerIndex": 1, "explanation": "By definition.", "difficulty": 1}
    q.update(kw)
    return q


def test_validate_valid_questions():
    q, why = dx.validate_question(mcq(quote="ends in an accepting state", docRef="D0", page=1), ALLOWED, PAGES)
    assert why is None and q["grounding"] == "source" and q["source"]["page"] == 1
    q, _ = dx.validate_question(mcq(quote="something the notes never said", docRef="D0"), ALLOWED, PAGES)
    assert q["grounding"] == "generated" and q["source"] is None
    num, why = dx.validate_question({"type": "numeric", "conceptIds": ["c2"], "prompt": "How many states does the minimal DFA have?",
                                     "answer": "4", "tolerance": 0, "explanation": "Merge equivalent states."}, ALLOWED)
    assert why is None and num["answer"] == 4.0
    short, why = dx.validate_question({"type": "short", "conceptIds": ["c1", "nope"], "prompt": "Explain the pumping lemma.",
                                       "modelAnswer": "Long strings repeat a state.", "keyPoints": ["pigeonhole"],
                                       "explanation": "See notes."}, ALLOWED)
    assert why is None and short["conceptIds"] == ["c1"]


@pytest.mark.parametrize("bad,reason", [
    (mcq(type="essay"), "unknown question type"),
    (mcq(prompt="short"), "prompt"),
    (mcq(explanation=""), "explanation"),
    (mcq(conceptIds=["zzz"]), "concept"),
    (mcq(answerIndex=7), "answerIndex"),
    (mcq(choices=["a", "a", "b"]), "distinct"),
    (mcq(choices=["only", "two"]), "3–6"),
    (mcq(prompt="This will be on the exam: which state accepts?"), "exam"),
    ({"type": "numeric", "conceptIds": ["c1"], "prompt": "Compute the number please", "answer": "many", "explanation": "x"}, "number"),
    ({"type": "numeric", "conceptIds": ["c1"], "prompt": "Compute the number please", "answer": 10, "tolerance": 5, "explanation": "x"}, "loose"),
    ({"type": "short", "conceptIds": ["c1"], "prompt": "Explain the idea please", "explanation": "x"}, "model answer"),
    ("not even a dict", "object"),
])
def test_validate_rejects_malformed(bad, reason):
    q, why = dx.validate_question(bad, ALLOWED, PAGES)
    assert q is None and reason in why


def test_public_question_hides_answers():
    q = {**mcq(), "id": "q1", "status": "active", "grounding": "generated"}
    hidden = dx.public_question(q)
    assert "answerIndex" not in hidden and "explanation" not in hidden
    assert dx.public_question(q, reveal=True)["answerIndex"] == 1


# -----------------------------------------------------------------------------
# mastery
# -----------------------------------------------------------------------------
def att(qid, correct, days_ago=1, confidence=None, difficulty=2, **kw):
    return {"questionId": qid, "correct": correct, "gradingMethod": kw.pop("method", "exact"), "confidence": confidence,
            "difficulty": difficulty, "createdAt": NOW - timedelta(days=days_ago), "conceptIds": ["c1"], **kw}


def test_single_correct_answer_is_not_mastery():
    m = dx.mastery([att("q1", True)], now=NOW)
    assert m["status"] == "insufficient_evidence" and "1 answer" in m["reasons"][0]


def test_repeating_one_question_is_not_enough_evidence():
    m = dx.mastery([att("q1", True, 3), att("q1", True, 2), att("q1", True, 1)], now=NOW)
    assert m["status"] == "insufficient_evidence"


def test_demonstrated_needs_varied_recent_correct_answers():
    m = dx.mastery([att("q1", True, 5), att("q2", True, 3), att("q3", True, 1)], now=NOW)
    assert m["status"] == "demonstrated"
    assert m["evidence"]["attempts"] == 3 and m["evidence"]["distinctQuestions"] == 3


def test_confidence_is_not_mastery():
    # always "sure", mostly wrong → needs practice, flagged as a likely misconception
    m = dx.mastery([att("q1", False, 3, confidence=3), att("q2", True, 2, confidence=3), att("q3", False, 1, confidence=3)], now=NOW)
    assert m["status"] == "needs_practice"
    assert any("misconception" in r for r in m["reasons"])
    assert m["evidence"]["confidentErrors"] == 2


def test_recency_weighting_and_repeated_attempts():
    old_wrong = [att(f"o{i}", False, 90) for i in range(4)]
    recent_right = [att("q1", True, 2), att("q2", True, 1), att("q3", True, 0)]
    assert dx.mastery(old_wrong + recent_right, now=NOW)["status"] == "demonstrated"
    recent_wrong = [att("q1", False, 2), att("q2", False, 1)]
    assert dx.mastery([att("q0", True, 30)] + recent_wrong, now=NOW)["status"] == "needs_practice"


def test_flagged_questions_and_self_grading():
    attempts = [att("bad", False, 1), att("q1", True, 3), att("q2", True, 2),
                att("q3", None, 1, method="self", selfGrade="partial")]
    m = dx.mastery(attempts, now=NOW, excluded_questions={"bad"})
    assert m["evidence"]["attempts"] == 3 and m["evidence"]["selfGraded"] == 1
    assert m["status"] == "developing"  # latest answer only partially right
    ungraded = dx.mastery([att("q1", None, 1, method="self", selfGrade=None)], now=NOW)
    assert ungraded["evidence"]["attempts"] == 0


def test_error_patterns():
    qs = [{"id": "q1", "type": "mcq", "prompt": "Pick the regular language", "choices": ["a", "b", "c"], "conceptIds": ["c1"]}]
    cs = [{"id": "c1", "name": "Regular languages"}]
    attempts = [att("q1", False, 3, confidence=3, response=2), att("q1", False, 1, confidence=3, response=2)]
    types = {p["type"] for p in dx.error_patterns(attempts, qs, cs)}
    assert types == {"confident_errors", "repeat_miss", "sticky_distractor"}


def test_practice_priorities_and_allocation():
    today = date(2026, 10, 12)
    concepts = [{"id": "weak", "name": "Weak"}, {"id": "unknown", "name": "Unknown"}, {"id": "strong", "name": "Strong"}]
    mastery = {"weak": {"status": "needs_practice"}, "unknown": {"status": "insufficient_evidence"}, "strong": {"status": "demonstrated"}}
    soon = {"id": "mid", "title": "Midterm", "dueDate": today + timedelta(days=5), "weight": 30}
    later = {"id": "fin", "title": "Final", "dueDate": today + timedelta(days=40), "weight": 40}
    items = dx.practice_priorities(mastery, concepts, {"weak": [later], "unknown": [soon], "strong": [soon]}, today)
    assert [i["conceptId"] for i in items] == ["unknown", "weak"]  # strong is skipped; proximity outweighs
    caps = {today + timedelta(days=i): 60 for i in range(5)}
    load = {today: 50}
    proposed, unallocated = dx.allocate_practice(items, caps, load, {"mid": today + timedelta(days=4), "fin": today + timedelta(days=39)})
    assert [p["date"] for p in proposed] == [(today + timedelta(days=1)).isoformat()] * 1 + [(today + timedelta(days=2)).isoformat()]
    assert unallocated == []


# -----------------------------------------------------------------------------
# api
# -----------------------------------------------------------------------------
TODAY = date.today()


@pytest.fixture
def course_setup(client, fake_ai):
    course = client.post("/courses", json={"name": "COMP 330", "color": "#abc"}).get_json()
    mid = client.post("/projects", json={"courseId": course["id"], "title": "Midterm", "kind": "exam",
                                         "dueDate": (TODAY + timedelta(days=6)).isoformat(), "weight": 30}).get_json()
    obj = client.post(f"/courses/{course['id']}/objectives", json={"text": "Design DFAs", "assessmentIds": [mid["id"]]}).get_json()
    client.upload(f"/courses/{course['id']}/documents", "file",
                  make_pdf(["A DFA accepts a string if it ends in an accepting state. The pumping lemma shows languages are not regular."]),
                  "notes.pdf", extra={"docType": "lecture_notes", "extract": "0"})
    return {"course": course, "mid": mid, "obj": obj}


def accept_concepts(client, fake_ai, course_id):
    fake_ai.queue({"concepts": [
        {"name": "DFA acceptance", "description": "When a DFA accepts", "objectiveRefs": ["O0"],
         "quote": "ends in an accepting state", "docRef": "D0", "page": 1},
        {"name": "Pumping lemma", "description": "Proving non-regularity", "objectiveRefs": []},
        {"name": "DFA acceptance", "description": "duplicate"},
        {"description": "no name"},
    ]})
    assert client.post(f"/courses/{course_id}/concepts/suggest", json={}).status_code == 202
    sug = client.get(f"/courses/{course_id}/concepts/suggestions").get_json()
    assert sug["status"] == "ready" and len(sug["suggestions"]) == 2
    assert sug["suggestions"][0]["sourceRefs"][0]["verified"] is True
    payload = [{"name": s["name"], "description": s["description"], "objectiveIds": s["objectiveIds"], "fromSuggestion": i}
               for i, s in enumerate(sug["suggestions"])]
    r = client.post(f"/courses/{course_id}/concepts", json={"concepts": payload})
    assert r.status_code == 201
    return client.get(f"/courses/{course_id}/concepts").get_json()["concepts"]


def generated_questions(c1, c2):
    return {"questions": [
        {"type": "mcq", "conceptIds": [c1], "prompt": "Which state must a DFA end in to accept a string?",
         "choices": ["start", "accepting", "dead"], "answerIndex": 1, "explanation": "Acceptance is defined by the final state.",
         "difficulty": 1, "quote": "ends in an accepting state", "docRef": "D0", "page": 1},
        {"type": "numeric", "conceptIds": [c1], "prompt": "A DFA over {0,1} tracking parity of 1s needs how many states?",
         "answer": 2, "tolerance": 0, "explanation": "Even and odd.", "difficulty": 2},
        {"type": "short", "conceptIds": [c2], "prompt": "Explain how the pumping lemma proves a language isn't regular.",
         "modelAnswer": "Assume regular, pick a long string, show pumping breaks it.", "keyPoints": ["contradiction"],
         "explanation": "Proof by contradiction.", "difficulty": 3},
        {"type": "mcq", "conceptIds": [c2], "prompt": "Broken question with one choice only?", "choices": ["x"], "answerIndex": 0, "explanation": "x"},
    ]}


def test_concept_suggestions_require_material(client):
    course = client.post("/courses", json={"name": "Empty", "color": "#000"}).get_json()
    assert client.post(f"/courses/{course['id']}/concepts/suggest", json={}).status_code == 400


def test_concept_suggestion_failure_is_reported(client, fake_ai, course_setup):
    fake_ai.queue(AIError("the AI provider timed out", kind="timeout"))
    client.post(f"/courses/{course_setup['course']['id']}/concepts/suggest", json={})
    sug = client.get(f"/courses/{course_setup['course']['id']}/concepts/suggestions").get_json()
    assert sug["status"] == "failed" and "timed out" in sug["error"]


def test_full_diagnostic_flow(client, fake_ai, course_setup, db):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    by_name = {c["name"]: c for c in concepts}
    c1, c2 = by_name["DFA acceptance"]["id"], by_name["Pumping lemma"]["id"]
    assert by_name["DFA acceptance"]["mastery"]["status"] == "insufficient_evidence"
    assert by_name["DFA acceptance"]["assessments"][0]["id"] == course_setup["mid"]["id"]

    fake_ai.queue(generated_questions(c1, c2))
    r = client.post("/diagnostics/sessions", json={"courseId": cid, "size": 4})
    assert r.status_code == 202
    sid = r.get_json()["id"]
    s = client.get(f"/diagnostics/sessions/{sid}").get_json()
    assert s["status"] == "ready" and len(s["items"]) == 3
    assert s["generation"]["dropped"] == 1 and s["warnings"]
    for it in s["items"]:  # nothing leaks before answering
        assert "answerIndex" not in it["question"] and "answer" not in it["question"] and "explanation" not in it["question"]
    items = {it["question"]["type"]: it["question"] for it in s["items"]}
    assert items["mcq"]["grounding"] == "source" and items["numeric"]["grounding"] == "generated"

    r = client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": items["mcq"]["id"], "response": 1, "confidence": 3}).get_json()
    assert r["correct"] is True and r["question"]["explanation"]
    assert client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": items["mcq"]["id"], "response": 0}).status_code == 409
    assert client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": items["numeric"]["id"], "response": "two"}).status_code == 400
    r = client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": items["numeric"]["id"], "response": "3", "confidence": 3}).get_json()
    assert r["correct"] is False and r["question"]["answer"] == 2
    r = client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": items["short"]["id"], "response": "it repeats"}).get_json()
    assert r["correct"] is None and r["question"]["modelAnswer"]
    assert client.post(f"/diagnostics/sessions/{sid}/self-grade", json={"questionId": items["short"]["id"], "result": "partial"}).status_code == 200

    done = client.post(f"/diagnostics/sessions/{sid}/complete", json={"today": TODAY.isoformat()}).get_json()
    assert done["summary"]["answered"] == 3
    per = {c["name"]: c for c in done["summary"]["concepts"]}
    assert per["DFA acceptance"]["answered"] == 2 and per["DFA acceptance"]["masteryAfter"] == "insufficient_evidence"
    assert done["practicePlan"] and done["practicePlan"][0]["assessmentId"] == course_setup["mid"]["id"]

    dash = client.get(f"/courses/{cid}/diagnostics?today={TODAY.isoformat()}").get_json()
    assert len(dash["groups"]["insufficient_evidence"]) == 2
    assert dash["recentSessions"][0]["status"] == "completed"
    assert dash["recentMistakes"][0]["questionId"] == items["numeric"]["id"]
    review = client.get(f"/diagnostics/mistakes/{items['numeric']['id']}").get_json()
    assert review["question"]["answer"] == 2 and review["attempts"][0]["response"] == "3"

    # flagging a question withdraws it and its answers stop counting
    assert client.post(f"/questions/{items['numeric']['id']}/flag", json={"reason": "incorrect", "note": "answer is wrong"}).status_code == 200
    concepts = {c["name"]: c for c in client.get(f"/courses/{cid}/concepts").get_json()["concepts"]}
    assert concepts["DFA acceptance"]["mastery"]["evidence"]["attempts"] == 1
    assert client.post(f"/questions/{items['mcq']['id']}/flag", json={"reason": "because"}).status_code == 400


def test_second_session_reuses_questions_and_survives_ai_failure(client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    c1, c2 = concepts[0]["id"], concepts[1]["id"]
    fake_ai.queue(generated_questions(c1, c2))
    client.post("/diagnostics/sessions", json={"courseId": cid, "size": 3})
    fake_ai.queue(AIError("rate limited", kind="rate_limited"))
    sid = client.post("/diagnostics/sessions", json={"courseId": cid, "size": 3}).get_json()["id"]
    s = client.get(f"/diagnostics/sessions/{sid}").get_json()
    assert s["status"] == "ready" and len(s["items"]) == 3  # unanswered questions are reused, no new AI needed


def test_fallback_when_no_questions_can_be_generated(client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    accept_concepts(client, fake_ai, cid)
    fake_ai.queue(AIError("the AI provider is not configured", kind="unavailable"))
    sid = client.post("/diagnostics/sessions", json={"courseId": cid, "size": 2}).get_json()["id"]
    s = client.get(f"/diagnostics/sessions/{sid}").get_json()
    assert s["status"] == "ready" and all(it["question"]["grounding"] == "fallback" for it in s["items"])
    assert any("self-check" in w for w in s["warnings"])


def test_session_for_assessment_uses_linked_concepts(client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    linked = next(c for c in concepts if c["name"] == "DFA acceptance")
    fake_ai.queue(generated_questions(linked["id"], "unused"))
    sid = client.post("/diagnostics/sessions", json={"courseId": cid, "assessmentId": course_setup["mid"]["id"], "size": 2}).get_json()["id"]
    s = client.get(f"/diagnostics/sessions/{sid}").get_json()
    assert s["conceptIds"] == [linked["id"]]
    assert all(linked["id"] in it["question"]["conceptIds"] for it in s["items"])


def test_correcting_concept_associations(client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    pump = next(c for c in concepts if c["name"] == "Pumping lemma")
    r = client.patch(f"/concepts/{pump['id']}", json={"objectiveIds": [course_setup["obj"]["id"]], "name": "Pumping lemma proofs"})
    assert r.status_code == 200 and r.get_json()["objectiveIds"] == [course_setup["obj"]["id"]]
    assert client.patch(f"/concepts/{pump['id']}", json={"objectiveIds": ["not-real"]}).status_code == 400
    fake_ai.queue(generated_questions(concepts[0]["id"], pump["id"]))
    client.post("/diagnostics/sessions", json={"courseId": cid, "size": 3})
    assert client.delete(f"/concepts/{pump['id']}").status_code == 200
    names = [c["name"] for c in client.get(f"/courses/{cid}/concepts").get_json()["concepts"]]
    assert names == ["DFA acceptance"]


def test_practice_scheduling(client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    accept_concepts(client, fake_ai, cid)
    prev = client.post("/diagnostics/practice/preview", json={"courseId": cid, "today": TODAY.isoformat()}).get_json()
    assert prev["proposed"] and all(p["assessmentId"] == course_setup["mid"]["id"] for p in prev["proposed"])
    assert all(p["date"] < course_setup["mid"]["dueDate"][:10] for p in prev["proposed"])
    items = [{k: p[k] for k in ("conceptId", "assessmentId", "date", "minutes")} for p in prev["proposed"]]
    r = client.post("/diagnostics/practice/apply", json={"items": items, "today": TODAY.isoformat()})
    assert r.status_code == 201
    client.post("/diagnostics/practice/apply", json={"items": items, "today": TODAY.isoformat()})  # idempotent
    tasks = client.get(f"/tasks?projectId={course_setup['mid']['id']}").get_json()["tasks"]
    practice = [t for t in tasks if t.get("kind") == "practice"]
    assert len(practice) == len(items) and practice[0]["title"].startswith("Practice: ")
    assert client.post("/diagnostics/practice/apply", json={"items": [{**items[0], "date": "2000-01-01"}], "today": TODAY.isoformat()}).status_code == 400


def test_diagnostics_isolation(client, other_client, fake_ai, course_setup):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    fake_ai.queue(generated_questions(concepts[0]["id"], concepts[1]["id"]))
    sid = client.post("/diagnostics/sessions", json={"courseId": cid, "size": 2}).get_json()["id"]
    qid = client.get(f"/diagnostics/sessions/{sid}").get_json()["items"][0]["question"]["id"]
    assert other_client.get(f"/diagnostics/sessions/{sid}").status_code == 404
    assert other_client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": qid, "response": 1}).status_code == 404
    assert other_client.post(f"/questions/{qid}/flag", json={"reason": "incorrect"}).status_code == 404
    assert other_client.get(f"/courses/{cid}/concepts").status_code == 404
    assert other_client.get(f"/courses/{cid}/diagnostics").status_code == 404
    assert other_client.get(f"/diagnostics/mistakes/{qid}").status_code == 404
    assert other_client.post("/diagnostics/sessions", json={"courseId": cid}).status_code == 404
    assert other_client.patch(f"/concepts/{concepts[0]['id']}", json={"name": "x"}).status_code == 404


def test_course_delete_removes_diagnostics(client, fake_ai, course_setup, db):
    cid = course_setup["course"]["id"]
    concepts = accept_concepts(client, fake_ai, cid)
    fake_ai.queue(generated_questions(concepts[0]["id"], concepts[1]["id"]))
    client.post("/diagnostics/sessions", json={"courseId": cid, "size": 2})
    client.delete(f"/courses/{cid}")
    leftovers = [p for p in db.docs if p.startswith("users/alice/") and any(
        f"/{c}/" in p for c in ("concepts", "questions", "diagnosticSessions", "attempts", "conceptSuggestions", "objectives", "documents"))]
    assert leftovers == []
