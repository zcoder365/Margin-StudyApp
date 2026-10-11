"""
diagnostics api: concept maps, diagnostic sessions, mastery and targeted practice.

firestore (under users/{uid}):
  concepts/{id}               courseId, name, description, objectiveIds, assessmentIds, sourceRefs, origin
  conceptSuggestions/{cid}    background job output: suggested concepts awaiting the student's ok
  questions/{id}              courseId, conceptIds, type, prompt, answer key, explanation, grounding, status, flags
  diagnosticSessions/{id}     courseId, assessmentId, conceptIds, questionIds, status, summary
  attempts/{sessionId-qid}    one answer: response, correct / selfGrade, confidence, difficulty

mastery is never stored — it's recomputed from attempts so it can't drift from
the evidence, and the evidence is returned alongside it.
"""

import logging
import math
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g
from firebase_admin import firestore

from core import (
    get_db, user_doc, require_auth, bad_request, stream_dicts, to_jsonable,
    as_date, date_to_storage, run_job, parse_client_today, commit_in_chunks,
)
from ai import get_ai, AIError
from services import diagnostics as dx
from services import workload as wl

log = logging.getLogger("margin.diagnostics")
bp = Blueprint("diagnostics", __name__)

MAX_SESSION = 12
EXCERPT_CHARS = 18000
STALE_JOB = timedelta(minutes=5)


def _ts(v):
    """comparable timestamp string (firestore datetimes and iso strings alike)."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).isoformat()
    return str(v or "")


def _col(uid, name):
    return user_doc(uid).collection(name)


def _course(uid, cid):
    s = _col(uid, "courses").document(cid).get()
    if not s.exists:
        return None
    return {**s.to_dict(), "id": cid}


def course_concepts(uid, cid):
    rows = [c for c in stream_dicts(_col(uid, "concepts").where("courseId", "==", cid)) if c.get("status", "active") == "active"]
    rows.sort(key=lambda c: c.get("name", "").lower())
    return rows


def course_objectives(uid, cid):
    return [o for o in stream_dicts(_col(uid, "objectives").where("courseId", "==", cid)) if o.get("status") == "confirmed"]


def course_excerpts(uid, cid):
    """page-labelled excerpts of the course's documents (most useful types first), capped in size."""
    order = {"lecture_notes": 0, "exam_review": 1, "syllabus": 2, "assignment": 3, "project_brief": 4, "rubric": 5, "other": 6}
    docs = [d for d in stream_dicts(_col(uid, "documents").where("courseId", "==", cid)) if d.get("hasText")]
    docs.sort(key=lambda d: order.get(d.get("docType"), 9))
    excerpts, by_doc, used = [], {}, 0
    for i, d in enumerate(docs[:8]):
        pages = stream_dicts(_col(uid, "documents").document(d["id"]).collection("pages"))
        pages.sort(key=lambda p: p.get("n", 0))
        texts = [p.get("text", "") for p in pages]
        by_doc[i] = {"id": d["id"], "filename": d.get("filename"), "pages": texts}
        for n, t in enumerate(texts, start=1):
            if used >= EXCERPT_CHARS:
                break
            chunk = t[:1500]
            if chunk.strip():
                excerpts.append({"docIndex": i, "page": n, "text": chunk})
                used += len(chunk)
    return excerpts, by_doc


def course_attempts(uid, cid):
    return stream_dicts(_col(uid, "attempts").where("courseId", "==", cid))


def course_questions(uid, cid):
    return stream_dicts(_col(uid, "questions").where("courseId", "==", cid))


def excluded_question_ids(questions):
    return {q["id"] for q in questions if q.get("status") in ("flagged", "retired")}


def compute_mastery(concepts, attempts, questions, now=None):
    excluded = excluded_question_ids(questions)
    out = {}
    for c in concepts:
        mine = [a for a in attempts if c["id"] in (a.get("conceptIds") or [])]
        out[c["id"]] = dx.mastery(mine, now=now, excluded_questions=excluded)
    return out


def concept_assessments(uid, cid, concepts):
    """{conceptId: [assessment]} via linked objectives (and any direct links)."""
    projects = {p["id"]: p for p in stream_dicts(_col(uid, "projects").where("courseId", "==", cid))}
    objectives = {o["id"]: o for o in course_objectives(uid, cid)}
    out = {}
    for c in concepts:
        ids = set(c.get("assessmentIds") or [])
        for oid in c.get("objectiveIds") or []:
            ids.update((objectives.get(oid) or {}).get("assessmentIds") or [])
        rows = []
        for pid in ids:
            p = projects.get(pid)
            if p and p.get("status") != "completed":
                rows.append({"id": pid, "title": p.get("title", ""), "dueDate": as_date(p.get("dueDate")),
                             "weight": p.get("weight"), "kind": p.get("kind")})
        out[c["id"]] = rows
    return out, projects


def _ser_mastery(m):
    return to_jsonable(m)


# -----------------------------------------------------------------------------
# concepts
# -----------------------------------------------------------------------------
@bp.route("/api/courses/<cid>/concepts", methods=["GET"])
@require_auth
def list_concepts(cid):
    uid = g.user_id
    if _course(uid, cid) is None:
        return bad_request("course not found", 404)
    concepts = course_concepts(uid, cid)
    mastery = compute_mastery(concepts, course_attempts(uid, cid), course_questions(uid, cid))
    links, _ = concept_assessments(uid, cid, concepts)
    rows = []
    for c in concepts:
        rows.append(to_jsonable({**c, "mastery": mastery[c["id"]],
                                 "assessments": [{"id": a["id"], "title": a["title"], "dueDate": a["dueDate"]} for a in links[c["id"]]]}))
    return jsonify({"concepts": rows, "rules": dx.MASTERY_RULES, "evidenceNotes": dx.EVIDENCE_NOTES})


def _suggest_ref(uid, cid):
    return _col(uid, "conceptSuggestions").document(cid)


@bp.route("/api/courses/<cid>/concepts/suggest", methods=["POST"])
@require_auth
def suggest_concepts(cid):
    uid = g.user_id
    if _course(uid, cid) is None:
        return bad_request("course not found", 404)
    snap = _suggest_ref(uid, cid).get()
    cur = snap.to_dict() if snap.exists else {}
    started = cur.get("startedAt")
    if cur.get("status") == "processing" and started and datetime.now(timezone.utc) - started < STALE_JOB:
        return bad_request("concept suggestions are already being prepared", 409)
    if not course_objectives(uid, cid) and not any(d.get("hasText") for d in stream_dicts(_col(uid, "documents").where("courseId", "==", cid))):
        return bad_request("add course documents or confirmed learning objectives first, so suggestions can be based on your material")
    _suggest_ref(uid, cid).set({"status": "processing", "error": None, "suggestions": [], "startedAt": firestore.SERVER_TIMESTAMP})
    run_job(run_concept_suggestions, uid, cid)
    return jsonify({"status": "processing"}), 202


def run_concept_suggestions(uid, cid):
    ref = _suggest_ref(uid, cid)
    try:
        course = _course(uid, cid) or {}
        objectives = course_objectives(uid, cid)
        excerpts, by_doc = course_excerpts(uid, cid)
        raw = get_ai().generate_json(dx.build_concept_prompt(course.get("name", ""), objectives, excerpts))
        suggestions = dx.normalize_concepts(raw, objectives, by_doc)
        existing = {dx.concept_id(cid, c["name"]) for c in course_concepts(uid, cid)}
        for s in suggestions:
            s["alreadyAdded"] = dx.concept_id(cid, s["name"]) in existing
        ref.set({"status": "ready", "suggestions": suggestions, "error": None,
                 "finishedAt": firestore.SERVER_TIMESTAMP}, merge=True)
    except AIError as e:
        ref.set({"status": "failed", "error": str(e), "errorKind": e.kind}, merge=True)
    except ValueError as e:
        ref.set({"status": "failed", "error": str(e), "errorKind": "malformed"}, merge=True)
    except Exception:
        log.exception("concept suggestion job crashed")
        ref.set({"status": "failed", "error": "something went wrong while suggesting concepts", "errorKind": "error"}, merge=True)


@bp.route("/api/courses/<cid>/concepts/suggestions", methods=["GET"])
@require_auth
def get_suggestions(cid):
    uid = g.user_id
    if _course(uid, cid) is None:
        return bad_request("course not found", 404)
    snap = _suggest_ref(uid, cid).get()
    return jsonify(to_jsonable(snap.to_dict()) if snap.exists else {"status": "none", "suggestions": []})


def _valid_ids(uid, coll, cid, ids):
    if not isinstance(ids, list) or len(ids) > 30:
        return None
    out = []
    for i in ids:
        if not isinstance(i, str):
            return None
        s = _col(uid, coll).document(i).get()
        if not s.exists or s.to_dict().get("courseId") != cid:
            return None
        out.append(i)
    return list(dict.fromkeys(out))


@bp.route("/api/courses/<cid>/concepts", methods=["POST"])
@require_auth
def add_concepts(cid):
    """add concepts (manually or by accepting suggestions). idempotent per concept name."""
    uid = g.user_id
    if _course(uid, cid) is None:
        return bad_request("course not found", 404)
    body = request.get_json(silent=True) or {}
    items = body.get("concepts")
    if not isinstance(items, list) or not items or len(items) > 40:
        return bad_request("send 1–40 concepts")
    snap = _suggest_ref(uid, cid).get()
    suggestions = (snap.to_dict() or {}).get("suggestions", []) if snap.exists else []
    batch, created = get_db().batch(), []
    for it in items:
        if not isinstance(it, dict):
            return bad_request("malformed concept")
        name = (it.get("name") or "").strip()
        if not name:
            return bad_request("every concept needs a name")
        objective_ids = _valid_ids(uid, "objectives", cid, it.get("objectiveIds") or [])
        if objective_ids is None:
            return bad_request("objectiveIds must be learning objectives from this course")
        refs, origin = [], "student"
        idx = it.get("fromSuggestion")
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(suggestions):
            refs, origin = suggestions[idx].get("sourceRefs", []), "ai_suggested"
        concept_id = dx.concept_id(cid, name)
        batch.set(_col(uid, "concepts").document(concept_id), {
            "courseId": cid, "name": name[:80], "description": (it.get("description") or "").strip()[:400],
            "objectiveIds": objective_ids, "assessmentIds": [], "sourceRefs": refs, "origin": origin,
            "status": "active", "updatedAt": firestore.SERVER_TIMESTAMP,
        }, merge=True)
        created.append(concept_id)
    batch.commit()
    return jsonify({"created": created}), 201


@bp.route("/api/concepts/<concept_id>", methods=["PATCH"])
@require_auth
def update_concept(concept_id):
    uid = g.user_id
    ref = _col(uid, "concepts").document(concept_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("concept not found", 404)
    cur = snap.to_dict()
    body = request.get_json(silent=True) or {}
    upd = {}
    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            return bad_request("name can't be empty")
        upd["name"] = name[:80]
    if "description" in body:
        upd["description"] = (body.get("description") or "").strip()[:400]
    for field, coll in (("objectiveIds", "objectives"), ("assessmentIds", "projects")):
        if field in body:
            ids = _valid_ids(uid, coll, cur["courseId"], body[field] or [])
            if ids is None:
                return bad_request(f"{field} must reference items in this course")
            upd[field] = ids
    if not upd:
        return bad_request("nothing to update")
    upd["updatedAt"] = firestore.SERVER_TIMESTAMP
    ref.update(upd)
    fresh = ref.get().to_dict()
    fresh["id"] = concept_id
    return jsonify(to_jsonable(fresh))


@bp.route("/api/concepts/<concept_id>", methods=["DELETE"])
@require_auth
def delete_concept(concept_id):
    """remove a concept. its questions lose the link (and are retired if it was their only concept);
    past attempts are kept so history isn't rewritten."""
    uid = g.user_id
    ref = _col(uid, "concepts").document(concept_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("concept not found", 404)
    ops = []
    for q in stream_dicts(_col(uid, "questions").where("courseId", "==", snap.to_dict()["courseId"])):
        if concept_id in (q.get("conceptIds") or []):
            rest = [c for c in q["conceptIds"] if c != concept_id]
            upd = {"conceptIds": rest}
            if not rest:
                upd["status"] = "retired"
            ops.append(("update", _col(uid, "questions").document(q["id"]), upd))
    ops.append(("delete", ref, None))
    commit_in_chunks(ops)
    return jsonify({"deleted": concept_id})


# -----------------------------------------------------------------------------
# sessions
# -----------------------------------------------------------------------------
def _session_ref(uid, sid):
    return _col(uid, "diagnosticSessions").document(sid)


@bp.route("/api/diagnostics/sessions", methods=["POST"])
@require_auth
def create_session():
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    cid = body.get("courseId")
    if not isinstance(cid, str) or _course(uid, cid) is None:
        return bad_request("course not found", 404)
    concepts = course_concepts(uid, cid)
    if not concepts:
        return bad_request("this course has no concepts yet — add or accept some first")
    size = body.get("size", 6)
    if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= MAX_SESSION:
        return bad_request(f"size must be 1–{MAX_SESSION}")
    assessment_id = body.get("assessmentId") or None
    chosen = concepts
    if assessment_id:
        p = _col(uid, "projects").document(assessment_id).get()
        if not p.exists or p.to_dict().get("courseId") != cid:
            return bad_request("assessment not found in this course", 404)
        links, _ = concept_assessments(uid, cid, concepts)
        linked = [c for c in concepts if any(a["id"] == assessment_id for a in links[c["id"]])]
        chosen = linked or concepts  # fall back to the whole course if nothing is linked yet
    if body.get("conceptIds"):
        wanted = set(body["conceptIds"]) if isinstance(body["conceptIds"], list) else set()
        chosen = [c for c in concepts if c["id"] in wanted]
        if not chosen:
            return bad_request("none of those concepts belong to this course")
    ref = _col(uid, "diagnosticSessions").document()
    ref.set({
        "courseId": cid, "assessmentId": assessment_id, "conceptIds": [c["id"] for c in chosen],
        "size": size, "questionIds": [], "status": "preparing", "warnings": [],
        "createdAt": firestore.SERVER_TIMESTAMP, "startedAt": firestore.SERVER_TIMESTAMP,
    })
    run_job(prepare_session, uid, ref.id)
    return jsonify({"id": ref.id, "status": "preparing"}), 202


def prepare_session(uid, sid):
    """pick existing questions (weak / unseen first), generate what's missing, validate, fall back if needed."""
    ref = _session_ref(uid, sid)
    try:
        s = ref.get().to_dict()
        cid = s["courseId"]
        concepts = [c for c in course_concepts(uid, cid) if c["id"] in s["conceptIds"]]
        questions = [q for q in course_questions(uid, cid) if q.get("status", "active") == "active"]
        attempts = course_attempts(uid, cid)
        mastery = compute_mastery(concepts, attempts, questions)
        size = s["size"]

        # most in need first; ties → least recently practised
        rank = {"needs_practice": 0, "insufficient_evidence": 1, "developing": 2, "demonstrated": 3}
        concepts.sort(key=lambda c: (rank[mastery[c["id"]]["status"]], _ts(mastery[c["id"]]["evidence"]["lastAttemptAt"])))
        targets = concepts[:size]
        per_concept = max(1, math.ceil(size / max(len(targets), 1)))

        last_seen = {}
        for a in attempts:
            ts = _ts(a.get("createdAt"))
            if ts > last_seen.get(a["questionId"], ""):
                last_seen[a["questionId"]] = ts
        recent_cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

        picked, need = [], {}
        for c in targets:
            pool = [q for q in questions if c["id"] in q.get("conceptIds", []) and q["id"] not in picked
                    and last_seen.get(q["id"], "") < recent_cutoff]
            pool.sort(key=lambda q: last_seen.get(q["id"], ""))
            take = pool[:per_concept]
            picked += [q["id"] for q in take]
            if len(take) < per_concept:
                need[c["id"]] = per_concept - len(take)
        picked = picked[:size]
        missing = min(sum(need.values()), size - len(picked))

        warnings, stats = [], {"requested": 0, "valid": 0, "dropped": 0}
        new_questions = []
        if missing > 0:
            gen_concepts = [c for c in targets if c["id"] in need]
            stats["requested"] = missing
            try:
                course = _course(uid, cid) or {}
                excerpts, by_doc = course_excerpts(uid, cid)
                objs = [o for o in course_objectives(uid, cid)
                        if any(o["id"] in (c.get("objectiveIds") or []) for c in gen_concepts)] or course_objectives(uid, cid)
                prompt = dx.build_question_prompt(course.get("name", ""), gen_concepts, objs, excerpts, missing,
                                                  avoid=[q["prompt"][:120] for q in questions][:30])
                raw = get_ai().generate_json(prompt)
                raw_list = raw.get("questions") if isinstance(raw, dict) else raw
                if not isinstance(raw_list, list):
                    raise AIError("the AI response was not in the expected format", kind="malformed")
                allowed = {c["id"] for c in gen_concepts}
                for rq in raw_list[: missing + 4]:
                    q, why = dx.validate_question(rq, allowed, by_doc)
                    if q is None:
                        stats["dropped"] += 1
                        continue
                    new_questions.append(q)
                new_questions = new_questions[:missing]
                stats["valid"] = len(new_questions)
                if stats["dropped"]:
                    warnings.append(f"{stats['dropped']} generated question(s) failed validation and were discarded")
            except AIError as e:
                warnings.append(f"new questions couldn't be generated ({e})")

        if missing > 0 and len(new_questions) < missing and not picked and not new_questions:
            # nothing usable at all: deterministic self-check prompts so the student can still practise
            new_questions = dx.fallback_questions(targets[:size])
            warnings.append("using self-check prompts because no questions could be generated — try again later for full questions")

        batch = get_db().batch()
        for q in new_questions:
            qref = _col(uid, "questions").document()
            batch.set(qref, {**q, "courseId": cid, "status": "active", "flags": [], "origin": "ai" if q["grounding"] != "fallback" else "fallback",
                             "createdAt": firestore.SERVER_TIMESTAMP})
            picked.append(qref.id)
        if not picked:
            batch.update(ref, {"status": "failed", "warnings": warnings or ["no questions available"], "generation": stats})
        else:
            batch.update(ref, {"status": "ready", "questionIds": picked[:max(size, len(picked))], "warnings": warnings,
                               "generation": stats, "readyAt": firestore.SERVER_TIMESTAMP})
        batch.commit()
    except Exception:
        log.exception("session preparation crashed")
        try:
            ref.update({"status": "failed", "warnings": ["something went wrong while preparing this session"]})
        except Exception:
            pass


def _load_session(uid, sid):
    snap = _session_ref(uid, sid).get()
    if not snap.exists:
        return None
    return {**snap.to_dict(), "id": sid}


def _session_payload(uid, s):
    qs = {}
    for qid in s.get("questionIds", []):
        q = _col(uid, "questions").document(qid).get()
        if q.exists:
            qs[qid] = {**q.to_dict(), "id": qid}
    attempts = {a["questionId"]: a for a in stream_dicts(_col(uid, "attempts").where("sessionId", "==", s["id"]))}
    items = []
    for qid in s.get("questionIds", []):
        if qid not in qs:
            continue
        a = attempts.get(qid)
        items.append({"question": dx.public_question(qs[qid], reveal=a is not None),
                      "attempt": to_jsonable({k: a.get(k) for k in ("response", "correct", "selfGrade", "confidence", "gradingMethod")}) if a else None})
    concepts = {c["id"]: c["name"] for c in course_concepts(uid, s["courseId"])}
    return to_jsonable({**{k: v for k, v in s.items() if k != "questionIds"}, "items": items, "conceptNames": concepts})


@bp.route("/api/diagnostics/sessions/<sid>", methods=["GET"])
@require_auth
def get_session(sid):
    s = _load_session(g.user_id, sid)
    if s is None:
        return bad_request("session not found", 404)
    return jsonify(_session_payload(g.user_id, s))


@bp.route("/api/diagnostics/sessions/<sid>/answer", methods=["POST"])
@require_auth
def answer(sid):
    uid = g.user_id
    s = _load_session(uid, sid)
    if s is None:
        return bad_request("session not found", 404)
    if s["status"] not in ("ready", "in_progress"):
        return bad_request("this session isn't accepting answers")
    body = request.get_json(silent=True) or {}
    qid = body.get("questionId")
    if qid not in s.get("questionIds", []):
        return bad_request("that question isn't part of this session")
    aref = _col(uid, "attempts").document(f"{sid}-{qid}")
    if aref.get().exists:
        return bad_request("you already answered this question", 409)
    qsnap = _col(uid, "questions").document(qid).get()
    if not qsnap.exists:
        return bad_request("question not found", 404)
    q = {**qsnap.to_dict(), "id": qid}
    confidence = body.get("confidence")
    if confidence is not None and confidence not in dx.CONFIDENCE_LEVELS:
        return bad_request("confidence must be 1, 2 or 3")
    response = body.get("response")
    correct, method = None, "self"
    if q["type"] == "mcq":
        if isinstance(response, bool) or not isinstance(response, int) or not 0 <= response < len(q["choices"]):
            return bad_request("choose one of the options")
        correct, method = response == q["answerIndex"], "exact"
    elif q["type"] == "numeric":
        try:
            correct, parsed = dx.grade_numeric(response, q["answer"], abs_tol=q.get("tolerance"))
        except dx.NumericParseError as e:
            return bad_request(f"couldn't read your answer as a number: {e}")
        method = "numeric"
    else:
        if not isinstance(response, str) or not response.strip():
            return bad_request("write an answer first (or a note that you're not sure)")
        response = response.strip()[:4000]
    batch = get_db().batch()
    batch.set(aref, {
        "sessionId": sid, "questionId": qid, "courseId": s["courseId"], "conceptIds": q.get("conceptIds", []),
        "type": q["type"], "response": response, "correct": correct, "gradingMethod": method, "selfGrade": None,
        "confidence": confidence, "difficulty": q.get("difficulty", 2), "createdAt": firestore.SERVER_TIMESTAMP,
    })
    if s["status"] == "ready":
        batch.update(_session_ref(uid, sid), {"status": "in_progress"})
    batch.commit()
    return jsonify({"correct": correct, "gradingMethod": method, "question": dx.public_question(q, reveal=True)})


@bp.route("/api/diagnostics/sessions/<sid>/self-grade", methods=["POST"])
@require_auth
def self_grade(sid):
    uid = g.user_id
    s = _load_session(uid, sid)
    if s is None:
        return bad_request("session not found", 404)
    body = request.get_json(silent=True) or {}
    grade = body.get("result")
    if grade not in dx.SELF_GRADES:
        return bad_request("result must be correct, partial or incorrect")
    aref = _col(uid, "attempts").document(f"{sid}-{body.get('questionId')}")
    snap = aref.get()
    if not snap.exists or snap.to_dict().get("gradingMethod") != "self":
        return bad_request("no self-graded answer to update", 404)
    aref.update({"selfGrade": grade})
    return jsonify({"questionId": body.get("questionId"), "selfGrade": grade})


@bp.route("/api/diagnostics/sessions/<sid>/complete", methods=["POST"])
@require_auth
def complete_session(sid):
    uid = g.user_id
    s = _load_session(uid, sid)
    if s is None:
        return bad_request("session not found", 404)
    cid = s["courseId"]
    attempts = stream_dicts(_col(uid, "attempts").where("sessionId", "==", sid))
    concepts = course_concepts(uid, cid)
    names = {c["id"]: c["name"] for c in concepts}
    per = {}
    for a in attempts:
        sc = dx.attempt_score(a)
        for c in a.get("conceptIds", []):
            p = per.setdefault(c, {"conceptId": c, "name": names.get(c, "removed concept"), "answered": 0, "score": 0.0, "ungraded": 0})
            if sc is None:
                p["ungraded"] += 1
            else:
                p["answered"] += 1
                p["score"] += sc
    all_attempts = course_attempts(uid, cid)
    questions = course_questions(uid, cid)
    mastery = compute_mastery([c for c in concepts if c["id"] in per], all_attempts, questions)
    for c, p in per.items():
        if c in mastery:
            p["masteryAfter"] = mastery[c]["status"]
            p["reasons"] = mastery[c]["reasons"]
    summary = {"answered": sum(1 for a in attempts if dx.attempt_score(a) is not None),
               "total": len(s.get("questionIds", [])), "concepts": list(per.values())}
    _session_ref(uid, sid).update({"status": "completed", "summary": summary, "completedAt": firestore.SERVER_TIMESTAMP})
    today = parse_client_today((request.get_json(silent=True) or {}).get("today"))
    links, _ = concept_assessments(uid, cid, concepts)
    plan = dx.practice_priorities(mastery, [c for c in concepts if c["id"] in per], links, today)
    return jsonify(to_jsonable({"summary": summary, "practicePlan": plan}))


@bp.route("/api/questions/<qid>/flag", methods=["POST"])
@require_auth
def flag_question(qid):
    """mark a question as wrong/unclear. it's withdrawn from future sessions and its answers stop counting."""
    uid = g.user_id
    ref = _col(uid, "questions").document(qid)
    if not ref.get().exists:
        return bad_request("question not found", 404)
    body = request.get_json(silent=True) or {}
    reason = body.get("reason")
    if reason not in dx.FLAG_REASONS:
        return bad_request(f"reason must be one of {', '.join(dx.FLAG_REASONS)}")
    ref.update({"status": "flagged", "flags": firestore.ArrayUnion([{
        "reason": reason, "note": str(body.get("note") or "")[:500], "at": datetime.now(timezone.utc).isoformat()}])})
    return jsonify({"id": qid, "status": "flagged"})


# -----------------------------------------------------------------------------
# dashboard
# -----------------------------------------------------------------------------
@bp.route("/api/courses/<cid>/diagnostics", methods=["GET"])
@require_auth
def dashboard(cid):
    uid = g.user_id
    if _course(uid, cid) is None:
        return bad_request("course not found", 404)
    today = parse_client_today(request.args.get("today"))
    concepts = course_concepts(uid, cid)
    attempts = course_attempts(uid, cid)
    questions = course_questions(uid, cid)
    mastery = compute_mastery(concepts, attempts, questions)
    links, _ = concept_assessments(uid, cid, concepts)
    groups = {k: [] for k in dx.MASTERY_RULES}
    for c in concepts:
        m = mastery[c["id"]]
        groups[m["status"]].append({"id": c["id"], "name": c["name"], "description": c.get("description", ""),
                                    "objectiveIds": c.get("objectiveIds", []), "origin": c.get("origin"),
                                    "sourceRefs": c.get("sourceRefs", []), "mastery": m,
                                    "assessments": [{"id": a["id"], "title": a["title"], "dueDate": a["dueDate"]} for a in links[c["id"]]]})
    sessions = stream_dicts(_col(uid, "diagnosticSessions").where("courseId", "==", cid))
    sessions.sort(key=lambda s: _ts(s.get("createdAt")), reverse=True)
    recent = [{"id": s["id"], "status": s.get("status"), "createdAt": s.get("createdAt"), "summary": s.get("summary"),
               "assessmentId": s.get("assessmentId")} for s in sessions[:8]]
    mistakes = []
    qmap = {q["id"]: q for q in questions}
    for a in sorted(attempts, key=lambda a: _ts(a.get("createdAt")), reverse=True):
        if dx.attempt_score(a) == 0.0 and a["questionId"] in qmap and qmap[a["questionId"]].get("status", "active") == "active":
            mistakes.append({"questionId": a["questionId"], "prompt": qmap[a["questionId"]]["prompt"][:200],
                             "conceptIds": a.get("conceptIds", []), "at": a.get("createdAt"), "sessionId": a.get("sessionId")})
        if len(mistakes) >= 10:
            break
    return jsonify(to_jsonable({
        "groups": groups, "rules": dx.MASTERY_RULES, "evidenceNotes": dx.EVIDENCE_NOTES,
        "patterns": dx.error_patterns(attempts, questions, concepts),
        "recentSessions": recent, "recentMistakes": mistakes,
        "practicePlan": dx.practice_priorities(mastery, concepts, links, today)[:6],
        "flaggedQuestions": sum(1 for q in questions if q.get("status") == "flagged"),
    }))


@bp.route("/api/diagnostics/mistakes/<qid>", methods=["GET"])
@require_auth
def review_mistake(qid):
    """revisit a question already answered (answer key included, since it was revealed before)."""
    uid = g.user_id
    snap = _col(uid, "questions").document(qid).get()
    if not snap.exists:
        return bad_request("question not found", 404)
    q = {**snap.to_dict(), "id": qid}
    mine = [a for a in stream_dicts(_col(uid, "attempts").where("questionId", "==", qid))]
    if not mine:
        return bad_request("you haven't answered this question yet", 403)
    mine.sort(key=lambda a: _ts(a.get("createdAt")))
    return jsonify(to_jsonable({"question": dx.public_question(q, reveal=True),
                                "attempts": [{k: a.get(k) for k in ("response", "correct", "selfGrade", "confidence", "createdAt")} for a in mine]}))


# -----------------------------------------------------------------------------
# scheduling practice
# -----------------------------------------------------------------------------
def _practice_context(uid, cid, today):
    from routes.planning import load_inputs  # local import avoids a cycle at module load
    course = _course(uid, cid)
    inputs = load_inputs(uid, course.get("termId"))
    av = inputs["availability"]
    horizon = today + timedelta(days=28)
    caps = wl.capacity_by_day(today, horizon, av, inputs["busy"])
    load = {}
    for t in inputs["tasks"]:
        if t["status"] != "done" and t.get("plannedDate") and today <= t["plannedDate"] <= horizon:
            load[t["plannedDate"]] = load.get(t["plannedDate"], 0) + wl.task_remaining(t)[0]
    upcoming = sorted([a for a in inputs["assessments"] if a["courseId"] == cid and a.get("dueDate") and a["dueDate"] >= today
                       and a["status"] != "completed"], key=lambda a: a["dueDate"])
    return caps, load, upcoming


@bp.route("/api/diagnostics/practice/preview", methods=["POST"])
@require_auth
def practice_preview():
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    cid = body.get("courseId")
    if not isinstance(cid, str) or _course(uid, cid) is None:
        return bad_request("course not found", 404)
    today = parse_client_today(body.get("today"))
    concepts = course_concepts(uid, cid)
    if isinstance(body.get("conceptIds"), list) and body["conceptIds"]:
        concepts = [c for c in concepts if c["id"] in body["conceptIds"]]
    mastery = compute_mastery(concepts, course_attempts(uid, cid), course_questions(uid, cid))
    links, _ = concept_assessments(uid, cid, concepts)
    limit = body.get("limit") if isinstance(body.get("limit"), int) and 1 <= body.get("limit") <= 10 else 5
    items = dx.practice_priorities(mastery, concepts, links, today)[:limit]
    caps, load, upcoming = _practice_context(uid, cid, today)
    fallback = upcoming[0] if upcoming else None
    for it in items:
        if it["assessmentId"] is None and fallback:
            it.update({"assessmentId": fallback["id"], "assessmentTitle": fallback["title"],
                       "deadline": fallback["dueDate"].isoformat(), "attachedToNearest": True})
    attachable = [i for i in items if i["assessmentId"]]
    no_home = [{**i, "why": "no upcoming assessment in this course to attach practice to"} for i in items if not i["assessmentId"]]
    last_day = {a["id"]: wl.last_work_day(a) for a in upcoming}
    proposed, unallocated = dx.allocate_practice(attachable, caps, load, last_day)
    return jsonify({"proposed": proposed, "unallocated": unallocated + no_home,
                    "note": "Practice only uses free time left after your existing plan, with at most 2 practice sessions a day."})


@bp.route("/api/diagnostics/practice/apply", methods=["POST"])
@require_auth
def practice_apply():
    uid = g.user_id
    body = request.get_json(silent=True) or {}
    items = body.get("items")
    today = parse_client_today(body.get("today"))
    if not isinstance(items, list) or not items or len(items) > 20:
        return bad_request("send 1–20 practice sessions")
    batch, created = get_db().batch(), []
    for it in items:
        if not isinstance(it, dict):
            return bad_request("malformed practice item")
        csnap = _col(uid, "concepts").document(str(it.get("conceptId"))).get()
        psnap = _col(uid, "projects").document(str(it.get("assessmentId"))).get()
        if not csnap.exists or not psnap.exists or csnap.to_dict()["courseId"] != psnap.to_dict()["courseId"]:
            return bad_request("practice must link a concept to an assessment in the same course", 404)
        try:
            d = datetime.fromisoformat(it.get("date")).date()
        except (TypeError, ValueError):
            return bad_request("each practice session needs a date")
        if d < today or d > today + timedelta(days=366):
            return bad_request("practice dates must be from today onward")
        minutes = it.get("minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 10 <= minutes <= 180:
            return bad_request("practice sessions must be 10–180 minutes")
        concept = csnap.to_dict()
        tid = f"practice-{it['conceptId']}-{d.isoformat()}"
        batch.set(_col(uid, "tasks").document(tid), {
            "projectId": it["assessmentId"], "courseId": concept["courseId"],
            "title": f"Practice: {concept['name']}", "estimatedMinutes": minutes, "timeSpent": None,
            "status": "scheduled", "dueDate": date_to_storage(d), "scheduledDate": None, "scheduledBlockId": None,
            "rescheduleCount": 0, "originalScheduledDate": None, "completedAt": None, "aiGenerated": False,
            "order": 1000, "kind": "practice", "conceptIds": [it["conceptId"]], "dependsOn": [], "pinned": False,
            "origin": "diagnostic", "createdAt": firestore.SERVER_TIMESTAMP,
        })
        created.append(tid)
    batch.commit()
    return jsonify({"created": created}), 201
