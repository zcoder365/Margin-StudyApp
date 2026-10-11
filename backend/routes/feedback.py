"""
feedback loop api: study session records, assessment outcomes, reviews, insights.

firestore (under users/{uid}):
  studySessions/{clientId}   taskId, projectId, courseId, conceptIds, plannedMinutes, actualMinutes,
                             startedAt, endedAt, completedTask, confidence (1–5), note
  projects/{id}.outcome      {score, total, percent, recordedAt, reflection}
  courses/{cid}/grades/outcome-{projectId}   optional copy into the existing grade calculator
"""

import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g
from firebase_admin import firestore

from core import (
    get_db, user_doc, require_auth, bad_request, stream_dicts, to_jsonable, as_date, parse_client_today,
)
from services import feedback as fb
from services import diagnostics as dx
from services import workload as wl

bp = Blueprint("feedback", __name__)

CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
FEELINGS = ("better", "as_expected", "worse")


def _col(uid, name):
    return user_doc(uid).collection(name)


def _parse_dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return "invalid"
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# -----------------------------------------------------------------------------
# study sessions
# -----------------------------------------------------------------------------
@bp.route("/api/study-sessions", methods=["POST"])
@require_auth
def log_session():
    """record a study session. retries with the same clientId don't double-count time."""
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    client_id = body.get("clientId")
    if not isinstance(client_id, str) or not CLIENT_ID.match(client_id):
        return bad_request("clientId (8–64 letters, digits, - or _) is required")
    ref = _col(uid, "studySessions").document(client_id)
    existing = ref.get()
    if existing.exists:
        return jsonify(to_jsonable({**existing.to_dict(), "id": client_id, "duplicate": True}))

    minutes = body.get("actualMinutes")
    if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not 1 <= minutes <= 600:
        return bad_request("actualMinutes must be between 1 and 600")
    planned = body.get("plannedMinutes")
    if planned is not None and (isinstance(planned, bool) or not isinstance(planned, (int, float)) or not 1 <= planned <= 600):
        return bad_request("plannedMinutes must be between 1 and 600")
    confidence = body.get("confidence")
    if confidence is not None and confidence not in (1, 2, 3, 4, 5):
        return bad_request("confidence must be 1–5")
    note = body.get("note")
    if note is not None and not isinstance(note, str):
        return bad_request("note must be text")
    started, ended = _parse_dt(body.get("startedAt")), _parse_dt(body.get("endedAt"))
    if "invalid" in (started, ended):
        return bad_request("startedAt/endedAt must be ISO timestamps")
    now = datetime.now(timezone.utc)
    ended = ended or now
    if ended > now + timedelta(minutes=5):
        return bad_request("a session can't end in the future")

    task_id, project_id = body.get("taskId"), body.get("projectId")
    task = None
    if task_id:
        tsnap = _col(uid, "tasks").document(str(task_id)).get()
        if not tsnap.exists:
            return bad_request("task not found", 404)
        task = tsnap.to_dict()
        project_id = task.get("projectId")
    if not project_id:
        return bad_request("a session needs a taskId or projectId")
    psnap = _col(uid, "projects").document(str(project_id)).get()
    if not psnap.exists:
        return bad_request("assessment not found", 404)
    course_id = psnap.to_dict().get("courseId")
    concept_ids = body.get("conceptIds") if isinstance(body.get("conceptIds"), list) else (task or {}).get("conceptIds") or []
    for c in concept_ids:
        cs = _col(uid, "concepts").document(str(c)).get()
        if not cs.exists or cs.to_dict().get("courseId") != course_id:
            return bad_request("conceptIds must belong to the assessment's course")

    data = {
        "taskId": task_id or None, "projectId": project_id, "courseId": course_id, "conceptIds": concept_ids,
        "plannedMinutes": planned, "actualMinutes": round(float(minutes), 1),
        "startedAt": started or (ended - timedelta(minutes=float(minutes))), "endedAt": ended,
        "completedTask": bool(body.get("completedTask")) and bool(task_id),
        "confidence": confidence, "note": (note or "").strip()[:500] or None,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    batch = get_db().batch()
    batch.set(ref, data)
    if task_id:
        upd = {"timeSpent": firestore.Increment(round(float(minutes), 1))}
        if data["completedTask"] and task.get("status") != "done":
            upd.update({"status": "done", "completedAt": firestore.SERVER_TIMESTAMP})
        batch.update(_col(uid, "tasks").document(task_id), upd)
    batch.commit()
    out = {**data, "id": client_id, "createdAt": None}
    if task_id:
        fresh = _col(uid, "tasks").document(task_id).get().to_dict()
        out["task"] = {"id": task_id, **fresh}
    return jsonify(to_jsonable(out)), 201


@bp.route("/api/study-sessions/<session_id>", methods=["PATCH"])
@require_auth
def update_session(session_id):
    """add the optional follow-up details after a session was logged (confidence, note, task finished)."""
    uid = g.user_id
    ref = _col(uid, "studySessions").document(session_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("session not found", 404)
    cur = snap.to_dict()
    body = request.get_json(silent=True) or {}
    upd = {}
    if "confidence" in body:
        if body["confidence"] is not None and body["confidence"] not in (1, 2, 3, 4, 5):
            return bad_request("confidence must be 1–5")
        upd["confidence"] = body["confidence"]
    if "note" in body:
        if body["note"] is not None and not isinstance(body["note"], str):
            return bad_request("note must be text")
        upd["note"] = (body["note"] or "").strip()[:500] or None
    batch = get_db().batch()
    if body.get("completedTask") is True and cur.get("taskId") and not cur.get("completedTask"):
        upd["completedTask"] = True
        tref = _col(uid, "tasks").document(cur["taskId"])
        tsnap = tref.get()
        if tsnap.exists and tsnap.to_dict().get("status") != "done":
            batch.update(tref, {"status": "done", "completedAt": firestore.SERVER_TIMESTAMP})
    if not upd:
        return bad_request("nothing to update")
    batch.update(ref, upd)
    batch.commit()
    return jsonify(to_jsonable({**ref.get().to_dict(), "id": session_id}))


@bp.route("/api/study-sessions", methods=["GET"])
@require_auth
def list_sessions():
    uid = g.user_id
    project_id = request.args.get("projectId")
    q = _col(uid, "studySessions")
    if project_id:
        q = q.where("projectId", "==", project_id)
    rows = stream_dicts(q)
    rows.sort(key=lambda s: str(s.get("endedAt") or ""), reverse=True)
    return jsonify({"sessions": [to_jsonable(r) for r in rows[:200]]})


# -----------------------------------------------------------------------------
# outcomes
# -----------------------------------------------------------------------------
@bp.route("/api/projects/<project_id>/outcome", methods=["PUT"])
@require_auth
def set_outcome(project_id):
    uid = g.user_id
    pref = _col(uid, "projects").document(project_id)
    psnap = pref.get()
    if not psnap.exists:
        return bad_request("assessment not found", 404)
    project = psnap.to_dict()
    body = request.get_json(silent=True) or {}
    score, total = body.get("score"), body.get("total")
    for v in (score, total):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return bad_request("score and total must be numbers")
    if total <= 0 or score < 0 or score > total * 1.5:
        return bad_request("enter a score between 0 and (a little over) the total")
    reflection = body.get("reflection") or {}
    if not isinstance(reflection, dict):
        return bad_request("reflection must be an object")
    feeling = reflection.get("feeling")
    if feeling is not None and feeling not in FEELINGS:
        return bad_request("feeling must be better, as_expected or worse")
    outcome = {
        "score": float(score), "total": float(total), "percent": round(score / total * 100, 1),
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "reflection": {"feeling": feeling, "note": str(reflection.get("note") or "").strip()[:1000] or None},
    }
    batch = get_db().batch()
    upd = {"outcome": outcome}
    if body.get("markComplete", True) and project.get("status") != "completed":
        upd.update({"status": "completed", "completedAt": firestore.SERVER_TIMESTAMP})
    batch.update(pref, upd)
    add = body.get("addToGrades")
    if add:
        cat = add.get("categoryId") if isinstance(add, dict) else None
        course_ref = _col(uid, "courses").document(project["courseId"])
        scheme = course_ref.collection("grading").document("scheme").get()
        cats = (scheme.to_dict() or {}).get("categories", []) if scheme.exists else []
        if not any(c.get("id") == cat for c in cats):
            return bad_request("that grade category doesn't exist for this course")
        batch.set(course_ref.collection("grades").document(f"outcome-{project_id}"), {
            "categoryId": cat, "name": project.get("title", "assessment"), "score": float(score), "total": float(total),
            "projectId": project_id, "createdAt": firestore.SERVER_TIMESTAMP,
        })
    batch.commit()
    return jsonify(to_jsonable({"outcome": outcome, "status": upd.get("status", project.get("status"))}))


@bp.route("/api/projects/<project_id>/outcome", methods=["DELETE"])
@require_auth
def delete_outcome(project_id):
    pref = _col(g.user_id, "projects").document(project_id)
    if not pref.get().exists:
        return bad_request("assessment not found", 404)
    pref.update({"outcome": None})
    return jsonify({"deleted": True})


# -----------------------------------------------------------------------------
# review
# -----------------------------------------------------------------------------
def build_review(uid, project_id, project, today):
    cid = project.get("courseId")
    tasks = stream_dicts(_col(uid, "tasks").where("projectId", "==", project_id))
    sessions = stream_dicts(_col(uid, "studySessions").where("projectId", "==", project_id))
    hist_snap = user_doc(uid).collection("planning").document("history").get()
    history = ((hist_snap.to_dict() or {}).get("byProject", {}) if hist_snap.exists else {}).get(project_id, [])
    from routes.diagnostics import course_concepts, concept_assessments
    concepts = course_concepts(uid, cid)
    links, _ = concept_assessments(uid, cid, concepts)
    linked = [c for c in concepts if any(a["id"] == project_id for a in links.get(c["id"], []))]
    # concept links skip completed assessments; fall back to objective links directly
    if not linked:
        objectives = {o["id"]: o for o in stream_dicts(_col(uid, "objectives").where("courseId", "==", cid))}
        linked = [c for c in concepts if project_id in (c.get("assessmentIds") or []) or any(
            project_id in (objectives.get(o) or {}).get("assessmentIds", []) for o in c.get("objectiveIds") or [])]
    attempts = stream_dicts(_col(uid, "attempts").where("courseId", "==", cid))
    flagged = {q["id"] for q in stream_dicts(_col(uid, "questions").where("courseId", "==", cid)) if q.get("status") in ("flagged", "retired")}
    by_concept = {c["id"]: [a for a in attempts if c["id"] in (a.get("conceptIds") or []) and a.get("questionId") not in flagged] for c in linked}
    assessment = {**project, "id": project_id, "dueDate": as_date(project.get("dueDate"))}
    return fb.assessment_review(assessment, tasks, sessions, history, by_concept,
                                [{"id": c["id"], "name": c["name"]} for c in linked], today)


@bp.route("/api/projects/<project_id>/review", methods=["GET"])
@require_auth
def get_review(project_id):
    uid = g.user_id
    snap = _col(uid, "projects").document(project_id).get()
    if not snap.exists:
        return bad_request("assessment not found", 404)
    today = parse_client_today(request.args.get("today"))
    return jsonify(to_jsonable(build_review(uid, project_id, snap.to_dict(), today)))


# -----------------------------------------------------------------------------
# insights (progress + adaptive recommendations)
# -----------------------------------------------------------------------------
@bp.route("/api/insights", methods=["GET"])
@require_auth
def insights():
    from routes.planning import load_inputs, all_task_history, _term_id
    from routes.diagnostics import course_concepts, concept_assessments, compute_mastery, course_attempts, course_questions
    uid = g.user_id
    today = parse_client_today(request.args.get("today"))
    term_id = _term_id(uid, request.args.get("termId"))
    inputs = load_inputs(uid, term_id) if term_id else {"courses": {}, "assessments": [], "tasks": [], "availability": wl.DEFAULT_AVAILABILITY}
    history = [{**t, "plannedDate": as_date(t.get("dueDate"))} for t in all_task_history(uid)]
    sessions = stream_dicts(_col(uid, "studySessions"))
    me = user_doc(uid).get()
    week_start = 0 if ((me.to_dict() or {}).get("weekStartDay", 1) == 1) else 6

    upcoming_concepts, recent_activity, weak_by_assessment, concept_progress = [], {}, {}, []
    for cid, course in inputs["courses"].items():
        concepts = course_concepts(uid, cid)
        if not concepts:
            continue
        attempts = course_attempts(uid, cid)
        questions = course_questions(uid, cid)
        links, _ = concept_assessments(uid, cid, concepts)
        now_m = compute_mastery(concepts, attempts, questions)
        two_weeks_ago = datetime.combine(today - timedelta(days=14), datetime.min.time(), tzinfo=timezone.utc)
        then_m = compute_mastery(concepts, [a for a in attempts if fb._as_dt(a.get("createdAt")) < two_weeks_ago], questions, now=two_weeks_ago)
        count = lambda m: {k: sum(1 for v in m.values() if v["status"] == k) for k in dx.MASTERY_RULES}  # noqa: E731
        concept_progress.append({"courseId": cid, "name": course.get("name"), "now": count(now_m), "twoWeeksAgo": count(then_m)})
        for a in attempts:
            d = fb._d(a.get("createdAt"))
            for c in a.get("conceptIds") or []:
                if d and (recent_activity.get(c) is None or d > recent_activity[c]):
                    recent_activity[c] = d
        for c in concepts:
            for a in links[c["id"]]:
                if a["dueDate"] and today <= a["dueDate"] <= today + timedelta(days=14):
                    upcoming_concepts.append({"conceptId": c["id"], "name": c["name"], "assessmentId": a["id"],
                                              "assessmentTitle": a["title"], "dueDate": a["dueDate"]})
                if a["dueDate"] and today <= a["dueDate"] <= today + timedelta(days=21) and now_m[c["id"]]["status"] == "needs_practice":
                    weak_by_assessment.setdefault(a["id"], (a["title"], a["dueDate"], []))[2].append(c["name"])

    projects_raw = {}
    for cid in inputs["courses"]:
        for p in stream_dicts(_col(uid, "projects").where("courseId", "==", cid)):
            projects_raw[p["id"]] = p
    past = [a for a in inputs["assessments"] if a.get("dueDate") and a["dueDate"] < today]
    past.sort(key=lambda a: a["dueDate"], reverse=True)
    reviews, review_rows = [], []
    for a in past[:5]:
        r = build_review(uid, a["id"], projects_raw[a["id"]], today)
        reviews.append(r)
        review_rows.append({"id": a["id"], "title": a["title"], "courseName": a["courseName"], "dueDate": a["dueDate"],
                            "outcome": projects_raw[a["id"]].get("outcome"), "status": a["status"]})

    recs = fb.recommendations(today, history, {cid: c.get("name", "") for cid, c in inputs["courses"].items()},
                              inputs["availability"], upcoming_concepts, recent_activity, weak_by_assessment, reviews)
    acks_snap = user_doc(uid).collection("planning").document("acks").get()
    acks = (acks_snap.to_dict() or {}) if acks_snap.exists else {}
    for r in recs:
        a = acks.get(r["id"].replace(".", "_"))
        r["ack"] = a.get("state") if a and a.get("fingerprint") == r["fingerprint"] else None

    calibration = wl.estimate_calibration(history)
    per_course = []
    for cid, c in inputs["courses"].items():
        cc = wl.estimate_calibration([t for t in history if t.get("courseId") == cid])
        per_course.append({"courseId": cid, "name": c.get("name"), **cc})
    return jsonify(to_jsonable({
        "recommendations": recs,
        "weekly": fb.weekly_study(sessions, history, today, weeks=6, week_start=week_start),
        "calibration": calibration, "calibrationByCourse": per_course,
        "conceptProgress": concept_progress,
        "pastAssessments": review_rows,
        "notes": ["Logged time comes from study sessions (the timer or “log study time”). Time typed directly into a task isn't dated, so it isn't in the weekly view."],
    }))
