"""regression tests for the pre-existing api (terms, courses, assignments, tasks, grades, schedule)."""

from datetime import date, timedelta

from tests.conftest import make_pdf


def make_course(client, name="COMP 330"):
    r = client.post("/courses", json={"name": name, "color": "#a8d5ba"})
    assert r.status_code == 201
    return r.get_json()


def make_project(client, course_id, title="PS1", due=None):
    r = client.post("/projects", json={"courseId": course_id, "title": title, "dueDate": due})
    assert r.status_code == 201
    return r.get_json()


def test_health(app):
    assert app.test_client().get("/api/health").get_json()["status"] == "ok"


def test_auth_required(app):
    c = app.test_client()
    assert c.get("/api/courses").status_code == 401
    assert c.get("/api/courses", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_user_init_creates_default_term(client):
    r = client.get("/terms").get_json()
    assert len(r["terms"]) == 1
    assert r["currentTermId"] == r["terms"][0]["id"]
    again = client.post("/users/init", json={}).get_json()
    assert again["isNewUser"] is False


def test_preferences_roundtrip(client):
    r = client.patch("/me", json={"weekStartDay": 0, "darkMode": True, "workIntervalMinutes": 50})
    assert r.status_code == 200
    me = client.get("/me").get_json()
    assert me["weekStartDay"] == 0 and me["darkMode"] is True and me["workIntervalMinutes"] == 50


def test_course_project_task_crud(client):
    course = make_course(client)
    proj = make_project(client, course["id"], due="2026-11-01")
    assert proj["dueDate"].startswith("2026-11-01")

    t = client.post("/tasks", json={"projectId": proj["id"], "title": "read ch 1", "estimatedMinutes": 45}).get_json()
    assert t["status"] == "pending" and t["order"] == 0

    upd = client.patch(f"/tasks/{t['id']}", json={"status": "done", "timeSpent": 50}).get_json()
    assert upd["status"] == "done" and upd["timeSpent"] == 50

    bad = client.patch(f"/tasks/{t['id']}", json={"estimatedMinutes": -3})
    assert bad.status_code == 400

    tasks = client.get(f"/tasks?projectId={proj['id']}").get_json()["tasks"]
    assert [x["id"] for x in tasks] == [t["id"]]

    cal = client.get("/calendar").get_json()["tasks"]
    assert cal[0]["courseName"] == "COMP 330"

    assert client.delete(f"/projects/{proj['id']}").status_code == 200
    assert client.get(f"/tasks?projectId={proj['id']}").get_json()["tasks"] == []


def test_reschedule_counter(client):
    course = make_course(client)
    proj = make_project(client, course["id"])
    t = client.post("/tasks", json={"projectId": proj["id"], "title": "x"}).get_json()
    client.patch(f"/tasks/{t['id']}", json={"scheduledDate": "2026-10-01"})
    r = client.patch(f"/tasks/{t['id']}", json={"scheduledDate": "2026-10-03"}).get_json()
    assert r["rescheduleCount"] == 1


def test_course_delete_cascades(client, db):
    course = make_course(client)
    proj = make_project(client, course["id"])
    client.post("/tasks", json={"projectId": proj["id"], "title": "x"})
    client.delete(f"/courses/{course['id']}")
    assert db.collection_dicts("users/alice/projects") == {}
    assert db.collection_dicts("users/alice/tasks") == {}


def test_term_delete_cascades(client, db):
    terms = client.get("/terms").get_json()
    course = make_course(client)
    make_project(client, course["id"])
    client.delete(f"/terms/{terms['currentTermId']}")
    assert db.collection_dicts("users/alice/courses") == {}
    assert db.collection_dicts("users/alice/projects") == {}


def test_grades(client):
    course = make_course(client)
    cats = [{"id": "c1", "name": "HW", "weight": 40}, {"id": "c2", "name": "Final", "weight": 60}]
    assert client.post(f"/courses/{course['id']}/grading", json={"categories": cats}).status_code == 200
    g = client.post(f"/courses/{course['id']}/grades", json={"categoryId": "c1", "name": "HW1", "score": 8, "total": 10})
    assert g.status_code == 201
    bad = client.post(f"/courses/{course['id']}/grades", json={"categoryId": "c1", "name": "HW2", "score": "abc", "total": 10})
    assert bad.status_code == 400
    data = client.get(f"/courses/{course['id']}/grading").get_json()
    assert len(data["grades"]) == 1 and data["categories"][1]["weight"] == 60


def test_schedule_roundtrip(client):
    blocks = [{"day": 0, "startTime": "09:00", "endTime": "11:00", "label": "class"}]
    assert client.post("/schedule", json={"blocks": blocks}).status_code == 200
    assert client.get("/schedule").get_json()["blocks"] == blocks
    assert client.post("/schedule", json={"blocks": [{"day": 0}]}).status_code == 400


def test_auto_schedule_assigns_days_before_due(client):
    course = make_course(client)
    due = (date.today() + timedelta(days=5)).isoformat()
    proj = make_project(client, course["id"], due=due)
    for i in range(3):
        client.post("/tasks", json={"projectId": proj["id"], "title": f"t{i}", "estimatedMinutes": 120})
    r = client.post(f"/projects/{proj['id']}/auto-schedule", json={}).get_json()
    assert len(r["tasks"]) == 3
    assert all(t["dueDate"] <= due for t in r["tasks"])
    assert all(t["status"] == "scheduled" for t in r["tasks"])


def test_upload_pdf_generates_tasks(client, fake_ai):
    course = make_course(client)
    proj = make_project(client, course["id"])
    fake_ai.queue([{"title": "Read the brief", "estimatedMinutes": 20}, {"title": "Write it", "estimatedMinutes": 90}])
    pdf = make_pdf(["Assignment 1\nWrite an essay about automata."])
    r = client.upload(f"/projects/{proj['id']}/upload", "pdf", pdf, "a1.pdf")
    assert r.status_code == 201, r.get_json()
    body = r.get_json()
    assert "automata" in body["extractedText"]
    assert [t["title"] for t in body["tasks"]] == ["Read the brief", "Write it"]
    assert body["totalEstimatedMinutes"] == 110


def test_upload_ai_failure_keeps_existing_tasks(client, fake_ai):
    """pre-existing bug: a failed ai call used to delete the previous ai tasks."""
    from ai import AIError
    course = make_course(client)
    proj = make_project(client, course["id"])
    fake_ai.queue([{"title": "Old task", "estimatedMinutes": 20}])
    pdf = make_pdf(["Assignment text"])
    client.upload(f"/projects/{proj['id']}/upload", "pdf", pdf, "a.pdf")
    fake_ai.queue(AIError("boom"))
    r = client.upload(f"/projects/{proj['id']}/upload", "pdf", pdf, "a.pdf").get_json()
    assert r["warning"]
    assert [t["title"] for t in r["tasks"]] == ["Old task"]


def test_upload_rejects_non_pdf(client):
    course = make_course(client)
    proj = make_project(client, course["id"])
    r = client.upload(f"/projects/{proj['id']}/upload", "pdf", b"hello", "a.pdf")
    assert r.status_code == 400


def test_user_isolation(client, other_client):
    course = make_course(client)
    proj = make_project(client, course["id"])
    t = client.post("/tasks", json={"projectId": proj["id"], "title": "secret"}).get_json()
    # mallory can't see or touch alice's data, even with the ids
    assert other_client.get("/courses?termId=all").get_json()["courses"] == []
    assert other_client.get(f"/tasks?projectId={proj['id']}").get_json()["tasks"] == []
    assert other_client.patch(f"/tasks/{t['id']}", json={"title": "pwned"}).status_code == 404
    assert other_client.patch(f"/projects/{proj['id']}", json={"title": "pwned"}).status_code == 404
    assert other_client.post("/tasks", json={"projectId": proj["id"], "title": "x"}).status_code == 404
    assert other_client.get(f"/courses/{course['id']}/grading").status_code == 404
