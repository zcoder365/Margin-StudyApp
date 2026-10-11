"""critical end-to-end workflow across all phases, through the http api only.

syllabus → review/confirm → forecast → missed session → recovery → concepts →
diagnostic → practice scheduled → study session → result → review → insights
"""

import uuid
from datetime import date, timedelta

from tests.conftest import make_pdf

TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


def test_full_student_loop(client, fake_ai):
    course = client.post("/courses", json={"name": "COMP 330", "color": "#abc"}).get_json()
    cid = course["id"]

    # 1. syllabus → review → correct a date → confirm
    pages = [f"Problem set 1 is due {iso(4)}. Midterm exam on {iso(9)} (30%). Students learn to design finite automata."]
    fake_ai.queue({
        "assessments": [
            {"title": "Problem set 1", "kind": "assignment", "dueDate": iso(5), "dueDateText": iso(4), "weightPercent": 10,
             "quote": f"Problem set 1 is due {iso(4)}.", "page": 1},
            {"title": "Midterm exam", "kind": "exam", "dueDate": iso(9), "weightPercent": 30, "estimatedHours": 6,
             "estimatedHoursIsGuess": True, "quote": f"Midterm exam on {iso(9)} (30%).", "page": 1},
        ],
        "learningObjectives": [{"text": "Design finite automata", "quote": "design finite automata", "page": 1,
                                "relatedAssessments": ["Midterm exam"]}],
    })
    doc = client.upload(f"/courses/{cid}/documents", "file", make_pdf(pages), "syllabus.pdf", extra={"docType": "syllabus"}).get_json()
    ex = client.get(f"/documents/{doc['id']}").get_json()["extraction"]
    keys = {a["fields"]["title"]: a["key"] for a in ex["assessments"]}
    r = client.post(f"/documents/{doc['id']}/apply", json={
        "assessments": [{"key": keys["Problem set 1"], "decision": "confirm", "fields": {"dueDate": iso(4)}},
                        {"key": keys["Midterm exam"], "decision": "confirm"}],
        "objectives": [{"key": ex["objectives"][0]["key"], "decision": "confirm"}]})
    assert r.status_code == 200
    projects = {p["title"]: p for p in client.get(f"/projects?courseId={cid}").get_json()["projects"]}
    ps1, mid = projects["Problem set 1"], projects["Midterm exam"]
    assert ps1["dueDate"].startswith(iso(4))

    # 2. tasks, one of them missed → forecast + recovery
    t1 = client.post("/tasks", json={"projectId": ps1["id"], "title": "Q1-Q3", "estimatedMinutes": 90, "dueDate": iso(-1)}).get_json()
    client.post("/tasks", json={"projectId": ps1["id"], "title": "Q4", "estimatedMinutes": 60, "dueDate": iso(1)})
    f = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert {a["title"] for a in f["assessments"]} == {"Problem set 1", "Midterm exam"}
    assert any(r["type"] == "missed" for r in f["risks"])
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    move = next(m for m in plan["moves"] if m["taskId"] == t1["id"])
    assert client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": plan["basis"],
                                                        "moves": [{"taskId": move["taskId"], "to": move["to"]}]}).status_code == 200
    f = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert not any(r["type"] == "missed" for r in f["risks"])

    # 3. concepts → diagnostic → practice scheduled
    fake_ai.queue({"concepts": [{"name": "DFA design", "description": "Build DFAs", "objectiveRefs": ["O0"]}]})
    client.post(f"/courses/{cid}/concepts/suggest", json={})
    sug = client.get(f"/courses/{cid}/concepts/suggestions").get_json()["suggestions"]
    client.post(f"/courses/{cid}/concepts", json={"concepts": [{**sug[0], "fromSuggestion": 0}]})
    concept = client.get(f"/courses/{cid}/concepts").get_json()["concepts"][0]
    assert concept["assessments"][0]["id"] == mid["id"]
    fake_ai.queue({"questions": [
        {"type": "numeric", "conceptIds": [concept["id"]], "prompt": "Minimal DFA states for 'contains 11'?", "answer": 3,
         "tolerance": 0, "explanation": "start, saw 1, saw 11", "difficulty": 2},
        {"type": "mcq", "conceptIds": [concept["id"]], "prompt": "A DFA has exactly one … per symbol per state",
         "choices": ["transition", "accepting state", "stack"], "answerIndex": 0, "explanation": "Deterministic.", "difficulty": 1},
    ]})
    sid = client.post("/diagnostics/sessions", json={"courseId": cid, "assessmentId": mid["id"], "size": 2}).get_json()["id"]
    items = client.get(f"/diagnostics/sessions/{sid}").get_json()["items"]
    for it in items:
        q = it["question"]
        resp = "4" if q["type"] == "numeric" else 0
        client.post(f"/diagnostics/sessions/{sid}/answer", json={"questionId": q["id"], "response": resp, "confidence": 2})
    done = client.post(f"/diagnostics/sessions/{sid}/complete", json={"today": TODAY.isoformat()}).get_json()
    assert done["summary"]["answered"] == 2
    prev = client.post("/diagnostics/practice/preview", json={"courseId": cid, "today": TODAY.isoformat()}).get_json()
    item = prev["proposed"][0]
    client.post("/diagnostics/practice/apply", json={"items": [{k: item[k] for k in ("conceptId", "assessmentId", "date", "minutes")}],
                                                     "today": TODAY.isoformat()})
    practice = [t for t in client.get(f"/tasks?projectId={mid['id']}").get_json()["tasks"] if t.get("kind") == "practice"]
    assert len(practice) == 1

    # 4. study → result → review → insights
    client.post("/study-sessions", json={"clientId": uuid.uuid4().hex, "taskId": t1["id"], "actualMinutes": 120, "completedTask": True})
    r = client.put(f"/projects/{ps1['id']}/outcome", json={"score": 8, "total": 10, "reflection": {"feeling": "as_expected"}})
    assert r.get_json()["status"] == "completed"
    review = client.get(f"/projects/{ps1['id']}/review?today={TODAY.isoformat()}").get_json()
    assert review["time"]["actualMinutes"] == 120 and review["outcome"]["percent"] == 80.0
    assert review["forecast"]["snapshots"] >= 1
    ins = client.get(f"/insights?today={TODAY.isoformat()}").get_json()
    assert ins["weekly"][-1]["loggedMinutes"] == 120
    assert not any(p["id"] == ps1["id"] for p in ins["pastAssessments"])  # only assessments whose due date has passed
    home = client.get("/overview/pending").get_json()
    assert home["loggedThisWeekMinutes"] == 120
