"""phase 4: the feedback loop — study sessions, outcomes, reviews, recommendations."""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from services import feedback as fb

TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def cid():
    return uuid.uuid4().hex[:16]


# -----------------------------------------------------------------------------
# pure: reviews
# -----------------------------------------------------------------------------
def t(est, spent, status="done", **kw):
    return {"estimatedMinutes": est, "timeSpent": spent, "status": status, "title": kw.pop("title", "task"), **kw}


def test_review_compares_estimates_and_actuals():
    due = date(2026, 10, 20)
    tasks = [t(60, 90, completedAt=datetime(2026, 10, 15, tzinfo=timezone.utc)), t(60, 80), t(30, 45), t(60, None, status="pending")]
    hist = [{"d": "2026-10-12", "s": "on_track"}, {"d": "2026-10-14", "s": "at_risk"}, {"d": "2026-10-18", "s": "tight"}]
    r = fb.assessment_review({"id": "p", "title": "PS", "dueDate": due, "status": "completed",
                              "completedAt": datetime(2026, 10, 19, tzinfo=timezone.utc)},
                             tasks, [], hist, {}, [], date(2026, 10, 25))
    assert r["time"]["estimatedMinutes"] == 210 and r["time"]["actualMinutes"] == 215
    assert r["time"]["medianRatio"] == 1.5 and r["time"]["comparedTasks"] == 3
    assert r["forecast"]["daysAtRisk"] == 1 and r["forecast"]["firstAtRisk"] == "2026-10-14"
    assert r["forecast"]["completedOnTime"] is True
    assert any("1.5× their estimates" in o for o in r["observations"])
    assert any(s["type"] == "estimates" for s in r["suggestions"])
    assert all(s["caveat"] == fb.SINGLE_OBSERVATION for s in r["suggestions"])


def test_review_with_sparse_data_makes_no_claims():
    r = fb.assessment_review({"id": "p", "title": "Quiz", "dueDate": date(2026, 10, 20), "status": "active"},
                             [t(30, 20)], [], [], {}, [], date(2026, 10, 25))
    assert r["time"]["medianRatio"] is None
    assert r["suggestions"] == []
    assert any("not enough to judge" in o for o in r["observations"])
    assert any("No forecast history" in o for o in r["observations"])


def test_review_learning_evidence_uses_attempts_before_due():
    due = date(2026, 10, 20)
    before = datetime(2026, 10, 15, tzinfo=timezone.utc)
    after = datetime(2026, 10, 22, tzinfo=timezone.utc)
    atts = {"c1": [{"questionId": f"q{i}", "correct": False, "gradingMethod": "exact", "createdAt": before} for i in range(3)]
            + [{"questionId": "q9", "correct": True, "gradingMethod": "exact", "createdAt": after}]}
    r = fb.assessment_review({"id": "p", "title": "Midterm", "dueDate": due, "status": "completed",
                              "outcome": {"percent": 64.0}}, [], [], [], atts, [{"id": "c1", "name": "Pumping lemma"}], date(2026, 10, 25))
    row = r["learning"][0]
    assert row["statusAtDue"] == "needs_practice" and row["attemptsBefore"] == 3 and row["attemptsAfter"] == 1
    assert any("64%" in o for o in r["observations"])
    assert any(s["type"] == "practice_weak" for s in r["suggestions"])
    assert "disagree" in r["caveat"]
    # no causal language anywhere
    text = " ".join(r["observations"] + [s["text"] for s in r["suggestions"]]).lower()
    assert "because" not in text and "caused" not in text


# -----------------------------------------------------------------------------
# pure: recommendations
# -----------------------------------------------------------------------------
def test_recommendations_need_enough_data():
    few = [t(60, 120, courseId="c") for _ in range(3)]
    assert fb.recommendations(TODAY, few, {"c": "X"}, {"useCalibration": False}, [], {}, {}) == []


def test_calibration_recommendation():
    many = [t(60, 100, courseId="c") for _ in range(8)]
    recs = fb.recommendations(TODAY, many, {"c": "X"}, {"useCalibration": False}, [], {}, {})
    r = next(r for r in recs if r["id"] == "rec:calibration")
    assert r["action"] == {"type": "enable_calibration"} and "8 finished tasks" in r["evidence"]


def test_missed_plan_recommendation_lowers_daily_limit():
    planned = [t(60, None, status="pending", plannedDate=TODAY - timedelta(days=i % 10 + 1)) for i in range(10)]
    planned += [t(60, 50, status="done", plannedDate=TODAY - timedelta(days=2), completedAt=datetime.combine(TODAY - timedelta(days=2), datetime.min.time()))]
    recs = fb.recommendations(TODAY, planned, {}, {"maxStudyMinutesPerDay": 240}, [], {}, {})
    r = next(r for r in recs if r["id"] == "rec:daily_limit")
    assert r["action"]["type"] == "set_daily_limit" and r["action"]["minutes"] < 240
    assert "10 of 11" in r["evidence"]


def test_diagnostic_and_practice_recommendations():
    due = TODAY + timedelta(days=6)
    upcoming = [{"conceptId": "c1", "name": "DFAs", "assessmentId": "mid", "assessmentTitle": "Midterm", "dueDate": due}]
    recs = fb.recommendations(TODAY, [], {}, {}, upcoming, {"c1": TODAY - timedelta(days=20)},
                              {"mid": ("Midterm", due, ["Pumping lemma"])})
    ids = {r["id"] for r in recs}
    assert {"rec:diagnose:mid", "rec:practice:mid"} <= ids
    fresh = fb.recommendations(TODAY, [], {}, {}, upcoming, {"c1": TODAY - timedelta(days=1)}, {})
    assert fresh == []


def test_weekly_study():
    sessions = [{"actualMinutes": 50, "endedAt": datetime.combine(TODAY, datetime.min.time(), tzinfo=timezone.utc)}]
    tasks = [{"estimatedMinutes": 90, "plannedDate": TODAY}]
    rows = fb.weekly_study(sessions, tasks, TODAY, weeks=3)
    assert len(rows) == 3 and rows[-1]["loggedMinutes"] == 50 and rows[-1]["plannedMinutes"] == 90


# -----------------------------------------------------------------------------
# api
# -----------------------------------------------------------------------------
@pytest.fixture
def setup(client):
    course = client.post("/courses", json={"name": "MATH 141", "color": "#0a0"}).get_json()
    proj = client.post("/projects", json={"courseId": course["id"], "title": "Quiz 2", "dueDate": iso(-1), "kind": "quiz"}).get_json()
    task = client.post("/tasks", json={"projectId": proj["id"], "title": "Review ch 4", "estimatedMinutes": 60, "dueDate": iso(-3)}).get_json()
    return {"course": course, "proj": proj, "task": task}


def test_study_session_updates_task_and_is_idempotent(client, setup):
    key = cid()
    body = {"clientId": key, "taskId": setup["task"]["id"], "plannedMinutes": 25, "actualMinutes": 30,
            "completedTask": True, "confidence": 4, "note": "chain rule was hard"}
    r = client.post("/study-sessions", json=body)
    assert r.status_code == 201, r.get_json()
    assert r.get_json()["task"]["timeSpent"] == 30 and r.get_json()["task"]["status"] == "done"
    again = client.post("/study-sessions", json=body)
    assert again.status_code == 200 and again.get_json()["duplicate"] is True
    task = client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"][0]
    assert task["timeSpent"] == 30  # not double counted
    client.post("/study-sessions", json={"clientId": cid(), "taskId": setup["task"]["id"], "actualMinutes": 15})
    task = client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"][0]
    assert task["timeSpent"] == 45
    sessions = client.get(f"/study-sessions?projectId={setup['proj']['id']}").get_json()["sessions"]
    assert len(sessions) == 2


def test_study_session_minimal_and_validation(client, setup):
    # logging time against an assessment without picking a task (e.g. exam prep) — every extra field optional
    assert client.post("/study-sessions", json={"clientId": cid(), "projectId": setup["proj"]["id"], "actualMinutes": 40}).status_code == 201
    bad = [
        {"projectId": setup["proj"]["id"], "actualMinutes": 40},                               # no clientId
        {"clientId": cid(), "projectId": setup["proj"]["id"], "actualMinutes": 0},
        {"clientId": cid(), "projectId": setup["proj"]["id"], "actualMinutes": 40, "confidence": 9},
        {"clientId": cid(), "actualMinutes": 40},
        {"clientId": cid(), "projectId": setup["proj"]["id"], "actualMinutes": 40, "endedAt": "2999-01-01T00:00:00"},
    ]
    for b in bad:
        assert client.post("/study-sessions", json=b).status_code == 400, b
    assert client.post("/study-sessions", json={"clientId": cid(), "taskId": "nope", "actualMinutes": 5}).status_code == 404


def test_outcome_optional_and_grade_copy(client, setup):
    pid = setup["proj"]["id"]
    review = client.get(f"/projects/{pid}/review?today={TODAY.isoformat()}").get_json()
    assert review["outcome"] is None  # reviews work without a result
    cats = [{"id": "quiz", "name": "Quizzes", "weight": 20}, {"id": "final", "name": "Final", "weight": 80}]
    client.post(f"/courses/{setup['course']['id']}/grading", json={"categories": cats})
    r = client.put(f"/projects/{pid}/outcome", json={"score": 17, "total": 20, "reflection": {"feeling": "as_expected", "note": "rushed q5"},
                                                      "addToGrades": {"categoryId": "quiz"}})
    assert r.status_code == 200 and r.get_json()["outcome"]["percent"] == 85.0 and r.get_json()["status"] == "completed"
    grades = client.get(f"/courses/{setup['course']['id']}/grading").get_json()["grades"]
    assert len(grades) == 1 and grades[0]["score"] == 17
    # re-entering replaces, doesn't duplicate the grade
    client.put(f"/projects/{pid}/outcome", json={"score": 18, "total": 20, "addToGrades": {"categoryId": "quiz"}})
    assert len(client.get(f"/courses/{setup['course']['id']}/grading").get_json()["grades"]) == 1
    for b in ({"score": 5, "total": 0}, {"score": "a", "total": 10}, {"score": 50, "total": 10},
              {"score": 5, "total": 10, "reflection": {"feeling": "awful"}}, {"score": 5, "total": 10, "addToGrades": {"categoryId": "nope"}}):
        assert client.put(f"/projects/{pid}/outcome", json=b).status_code == 400, b
    assert client.delete(f"/projects/{pid}/outcome").status_code == 200
    assert client.get(f"/projects/{pid}").get_json()["outcome"] is None


def test_review_endpoint_combines_plan_study_and_forecast(client, setup):
    pid = setup["proj"]["id"]
    client.post("/study-sessions", json={"clientId": cid(), "taskId": setup["task"]["id"], "actualMinutes": 80, "completedTask": True})
    r = client.get(f"/projects/{pid}/review?today={TODAY.isoformat()}").get_json()
    assert r["time"]["estimatedMinutes"] == 60 and r["time"]["actualMinutes"] == 80
    assert r["prep"]["firstWorkDay"] == TODAY.isoformat()
    assert r["observations"] and r["caveat"]


def test_insights_sparse_and_actionable(client, setup):
    r = client.get(f"/insights?today={TODAY.isoformat()}").get_json()
    assert r["recommendations"] == [] or all(x["evidence"] for x in r["recommendations"])
    assert len(r["weekly"]) == 6 and r["calibration"]["sufficient"] is False
    assert r["pastAssessments"][0]["id"] == setup["proj"]["id"]
    # build enough history for a calibration recommendation
    for i in range(6):
        tk = client.post("/tasks", json={"projectId": setup["proj"]["id"], "title": f"t{i}", "estimatedMinutes": 30, "dueDate": iso(-2)}).get_json()
        client.post("/study-sessions", json={"clientId": cid(), "taskId": tk["id"], "actualMinutes": 60, "completedTask": True})
    r = client.get(f"/insights?today={TODAY.isoformat()}").get_json()
    rec = next(x for x in r["recommendations"] if x["id"] == "rec:calibration")
    assert r["weekly"][-1]["loggedMinutes"] == 360
    # dismiss it (shared ack store) → reported as dismissed until the evidence changes
    client.post("/planning/acks", json={"riskId": rec["id"], "state": "dismissed", "fingerprint": rec["fingerprint"]})
    r = client.get(f"/insights?today={TODAY.isoformat()}").get_json()
    assert next(x for x in r["recommendations"] if x["id"] == "rec:calibration")["ack"] == "dismissed"
    # accepting = turning calibration on; then the recommendation disappears
    client.put("/planning/availability", json={"useCalibration": True})
    r = client.get(f"/insights?today={TODAY.isoformat()}").get_json()
    assert not any(x["id"] == "rec:calibration" for x in r["recommendations"])


def test_feedback_isolation(client, other_client, setup):
    pid = setup["proj"]["id"]
    assert other_client.post("/study-sessions", json={"clientId": cid(), "taskId": setup["task"]["id"], "actualMinutes": 10}).status_code == 404
    assert other_client.post("/study-sessions", json={"clientId": cid(), "projectId": pid, "actualMinutes": 10}).status_code == 404
    assert other_client.put(f"/projects/{pid}/outcome", json={"score": 1, "total": 2}).status_code == 404
    assert other_client.get(f"/projects/{pid}/review").status_code == 404
    client.post("/study-sessions", json={"clientId": cid(), "projectId": pid, "actualMinutes": 10})
    assert other_client.get(f"/study-sessions?projectId={pid}").get_json()["sessions"] == []
    assert other_client.get("/insights").get_json()["pastAssessments"] == []


def test_session_follow_up_details(client, setup, other_client):
    key = cid()
    client.post("/study-sessions", json={"clientId": key, "taskId": setup["task"]["id"], "actualMinutes": 25, "plannedMinutes": 25})
    r = client.patch(f"/study-sessions/{key}", json={"confidence": 2, "note": "  need to redo q3 ", "completedTask": True})
    assert r.status_code == 200 and r.get_json()["note"] == "need to redo q3" and r.get_json()["completedTask"] is True
    task = client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"][0]
    assert task["status"] == "done" and task["timeSpent"] == 25
    assert client.patch(f"/study-sessions/{key}", json={"confidence": 7}).status_code == 400
    assert client.patch(f"/study-sessions/{key}", json={}).status_code == 400
    assert other_client.patch(f"/study-sessions/{key}", json={"note": "x"}).status_code == 404


def test_review_wording_for_work_logged_after_due():
    after = datetime(2026, 10, 22, tzinfo=timezone.utc)
    r = fb.assessment_review({"id": "p", "title": "Quiz", "dueDate": date(2026, 10, 20), "status": "completed"},
                             [t(30, 25, completedAt=after)], [], [], {}, [], date(2026, 10, 25))
    assert r["prep"]["daysBeforeDue"] == -2
    assert any("after the due date" in o for o in r["observations"])
    assert not any("-2 days" in o for o in r["observations"])


def test_overview_pending(client, setup, other_client, fake_ai):
    from tests.conftest import make_pdf
    fake_ai.queue({"assessments": [{"title": "Final", "kind": "exam"}]})
    client.upload(f"/courses/{setup['course']['id']}/documents", "file", make_pdf(["Final exam TBA"]), "s.pdf", extra={"docType": "syllabus"})
    client.post("/study-sessions", json={"clientId": cid(), "projectId": setup["proj"]["id"], "actualMinutes": 45})
    r = client.get("/overview/pending").get_json()
    assert r["documents"][0]["status"] == "needs_review" and r["loggedThisWeekMinutes"] == 45
    assert other_client.get("/overview/pending").get_json() == {"documents": [], "sessions": [], "loggedThisWeekMinutes": 0}
