"""phase 2: workload forecasting, risk detection and schedule recovery."""

from datetime import date, timedelta

import pytest

from services import workload as wl
from services import recovery as rc

MON = date(2026, 10, 12)  # a monday
AV = dict(wl.DEFAULT_AVAILABILITY)  # 09:00–22:00, 240 min/day, sessions ≤ 120


def D(n):
    return MON + timedelta(days=n)


def assess(aid, due, kind="assignment", **kw):
    return {"id": aid, "title": aid, "dueDate": due, "kind": kind, "status": "active", **kw}


def task(tid, pid, est=60, planned=None, **kw):
    return {"id": tid, "projectId": pid, "title": tid, "estimatedMinutes": est, "timeSpent": kw.pop("spent", None),
            "status": kw.pop("status", "pending"), "plannedDate": planned, "dependsOn": kw.pop("deps", []),
            "pinned": kw.pop("pinned", False), "rescheduleCount": kw.pop("moved", 0), "order": kw.pop("order", 0), **kw}


# -----------------------------------------------------------------------------
# availability
# -----------------------------------------------------------------------------
def test_free_minutes_respects_commitments_and_caps():
    assert wl.free_minutes(MON, AV, []) == 240  # capped by daily limit
    busy = [{"day": 0, "startTime": "09:00", "endTime": "15:00"}, {"day": 0, "startTime": "14:00", "endTime": "20:30"}]
    assert wl.free_minutes(MON, AV, busy) == 90  # overlapping blocks merged: 20:30–22:00 free
    assert wl.free_minutes(D(1), AV, busy) == 240  # blocks only apply on mondays
    late = wl.free_minutes(MON, AV, [], today=MON, now_minutes=21 * 60)
    assert late == 60  # only the rest of today's window counts


def test_days_off_and_blackouts():
    av = {**AV, "daysOff": [5, 6], "blackouts": [{"start": D(2).isoformat(), "end": D(3).isoformat(), "label": "trip"}]}
    assert wl.free_minutes(D(5), av, []) == 0
    assert wl.free_minutes(D(2), av, []) == 0 and wl.free_minutes(D(3), av, []) == 0
    assert wl.free_minutes(D(4), av, []) == 240


def test_normalize_availability_validation():
    _, errs = wl.normalize_availability({"dayStart": "25:00", "maxStudyMinutesPerDay": -5, "daysOff": [9],
                                         "blackouts": [{"start": "2026-10-10", "end": "2026-10-01"}]})
    assert len(errs) == 4
    ok, errs = wl.normalize_availability({"dayStart": "08:00", "maxStudyMinutesPerDay": 300})
    assert errs == [] and ok["dayStart"] == "08:00" and ok["dayEnd"] == "22:00"


# -----------------------------------------------------------------------------
# estimates
# -----------------------------------------------------------------------------
def test_calibration_requires_enough_history():
    few = [task(f"t{i}", "p", est=60, spent=120, status="done") for i in range(3)]
    c = wl.estimate_calibration(few)
    assert c["sufficient"] is False and c["factor"] == 1.0
    many = [task(f"t{i}", "p", est=60, spent=90, status="done") for i in range(10)]
    c = wl.estimate_calibration(many + [task("open", "p", est=60, spent=500)])  # open tasks ignored
    assert c["sufficient"] and c["n"] == 10 and c["median"] == 1.5
    assert c["factor"] == pytest.approx(1 + 0.5 * 10 / 15, abs=0.01)  # shrunk toward 1


def test_task_remaining_flags():
    assert wl.task_remaining(task("a", "p", est=None)) == (30, ["missing_estimate"])
    assert wl.task_remaining(task("a", "p", est=60, spent=80)) == (15, ["over_estimate"])
    assert wl.task_remaining(task("a", "p", est=60, spent=20)) == (40, [])
    assert wl.task_remaining(task("a", "p", status="done")) == (0, [])


# -----------------------------------------------------------------------------
# forecast
# -----------------------------------------------------------------------------
def by_id(result):
    return {r["id"]: r for r in result["assessments"]}


def test_insufficient_time_is_at_risk():
    # 10h of work, due in 2 days at 4h/day → 12h free incl. due day … make it 14h
    tasks = [task(f"t{i}", "essay", est=120) for i in range(7)]
    r = by_id(wl.forecast(MON, [assess("essay", D(2))], tasks, AV, []))["essay"]
    assert r["status"] == "at_risk"
    assert r["remainingMinutes"] == 840 and r["availableMinutes"] == 720 and r["unallocatedMinutes"] == 120
    assert any("doesn't fit" in d for d in r["drivers"])


def test_on_track_and_tight_thresholds():
    tasks = [task("a", "p1", est=120)]
    r = by_id(wl.forecast(MON, [assess("p1", D(6))], tasks, AV, []))["p1"]
    assert r["status"] == "on_track"
    tasks = [task(f"a{i}", "p1", est=200) for i in range(5)]  # 1000 of 1200 min → 83%
    r = by_id(wl.forecast(MON, [assess("p1", D(4))], tasks, AV, []))["p1"]
    assert r["status"] == "tight"


def test_overlapping_assessments_compete_for_time():
    a = assess("early", D(2))
    b = assess("late", D(3))
    tasks = [task(f"e{i}", "early", est=120) for i in range(5)] + [task(f"l{i}", "late", est=120) for i in range(4)]
    res = by_id(wl.forecast(MON, [a, b], tasks, AV, []))
    assert res["early"]["status"] == "tight"           # 600 of 720 free through D2
    assert res["late"]["status"] == "at_risk"          # 1080 needed by D3, only 960 free
    assert res["late"]["competingMinutes"] == 600 and res["late"]["unallocatedMinutes"] == 120
    # alone, the later assessment would have been fine
    alone = by_id(wl.forecast(MON, [b], [t for t in tasks if t["projectId"] == "late"], AV, []))
    assert alone["late"]["status"] == "on_track"


def test_fixed_commitments_reduce_capacity():
    tasks = [task(f"t{i}", "p", est=120) for i in range(5)]  # 600 min
    free = by_id(wl.forecast(MON, [assess("p", D(3))], tasks, AV, []))["p"]
    busy = [{"day": d, "startTime": "09:00", "endTime": "20:00"} for d in range(7)]  # 2h free a day
    tight = by_id(wl.forecast(MON, [assess("p", D(3))], tasks, AV, busy))["p"]
    assert free["status"] == "on_track" and tight["status"] == "at_risk"
    assert tight["availableMinutes"] == 4 * 120


def test_exams_must_be_prepared_the_day_before():
    tasks = [task("study", "mid", est=240)]
    r = by_id(wl.forecast(MON, [assess("mid", D(1), kind="exam")], tasks, AV, []))["mid"]
    assert r["lastWorkDay"] == MON.isoformat() and r["availableMinutes"] == 240


def test_missing_information_statuses():
    res = by_id(wl.forecast(MON, [
        assess("nodate", None),
        assess("noest", D(5)),                       # no tasks, no expected workload
        assess("mostly_missing", D(5)),
        assess("workload", D(5), estimatedHours=6),  # no tasks but expected workload
    ], [task("m1", "mostly_missing", est=None), task("m2", "mostly_missing", est=None), task("m3", "mostly_missing", est=60)], AV, []))
    assert res["nodate"]["status"] == "insufficient_information"
    assert res["noest"]["status"] == "insufficient_information"
    assert res["mostly_missing"]["status"] == "insufficient_information"
    assert res["workload"]["status"] == "on_track" and res["workload"]["demand"]["basis"] == "expected_workload"


def test_done_and_past_due():
    res = by_id(wl.forecast(MON, [assess("old", D(-2)), assess("fin", D(3), status="completed"),
                                  assess("alldone", D(3))],
                            [task("x", "alldone", status="done")], AV, []))
    assert res["old"]["status"] == "past_due"
    assert res["fin"]["status"] == "done" and res["alldone"]["status"] == "done"


def test_forecast_reacts_to_deadline_and_availability_changes():
    tasks = [task(f"t{i}", "p", est=120) for i in range(6)]  # 12h
    assert by_id(wl.forecast(MON, [assess("p", D(1))], tasks, AV, []))["p"]["status"] == "at_risk"   # 8h free
    assert by_id(wl.forecast(MON, [assess("p", D(2))], tasks, AV, []))["p"]["status"] == "tight"     # exactly 12h
    assert by_id(wl.forecast(MON, [assess("p", D(6))], tasks, AV, []))["p"]["status"] == "on_track"
    more = {**AV, "maxStudyMinutesPerDay": 480}
    assert by_id(wl.forecast(MON, [assess("p", D(1))], tasks, more, []))["p"]["status"] == "on_track"


def test_calibration_only_applied_when_opted_in():
    cal = {"sufficient": True, "factor": 1.5, "n": 10, "median": 1.8}
    tasks = [task("t", "p", est=120)]
    off = by_id(wl.forecast(MON, [assess("p", D(5))], tasks, AV, [], calibration=cal))["p"]
    on = by_id(wl.forecast(MON, [assess("p", D(5))], tasks, {**AV, "useCalibration": True}, [], calibration=cal))["p"]
    assert off["remainingMinutes"] == 120 and on["remainingMinutes"] == 180


def test_risk_detection():
    tasks = [
        task("missed", "p", est=60, planned=D(-1)),
        task("over1", "p", est=200, planned=D(1)), task("over2", "p", est=200, planned=D(1)),
        task("stuck", "p", est=30, planned=D(3), moved=4),
    ]
    cal = {"sufficient": True, "factor": 1.4, "n": 8, "median": 1.6}
    res = wl.forecast(MON, [assess("p", D(10))], tasks, AV, [], calibration=cal)
    types = {r["type"] for r in res["risks"]}
    assert {"missed", "overloaded_day", "repeatedly_moved", "unrealistic_assumption"} <= types
    over = next(r for r in res["risks"] if r["type"] == "overloaded_day")
    assert D(1).isoformat() in over["related"]
    assert all(r["explanation"] and r["fingerprint"] for r in res["risks"])
    assert res["definitions"]["at_risk"].startswith("The remaining work does not fit")


def test_crunch_detection():
    res = wl.forecast(MON, [assess(f"a{i}", D(3 + i), estimatedHours=1) for i in range(3)], [], AV,
                      [{"day": 0, "startTime": "09:00", "endTime": "10:00"}])
    assert any(r["type"] == "crunch" for r in res["risks"])


# -----------------------------------------------------------------------------
# recovery
# -----------------------------------------------------------------------------
def caps(n=10, minutes=240, start=MON):
    return {start + timedelta(days=i): minutes for i in range(n)}


def test_missed_session_is_moved_forward():
    tasks = [task("missed", "p", est=60, planned=D(-2)), task("future", "p", est=60, planned=D(3))]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps())
    assert [m["taskId"] for m in plan["moves"]] == ["missed"]
    m = plan["moves"][0]
    assert m["from"] == D(-2).isoformat() and m["to"] == MON.isoformat() and "missed" in m["reason"]
    assert plan["keptInPlace"] == 1


def test_completed_and_pinned_tasks_never_move():
    tasks = [task("done", "p", est=60, planned=D(-3), status="done"),
             task("pinned", "p", est=240, planned=D(0), pinned=True),
             task("missed", "p", est=60, planned=D(-1))]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps())
    moved = {m["taskId"] for m in plan["moves"]}
    assert "done" not in moved and "pinned" not in moved
    assert plan["moves"][0]["to"] == D(1).isoformat()  # monday is full from the pinned task
    assert "done" not in plan["basis"]


def test_impossible_schedule_is_reported_not_pushed_past_deadline():
    tasks = [task(f"t{i}", "p", est=120, planned=D(-1)) for i in range(5)]  # 10h, due tomorrow (2 days of 4h)
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(1))], caps())
    assert plan["unplaceable"]
    assert sum(u["minutesShort"] for u in plan["unplaceable"]) == 120
    assert all(m["to"] <= D(1).isoformat() for m in plan["moves"])
    assert plan["interventions"] and "extension" in " ".join(plan["interventions"])


def test_dependencies_are_respected():
    tasks = [task("draft", "p", est=200, planned=D(-1), order=1, deps=["outline"]),
             task("outline", "p", est=200, planned=D(-1), order=0)]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps(), max_session_minutes=240)
    to = {m["taskId"]: m["to"] for m in plan["moves"]}
    # draft is listed first but must wait for its outline; monday only has room for one of them
    assert to["outline"] == MON.isoformat() and to["draft"] == D(1).isoformat()


def test_long_tasks_are_split_across_days():
    tasks = [task("big", "p", est=300, planned=D(-1))]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps(), max_session_minutes=120)
    assert not plan["moves"] and len(plan["splits"]) == 1
    parts = plan["splits"][0]["parts"]
    assert sum(p["minutes"] for p in parts) == 300 and all(30 <= p["minutes"] <= 120 for p in parts)


def test_repeatedly_moved_tasks_are_unselected_with_advice():
    tasks = [task("stuck", "p", est=60, planned=D(-1), moved=3)]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps())
    m = plan["moves"][0]
    assert m["selected"] is False and "consider splitting" in m["warning"]


def test_dependency_loop_is_reported():
    tasks = [task("a", "p", planned=D(-1), deps=["b"]), task("b", "p", planned=D(-1), deps=["a"])]
    plan = rc.plan_recovery(MON, tasks, [assess("p", D(7))], caps())
    assert {u["taskId"] for u in plan["unplaceable"]} == {"a", "b"}


# -----------------------------------------------------------------------------
# api
# -----------------------------------------------------------------------------
TODAY = date.today()


def iso(n):
    return (TODAY + timedelta(days=n)).isoformat()


@pytest.fixture
def setup(client):
    course = client.post("/courses", json={"name": "MATH 141", "color": "#f00"}).get_json()
    proj = client.post("/projects", json={"courseId": course["id"], "title": "PS3", "dueDate": iso(5)}).get_json()
    t1 = client.post("/tasks", json={"projectId": proj["id"], "title": "Q1", "estimatedMinutes": 60, "dueDate": iso(-2)}).get_json()
    t2 = client.post("/tasks", json={"projectId": proj["id"], "title": "Q2", "estimatedMinutes": 60, "dueDate": iso(2)}).get_json()
    t3 = client.post("/tasks", json={"projectId": proj["id"], "title": "Q0", "estimatedMinutes": 30, "dueDate": iso(-3)}).get_json()
    client.patch(f"/tasks/{t3['id']}", json={"status": "done"})
    client.post("/schedule", json={"blocks": [{"day": 0, "startTime": "10:00", "endTime": "12:00", "label": "class"}]})
    return {"course": course, "proj": proj, "t1": t1, "t2": t2, "t3": t3}


def test_forecast_endpoint(client, setup):
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    row = next(a for a in r["assessments"] if a["id"] == setup["proj"]["id"])
    assert row["status"] == "on_track" and row["remainingMinutes"] == 120
    assert any(x["type"] == "missed" for x in r["risks"])
    assert r["definitions"] and r["assumptions"]
    # the forecast updates when the deadline moves
    client.patch(f"/projects/{setup['proj']['id']}", json={"dueDate": iso(-1)})
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert next(a for a in r["assessments"] if a["id"] == setup["proj"]["id"])["status"] == "past_due"


def test_forecast_reacts_to_availability_api(client, setup):
    client.put("/planning/availability", json={"maxStudyMinutesPerDay": 0})
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert next(a for a in r["assessments"] if a["id"] == setup["proj"]["id"])["status"] == "at_risk"
    assert client.put("/planning/availability", json={"dayEnd": "05:00"}).status_code == 400
    assert client.get("/planning/availability").get_json()["maxStudyMinutesPerDay"] == 0


def test_risk_dismissal_resurfaces_when_inputs_change(client, setup):
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    missed = next(x for x in r["risks"] if x["type"] == "missed")
    client.post("/planning/acks", json={"riskId": missed["id"], "state": "dismissed", "fingerprint": missed["fingerprint"]})
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert next(x for x in r["risks"] if x["type"] == "missed")["ack"] == "dismissed"
    # a second missed task changes the fingerprint → the risk shows again
    client.post("/tasks", json={"projectId": setup["proj"]["id"], "title": "Q4", "estimatedMinutes": 30, "dueDate": iso(-1)})
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert next(x for x in r["risks"] if x["type"] == "missed")["ack"] is None


def test_recovery_preview_apply_preserves_data(client, setup, db):
    before_schedule = client.get("/schedule").get_json()
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    assert [m["taskId"] for m in plan["moves"]] == [setup["t1"]["id"]]
    assert setup["t3"]["id"] not in plan["basis"]  # completed task is not part of the plan
    r = client.post("/planning/recovery/apply", json={
        "today": TODAY.isoformat(), "basis": plan["basis"],
        "moves": [{"taskId": m["taskId"], "to": m["to"]} for m in plan["moves"]], "splits": []})
    assert r.status_code == 200, r.get_json()
    tasks = {t["id"]: t for t in client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"]}
    assert len(tasks) == 3  # nothing deleted
    moved = tasks[setup["t1"]["id"]]
    assert moved["dueDate"][:10] >= TODAY.isoformat() and moved["rescheduleCount"] == 1
    assert tasks[setup["t3"]["id"]]["status"] == "done" and tasks[setup["t3"]["id"]]["dueDate"][:10] == iso(-3)
    assert tasks[setup["t2"]["id"]]["dueDate"][:10] == iso(2)
    assert client.get("/schedule").get_json() == before_schedule


def test_recovery_rejects_stale_or_completed(client, setup):
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    moves = [{"taskId": m["taskId"], "to": m["to"]} for m in plan["moves"]]
    client.patch(f"/tasks/{setup['t1']['id']}", json={"estimatedMinutes": 90})
    r = client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": plan["basis"], "moves": moves})
    assert r.status_code == 409
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    client.patch(f"/tasks/{setup['t1']['id']}", json={"status": "done"})
    r = client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": plan["basis"],
                                                     "moves": [{"taskId": setup["t1"]["id"], "to": iso(1)}]})
    assert r.status_code == 409
    assert client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": {}, "moves": [
        {"taskId": setup["t2"]["id"], "to": iso(-5)}]}).status_code == 409


def test_recovery_rejects_past_dates(client, setup):
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    r = client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": plan["basis"],
                                                     "moves": [{"taskId": setup["t1"]["id"], "to": iso(-1)}]})
    assert r.status_code == 400


def test_split_apply_and_undo(client, setup):
    big = client.post("/tasks", json={"projectId": setup["proj"]["id"], "title": "Write-up", "estimatedMinutes": 300, "dueDate": iso(-1)}).get_json()
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    split = next(s for s in plan["splits"] if s["taskId"] == big["id"])
    r = client.post("/planning/recovery/apply", json={
        "today": TODAY.isoformat(), "basis": plan["basis"],
        "moves": [{"taskId": m["taskId"], "to": m["to"]} for m in plan["moves"]],
        "splits": [{"taskId": split["taskId"], "parts": split["parts"]}]})
    assert r.status_code == 200, r.get_json()
    tasks = client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"]
    parts = [t for t in tasks if t["title"].startswith("Write-up")]
    assert len(parts) == len(split["parts"])
    assert sum(t["estimatedMinutes"] for t in parts) == 300
    u = client.post("/planning/recovery/undo", json={})
    assert u.status_code == 200
    tasks = client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"]
    restored = [t for t in tasks if t["title"].startswith("Write-up")]
    assert len(restored) == 1 and restored[0]["estimatedMinutes"] == 300 and restored[0]["dueDate"][:10] == iso(-1)
    t1 = next(t for t in tasks if t["id"] == setup["t1"]["id"])
    assert t1["dueDate"][:10] == iso(-2) and t1["rescheduleCount"] == 0
    assert client.post("/planning/recovery/undo", json={}).status_code == 404


def test_task_dependencies_validation(client, setup):
    t1, t2 = setup["t1"], setup["t2"]
    assert client.patch(f"/tasks/{t2['id']}", json={"dependsOn": [t1["id"]]}).status_code == 200
    assert client.patch(f"/tasks/{t1['id']}", json={"dependsOn": [t2["id"]]}).status_code == 400  # loop
    assert client.patch(f"/tasks/{t1['id']}", json={"dependsOn": [t1["id"]]}).status_code == 400
    other = client.post("/projects", json={"courseId": setup["course"]["id"], "title": "Other"}).get_json()
    ot = client.post("/tasks", json={"projectId": other["id"], "title": "x"}).get_json()
    assert client.patch(f"/tasks/{t1['id']}", json={"dependsOn": [ot["id"]]}).status_code == 400
    assert client.patch(f"/tasks/{t1['id']}", json={"pinned": True}).get_json()["pinned"] is True


def test_planning_isolation(client, other_client, setup):
    r = other_client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    assert r["assessments"] == []
    plan = client.post("/planning/recovery/preview", json={"today": TODAY.isoformat()}).get_json()
    r = other_client.post("/planning/recovery/apply", json={"today": TODAY.isoformat(), "basis": plan["basis"],
                                                           "moves": [{"taskId": setup["t1"]["id"], "to": iso(1)}]})
    assert r.status_code == 409  # alice's task doesn't exist in mallory's account
    assert client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"][0]["dueDate"]


def test_auto_schedule_skips_pinned_tasks(client, setup):
    client.patch(f"/tasks/{setup['t2']['id']}", json={"pinned": True})
    r = client.post(f"/projects/{setup['proj']['id']}/auto-schedule", json={}).get_json()
    assert setup["t2"]["id"] not in [t["id"] for t in r["tasks"]]
    t2 = next(t for t in client.get(f"/tasks?projectId={setup['proj']['id']}").get_json()["tasks"] if t["id"] == setup["t2"]["id"])
    assert t2["dueDate"][:10] == iso(2)


def test_acks_work_for_ids_with_special_characters(client, setup):
    r = client.get(f"/planning/forecast?today={TODAY.isoformat()}").get_json()
    rid = "at_risk:" + setup["proj"]["id"] + "-x.y/z"
    assert client.post("/planning/acks", json={"riskId": rid, "state": "dismissed", "fingerprint": "f"}).status_code == 200
    assert client.post("/planning/acks", json={"riskId": rid, "state": None}).status_code == 200
