"""
planning api: availability settings, workload forecast, risk acknowledgements,
and schedule recovery (preview → apply → undo).

firestore (under users/{uid}):
  planning/availability   study window, daily limits, days off, blackouts, calibration opt-in
  planning/acks           {riskId: {state, fingerprint, at}} — dismissed/acknowledged risks
  planning/history        daily forecast snapshots per assessment (for later reviews)
  planning/lastRecovery   snapshot of the last applied recovery, for undo

tasks keep the existing meaning of `dueDate` = the day the task is planned for.
"""

import hashlib
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g
from firebase_admin import firestore

from core import (
    get_db, user_doc, require_auth, bad_request, stream_dicts, to_jsonable,
    as_date, date_to_storage, parse_client_today,
)
from services import workload as wl
from services import recovery as rc

bp = Blueprint("planning", __name__)

HISTORY_LIMIT = 60


def _planning(uid, name):
    return user_doc(uid).collection("planning").document(name)


def ack_key(risk_id):
    """risk ids contain ':' etc., which aren't valid firestore field paths — store under a hash."""
    return "a_" + hashlib.sha1(risk_id.encode()).hexdigest()[:20]


def load_acks(uid):
    snap = _planning(uid, "acks").get()
    return (snap.to_dict() or {}) if snap.exists else {}


def ack_state(acks, item):
    a = acks.get(ack_key(item["id"]))
    # a dismissal only holds while the underlying numbers are unchanged
    return a.get("state") if a and a.get("fingerprint") == item["fingerprint"] else None


def _now_minutes(raw):
    try:
        v = int(raw)
        return v if 0 <= v <= 24 * 60 else None
    except (TypeError, ValueError):
        return None


def _term_id(uid, raw):
    if raw:
        return raw
    snap = user_doc(uid).get()
    return (snap.to_dict() or {}).get("currentTermId") if snap.exists else None


def load_inputs(uid, term_id):
    """everything the forecast/recovery needs for one term, as plain dicts."""
    courses = {c["id"]: c for c in stream_dicts(user_doc(uid).collection("courses").where("termId", "==", term_id))} if term_id else {}
    assessments, tasks = [], []
    for cid, course in courses.items():
        for p in stream_dicts(user_doc(uid).collection("projects").where("courseId", "==", cid)):
            assessments.append({
                "id": p["id"], "title": p.get("title", ""), "courseId": cid, "courseName": course.get("name", ""),
                "courseColor": course.get("color"), "dueDate": as_date(p.get("dueDate")), "kind": p.get("kind"),
                "status": p.get("status", "active"), "weight": p.get("weight"), "estimatedHours": p.get("estimatedHours"),
            })
        for t in stream_dicts(user_doc(uid).collection("tasks").where("courseId", "==", cid)):
            tasks.append(task_view(t))
    project_ids = {a["id"] for a in assessments}
    tasks = [t for t in tasks if t.get("projectId") in project_ids]

    sched = user_doc(uid).collection("schedule").document("weekly").get()
    busy = (sched.to_dict() or {}).get("blocks", []) if sched.exists else []
    av_snap = _planning(uid, "availability").get()
    availability, _ = wl.normalize_availability(av_snap.to_dict() if av_snap.exists else {})
    return {"courses": courses, "assessments": assessments, "tasks": tasks, "busy": busy, "availability": availability}


def task_view(t):
    return {
        "id": t["id"], "projectId": t.get("projectId"), "courseId": t.get("courseId"), "title": t.get("title", ""),
        "estimatedMinutes": t.get("estimatedMinutes"), "timeSpent": t.get("timeSpent"), "status": t.get("status"),
        "plannedDate": as_date(t.get("dueDate")), "dependsOn": t.get("dependsOn") or [], "pinned": bool(t.get("pinned")),
        "rescheduleCount": t.get("rescheduleCount") or 0, "order": t.get("order") or 0, "kind": t.get("kind"),
    }


def all_task_history(uid):
    """every task the student has (all terms) — used only for estimate calibration."""
    return stream_dicts(user_doc(uid).collection("tasks"))


def logged_minutes_by_assessment(uid, assessment_ids):
    """study minutes logged against assessments without tasks (from study sessions)."""
    out = {}
    for s in stream_dicts(user_doc(uid).collection("studySessions")):
        pid = s.get("projectId")
        if pid in assessment_ids and not s.get("taskId"):
            out[pid] = out.get(pid, 0) + (s.get("actualMinutes") or 0)
    return out


def compute_forecast(uid, term_id, today, now_minutes=None):
    inputs = load_inputs(uid, term_id)
    calibration = wl.estimate_calibration(all_task_history(uid))
    logged = logged_minutes_by_assessment(uid, {a["id"] for a in inputs["assessments"]})
    result = wl.forecast(today, inputs["assessments"], inputs["tasks"], inputs["availability"], inputs["busy"],
                         calibration=calibration, now_minutes=now_minutes, logged_by_assessment=logged)
    acks = load_acks(uid)
    for r in result["risks"]:
        r["ack"] = ack_state(acks, r)
    return result, inputs


def record_history(uid, today, rows):
    """keep one forecast snapshot per assessment per day (used by assessment reviews)."""
    ref = _planning(uid, "history")
    snap = ref.get()
    data = (snap.to_dict() or {}) if snap.exists else {}
    if data.get("lastDate") == today.isoformat():
        return
    by_project = data.get("byProject", {})
    for r in rows:
        if r["status"] in ("done",) and not by_project.get(r["id"]):
            continue
        entry = {"d": today.isoformat(), "s": r["status"], "rem": r["remainingMinutes"],
                 "avail": r.get("availableMinutes")}
        hist = by_project.get(r["id"], [])
        hist.append(entry)
        by_project[r["id"]] = hist[-HISTORY_LIMIT:]
    ref.set({"lastDate": today.isoformat(), "byProject": by_project})


@bp.route("/api/planning/forecast", methods=["GET"])
@require_auth
def get_forecast():
    uid = g.user_id
    term_id = _term_id(uid, request.args.get("termId"))
    today = parse_client_today(request.args.get("today"))
    if not term_id:
        return jsonify({"assessments": [], "days": [], "risks": [], "summary": {}, "definitions": wl.STATUS_DEFINITIONS})
    result, _ = compute_forecast(uid, term_id, today, _now_minutes(request.args.get("nowMinutes")))
    try:
        record_history(uid, today, result["assessments"])
    except Exception:
        pass  # history is best-effort and must never break the forecast
    return jsonify(to_jsonable(result))


@bp.route("/api/planning/availability", methods=["GET"])
@require_auth
def get_availability():
    snap = _planning(g.user_id, "availability").get()
    availability, _ = wl.normalize_availability(snap.to_dict() if snap.exists else {})
    return jsonify(availability)


@bp.route("/api/planning/availability", methods=["PUT"])
@require_auth
def put_availability():
    body = request.get_json(silent=True) or {}
    snap = _planning(g.user_id, "availability").get()
    current = snap.to_dict() if snap.exists else {}
    availability, errors = wl.normalize_availability({**current, **body})
    if errors:
        return jsonify({"error": "; ".join(errors)}), 400
    _planning(g.user_id, "availability").set(availability)
    return jsonify(availability)


@bp.route("/api/planning/acks", methods=["POST"])
@require_auth
def ack_risk():
    body = request.get_json(silent=True) or {}
    risk_id, state, fp = body.get("riskId"), body.get("state"), body.get("fingerprint")
    if not isinstance(risk_id, str) or not risk_id or len(risk_id) > 200:
        return bad_request("riskId is required")
    if state not in ("dismissed", "acknowledged", None):
        return bad_request("state must be dismissed, acknowledged or null")
    key = ack_key(risk_id)
    ref = _planning(g.user_id, "acks")
    if not ref.get().exists:
        ref.set({})
    if state is None:
        ref.update({key: firestore.DELETE_FIELD})
    else:
        ref.update({key: {"riskId": risk_id, "state": state, "fingerprint": str(fp), "at": datetime.now(timezone.utc).isoformat()}})
    return jsonify({"riskId": risk_id, "state": state})


# -----------------------------------------------------------------------------
# recovery
# -----------------------------------------------------------------------------
def _recovery_plan(uid, term_id, today, now_minutes):
    inputs = load_inputs(uid, term_id)
    av = inputs["availability"]
    calibration = wl.estimate_calibration(all_task_history(uid))
    factor = calibration["factor"] if av.get("useCalibration") and calibration["sufficient"] else 1.0
    lwds = [wl.last_work_day(a) for a in inputs["assessments"] if a.get("dueDate") and a["status"] != "completed"]
    horizon = max([d for d in lwds if d and d >= today] + [today + timedelta(days=13)])
    horizon = min(horizon, today + timedelta(days=wl.HORIZON_DAYS))
    caps = wl.capacity_by_day(today, horizon, av, inputs["busy"], now_minutes)
    plan = rc.plan_recovery(today, inputs["tasks"], inputs["assessments"], caps,
                            max_session_minutes=av["maxSessionMinutes"], factor=factor)
    titles = {a["id"]: a["title"] for a in inputs["assessments"]}
    for c in plan["moves"] + plan["splits"] + plan["unplaceable"]:
        c["assessmentTitle"] = titles.get(c.get("projectId"), "")
    plan["today"] = today.isoformat()
    plan["factor"] = factor
    return plan


@bp.route("/api/planning/recovery/preview", methods=["POST"])
@require_auth
def recovery_preview():
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    term_id = _term_id(uid, body.get("termId"))
    if not term_id:
        return bad_request("no current term")
    today = parse_client_today(body.get("today"))
    return jsonify(_recovery_plan(uid, term_id, today, _now_minutes(body.get("nowMinutes"))))


def _parse_day(v, today):
    try:
        d = date.fromisoformat(v)
    except (TypeError, ValueError):
        return None
    if d < today or d > today + timedelta(days=366):
        return None
    return d


@bp.route("/api/planning/recovery/apply", methods=["POST"])
@require_auth
def recovery_apply():
    """apply the moves/splits the student accepted. rejects the whole request if any
    task changed since the preview, was completed, or doesn't belong to the student."""
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    today = parse_client_today(body.get("today"))
    basis = body.get("basis") or {}
    moves = body.get("moves") or []
    splits = body.get("splits") or []
    if not isinstance(moves, list) or not isinstance(splits, list) or not isinstance(basis, dict):
        return bad_request("malformed recovery plan")
    if not moves and not splits:
        return bad_request("no changes selected")
    if len(moves) + len(splits) > 150:
        return bad_request("too many changes at once")

    tasks_coll = user_doc(uid).collection("tasks")
    touched = {}
    for c in moves + splits:
        tid = c.get("taskId") if isinstance(c, dict) else None
        if not isinstance(tid, str) or tid in touched:
            return bad_request("each task can only be changed once")
        snap = tasks_coll.document(tid).get()
        if not snap.exists:
            return bad_request("a task in this plan no longer exists — rebuild the plan", 409)
        t = snap.to_dict()
        t["id"] = tid
        view = task_view(t)
        if view["status"] == "done":
            return bad_request(f"“{view['title']}” was completed since the plan was made — rebuild the plan", 409)
        if basis.get(tid) != rc.task_fingerprint(view):
            return bad_request(f"“{view['title']}” changed since the plan was made — rebuild the plan", 409)
        touched[tid] = t

    batch = get_db().batch()
    undo = {"at": datetime.now(timezone.utc).isoformat(), "tasks": {}, "created": []}

    def remember(tid):
        t = touched[tid]
        undo["tasks"][tid] = {
            "dueDate": t.get("dueDate"), "status": t.get("status"), "estimatedMinutes": t.get("estimatedMinutes"),
            "rescheduleCount": t.get("rescheduleCount") or 0, "originalScheduledDate": t.get("originalScheduledDate"),
            "splitParts": t.get("splitParts"),
        }

    def move_updates(t, d):
        new = date_to_storage(d)
        upd = {"dueDate": new, "status": "scheduled" if t.get("status") != "done" else t["status"]}
        old = as_date(t.get("dueDate"))
        if old and old != d:
            upd["rescheduleCount"] = (t.get("rescheduleCount") or 0) + 1
            if not t.get("originalScheduledDate"):
                upd["originalScheduledDate"] = t.get("dueDate")
        return upd

    for m in moves:
        d = _parse_day(m.get("to"), today)
        if d is None:
            return bad_request("move dates must be between today and a year from now")
        remember(m["taskId"])
        batch.update(tasks_coll.document(m["taskId"]), move_updates(touched[m["taskId"]], d))

    for s in splits:
        parts = s.get("parts")
        if not isinstance(parts, list) or not 2 <= len(parts) <= 10:
            return bad_request("a split needs between 2 and 10 parts")
        clean = []
        for p in parts:
            d = _parse_day(p.get("date"), today)
            mins = p.get("minutes")
            if d is None or isinstance(mins, bool) or not isinstance(mins, (int, float)) or not 5 <= mins <= 600:
                return bad_request("each part needs a valid date and 5–600 minutes")
            clean.append((d, int(mins)))
        tid = s["taskId"]
        t = touched[tid]
        remember(tid)
        n = len(clean)
        title = t.get("title", "")
        first_d, first_m = clean[0]
        upd = move_updates(t, first_d)
        # the original keeps the time already logged; its estimate becomes part 1
        upd["estimatedMinutes"] = int((t.get("timeSpent") or 0) + first_m)
        upd["title"] = f"{title} (part 1/{n})"
        upd["splitParts"] = n
        batch.update(tasks_coll.document(tid), upd)
        undo["tasks"][tid]["title"] = title
        for i, (d, mins) in enumerate(clean[1:], start=2):
            new_id = f"{tid}-part{i}"
            undo["created"].append(new_id)
            batch.set(tasks_coll.document(new_id), {
                "projectId": t.get("projectId"), "courseId": t.get("courseId"),
                "title": f"{title} (part {i}/{n})", "estimatedMinutes": mins, "timeSpent": None,
                "status": "scheduled", "dueDate": date_to_storage(d), "scheduledDate": None,
                "scheduledBlockId": None, "rescheduleCount": 0, "originalScheduledDate": None,
                "completedAt": None, "aiGenerated": bool(t.get("aiGenerated")),
                "order": (t.get("order") or 0) + i / 100, "splitFrom": tid,
                "dependsOn": t.get("dependsOn") or [], "kind": t.get("kind"),
                "conceptIds": t.get("conceptIds") or [],
                "createdAt": firestore.SERVER_TIMESTAMP,
            })

    batch.set(_planning(uid, "lastRecovery"), undo)
    batch.commit()
    return jsonify({"moved": len(moves), "split": len(splits), "undoAvailable": True})


@bp.route("/api/planning/recovery/undo", methods=["POST"])
@require_auth
def recovery_undo():
    uid = g.user_id
    ref = _planning(uid, "lastRecovery")
    snap = ref.get()
    if not snap.exists or not (snap.to_dict() or {}).get("tasks"):
        return bad_request("there's no plan change to undo", 404)
    data = snap.to_dict()
    tasks_coll = user_doc(uid).collection("tasks")
    for new_id in data.get("created", []):
        s = tasks_coll.document(new_id).get()
        if s.exists and (s.to_dict().get("status") == "done" or (s.to_dict().get("timeSpent") or 0) > 0):
            return bad_request("you've already worked on the new task parts, so this change can't be undone automatically", 409)
    batch = get_db().batch()
    restored = 0
    for tid, prev in data["tasks"].items():
        s = tasks_coll.document(tid).get()
        if not s.exists:
            continue
        upd = {k: v for k, v in prev.items()}
        if s.to_dict().get("status") == "done":
            upd.pop("status", None)  # never un-complete work
        batch.update(tasks_coll.document(tid), upd)
        restored += 1
    for new_id in data.get("created", []):
        batch.delete(tasks_coll.document(new_id))
    batch.set(ref, {"tasks": {}, "created": [], "undoneAt": datetime.now(timezone.utc).isoformat()})
    batch.commit()
    return jsonify({"restored": restored, "removedParts": len(data.get("created", []))})
