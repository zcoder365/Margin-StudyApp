"""
document intelligence api: upload → background extraction → review → confirm.

firestore layout (all under users/{uid}):
  documents/{docId}                 metadata, status, extraction (for review), applied map
  documents/{docId}/pages/{n}       extracted page text (source references point here)
  documents/{docId}/chunks/{i}      original file bytes in <1MB pieces
  objectives/{id}                   confirmed learning objectives
  projects/{id}                     assessments (existing "projects" collection, extended)

nothing reaches projects/objectives until the student confirms it in review.
confirmed items get deterministic ids ({docId}-{itemKey}) so re-applying or
reprocessing a document updates instead of duplicating.
"""

import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, request, jsonify, g, Response
from firebase_admin import firestore

from core import (
    get_db, user_doc, require_auth, bad_request, stream_dicts, to_jsonable,
    as_date, date_to_storage, commit_in_chunks, run_job, parse_client_today,
)
from ai import get_ai, AIError
from services import documents as docsvc

log = logging.getLogger("margin.documents")
bp = Blueprint("documents", __name__)

CHUNK_BYTES = 900_000
STALE_PROCESSING = timedelta(minutes=5)


def _course(uid, course_id):
    snap = user_doc(uid).collection("courses").document(course_id).get()
    return snap.to_dict() if snap.exists else None


def _doc_ref(uid, doc_id):
    return user_doc(uid).collection("documents").document(doc_id)


def _public_doc(d, include_extraction=False):
    out = {k: v for k, v in d.items() if k not in ("extraction", "extractionRaw")}
    if include_extraction:
        out["extraction"] = d.get("extraction")
    return to_jsonable(out)


def existing_assessments(uid, course_id):
    rows = []
    for p in stream_dicts(user_doc(uid).collection("projects").where("courseId", "==", course_id)):
        d = as_date(p.get("dueDate"))
        rows.append({
            "id": p["id"], "title": p.get("title", ""), "kind": p.get("kind"),
            "dueDate": d.isoformat() if d else None,
            "sourceDocumentId": p.get("sourceDocumentId"), "sourceKey": p.get("sourceKey"),
        })
    return rows


# -----------------------------------------------------------------------------
# storing documents
# -----------------------------------------------------------------------------
def store_document(uid, course, course_id, data, filename, kind, doc_type, assessment_id=None, status="stored"):
    """write the document, its page text and original bytes. returns (doc_id, pages)."""
    pages = docsvc.extract_pages(data, kind)
    ref = user_doc(uid).collection("documents").document()
    chunks = [data[i:i + CHUNK_BYTES] for i in range(0, len(data), CHUNK_BYTES)]
    meta = {
        "courseId": course_id,
        "termId": course.get("termId"),
        "assessmentId": assessment_id,
        "docType": doc_type,
        "filename": (filename or "document")[:200],
        "contentKind": kind,
        "sizeBytes": len(data),
        "sha256": docsvc.sha256(data),
        "pageCount": len(pages),
        "chunkCount": len(chunks),
        "hasText": any(p.strip() for p in pages),
        "status": status,
        "error": None,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    ops = [("set", ref, meta)]
    for i, p in enumerate(pages, start=1):
        ops.append(("set", ref.collection("pages").document(str(i)), {"n": i, "text": p}))
    for i, c in enumerate(chunks):
        ops.append(("set", ref.collection("chunks").document(str(i)), {"i": i, "data": c}))
    # metadata goes last so a half-written upload never shows up as a complete document
    commit_in_chunks(ops[1:])
    commit_in_chunks(ops[:1])
    return ref.id, pages


def _load_pages(uid, doc_id):
    rows = stream_dicts(_doc_ref(uid, doc_id).collection("pages"))
    rows.sort(key=lambda r: r.get("n", 0))
    return [r.get("text", "") for r in rows]


def delete_document_data(uid, doc_id):
    ref = _doc_ref(uid, doc_id)
    ops = [("delete", s.reference, None) for s in ref.collection("pages").stream()]
    ops += [("delete", s.reference, None) for s in ref.collection("chunks").stream()]
    ops.append(("delete", ref, None))
    commit_in_chunks(ops)


@bp.route("/api/courses/<course_id>/documents", methods=["POST"])
@require_auth
def upload_document(course_id):
    uid = g.user_id
    course = _course(uid, course_id)
    if course is None:
        return bad_request("course not found", 404)

    f = request.files.get("file")
    if f is None:
        return bad_request("no file provided — send it as multipart field 'file'")
    data = f.read()
    if not data:
        return bad_request("the file is empty")
    if len(data) > docsvc.MAX_UPLOAD_BYTES:
        return bad_request("file is too large — the limit is 10MB", 413)

    kind = docsvc.sniff_kind(data, f.filename)
    if kind is None:
        return bad_request("unsupported file type — upload a PDF or a .txt/.md file")

    doc_type = request.form.get("docType", "other")
    if doc_type not in docsvc.DOC_TYPES:
        return bad_request("unknown document type")

    assessment_id = request.form.get("assessmentId") or None
    if assessment_id:
        snap = user_doc(uid).collection("projects").document(assessment_id).get()
        if not snap.exists or snap.to_dict().get("courseId") != course_id:
            return bad_request("assessment not found in this course", 404)

    try:
        doc_id, pages = store_document(uid, course, course_id, data, f.filename, kind, doc_type, assessment_id)
    except docsvc.DocumentError as e:
        return bad_request(str(e))

    extract = request.form.get("extract", "1") == "1"
    if extract:
        if not any(p.strip() for p in pages):
            _doc_ref(uid, doc_id).update({
                "status": "failed",
                "error": "no text could be read from this file (it may be a scanned image). You can still keep it for reference.",
                "errorKind": "no_text",
            })
        else:
            _start_extraction(uid, doc_id, force=False, today=request.form.get("today"))

    snap = _doc_ref(uid, doc_id).get()
    return jsonify({**_public_doc(snap.to_dict()), "id": doc_id}), 202


@bp.route("/api/courses/<course_id>/documents", methods=["GET"])
@require_auth
def list_documents(course_id):
    uid = g.user_id
    if _course(uid, course_id) is None:
        return bad_request("course not found", 404)
    docs = [_public_doc(d) for d in stream_dicts(user_doc(uid).collection("documents").where("courseId", "==", course_id))]
    docs.sort(key=lambda d: d.get("createdAt") or "", reverse=True)
    return jsonify({"documents": docs})


@bp.route("/api/documents/<doc_id>", methods=["GET"])
@require_auth
def get_document(doc_id):
    snap = _doc_ref(g.user_id, doc_id).get()
    if not snap.exists:
        return bad_request("document not found", 404)
    d = snap.to_dict()
    d["id"] = doc_id
    return jsonify(_public_doc(d, include_extraction=True))


@bp.route("/api/documents/<doc_id>/pages/<int:n>", methods=["GET"])
@require_auth
def get_document_page(doc_id, n):
    ref = _doc_ref(g.user_id, doc_id)
    if not ref.get().exists:
        return bad_request("document not found", 404)
    snap = ref.collection("pages").document(str(n)).get()
    if not snap.exists:
        return bad_request("page not found", 404)
    return jsonify({"n": n, "text": snap.to_dict().get("text", "")})


@bp.route("/api/documents/<doc_id>/file", methods=["GET"])
@require_auth
def get_document_file(doc_id):
    ref = _doc_ref(g.user_id, doc_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("document not found", 404)
    meta = snap.to_dict()
    rows = stream_dicts(ref.collection("chunks"))
    rows.sort(key=lambda r: r.get("i", 0))
    data = b"".join(bytes(r.get("data") or b"") for r in rows)
    if len(rows) != meta.get("chunkCount", len(rows)):
        return bad_request("the stored file is incomplete", 500)
    mimetype = "application/pdf" if meta.get("contentKind") == "pdf" else "text/plain; charset=utf-8"
    return Response(data, mimetype=mimetype, headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": "inline",
    })


@bp.route("/api/documents/<doc_id>", methods=["DELETE"])
@require_auth
def delete_document(doc_id):
    uid = g.user_id
    if not _doc_ref(uid, doc_id).get().exists:
        return bad_request("document not found", 404)
    delete_document_data(uid, doc_id)
    # confirmed assessments/objectives belong to the student and are kept; unlink the file
    for p in stream_dicts(user_doc(uid).collection("projects").where("documentId", "==", doc_id)):
        user_doc(uid).collection("projects").document(p["id"]).update({"documentId": None})
    return jsonify({"deleted": doc_id})


# -----------------------------------------------------------------------------
# extraction
# -----------------------------------------------------------------------------
def _start_extraction(uid, doc_id, force, today=None):
    _doc_ref(uid, doc_id).update({
        "status": "processing",
        "error": None,
        "errorKind": None,
        "processingStartedAt": firestore.SERVER_TIMESTAMP,
    })
    run_job(run_extraction, uid, doc_id, force, today)


def run_extraction(uid, doc_id, force=False, today=None):
    """background job: pages → model → normalized, annotated extraction awaiting review."""
    ref = _doc_ref(uid, doc_id)
    try:
        meta = ref.get().to_dict()
        if meta is None:
            return
        pages = _load_pages(uid, doc_id)
        course = _course(uid, meta["courseId"]) or {}
        term_name = ""
        if course.get("termId"):
            t = user_doc(uid).collection("terms").document(course["termId"]).get()
            term_name = (t.to_dict() or {}).get("name", "") if t.exists else ""
        hint = None
        if meta.get("assessmentId"):
            p = user_doc(uid).collection("projects").document(meta["assessmentId"]).get()
            hint = (p.to_dict() or {}).get("title") if p.exists else None

        raw, cached_from = None, None
        if not force:
            # reuse a previous model response for an identical file instead of paying for it again
            for other in stream_dicts(user_doc(uid).collection("documents").where("sha256", "==", meta["sha256"])):
                if (other["id"] != doc_id and other.get("extractionRaw") is not None
                        and other.get("docType") == meta.get("docType")
                        and other.get("promptVersion") == docsvc.PROMPT_VERSION):
                    raw, cached_from = other["extractionRaw"], other["id"]
                    break
        if raw is None:
            prompt = docsvc.build_extraction_prompt(
                meta.get("docType"), pages, course.get("name", ""), term_name,
                today=parse_client_today(today), assessment_hint=hint)
            raw = get_ai().generate_json(prompt)

        extraction = docsvc.normalize_extraction(raw, pages)
        docsvc.detect_issues(extraction, existing_assessments(uid, meta["courseId"]), document_id=doc_id)
        if not extraction["assessments"] and not extraction["objectives"]:
            extraction["warnings"].append({
                "code": "nothing_found", "severity": "info", "field": None,
                "message": "no assessments or learning objectives were found in this document",
            })
        extraction["generatedAt"] = datetime.now(timezone.utc).isoformat()
        extraction["promptVersion"] = docsvc.PROMPT_VERSION
        ref.update({
            "status": "needs_review",
            "extraction": extraction,
            "extractionRaw": raw,
            "extractionCachedFrom": cached_from,
            "promptVersion": docsvc.PROMPT_VERSION,
            "processedAt": firestore.SERVER_TIMESTAMP,
            "error": None,
            "errorKind": None,
        })
    except AIError as e:
        ref.update({"status": "failed", "error": str(e), "errorKind": e.kind})
    except docsvc.DocumentError as e:
        ref.update({"status": "failed", "error": str(e), "errorKind": "malformed"})
    except Exception:
        log.exception("extraction job crashed for a document")
        try:
            ref.update({"status": "failed", "error": "something went wrong while processing this document", "errorKind": "error"})
        except Exception:
            pass


@bp.route("/api/documents/<doc_id>/process", methods=["POST"])
@require_auth
def process_document(doc_id):
    uid = g.user_id
    snap = _doc_ref(uid, doc_id).get()
    if not snap.exists:
        return bad_request("document not found", 404)
    meta = snap.to_dict()
    if not meta.get("hasText"):
        return bad_request("no text could be read from this file, so there is nothing to extract")
    started = meta.get("processingStartedAt")
    if meta.get("status") == "processing" and started and datetime.now(timezone.utc) - started < STALE_PROCESSING:
        return bad_request("this document is already being processed", 409)
    body = request.get_json(silent=True) or {}
    _start_extraction(uid, doc_id, force=bool(body.get("force")), today=body.get("today"))
    d = _doc_ref(uid, doc_id).get().to_dict()
    d["id"] = doc_id
    return jsonify(_public_doc(d)), 202


# -----------------------------------------------------------------------------
# review + confirm
# -----------------------------------------------------------------------------
def _merge_review(extraction, body):
    """apply the student's edits (by item key) onto the stored extraction.

    only items that exist in the stored extraction can be edited; unknown keys are
    ignored, so the client can't inject arbitrary records.
    """
    errors = []
    by_key = {a["key"]: a for a in extraction.get("assessments", [])}
    for edit in body.get("assessments", []) or []:
        item = by_key.get(edit.get("key"))
        if item is None:
            continue
        if edit.get("decision") in ("pending", "confirm", "reject"):
            item["decision"] = edit["decision"]
        if edit.get("action") in ("create", "update"):
            item["action"] = edit["action"]
        if "targetId" in edit:
            item["targetId"] = edit["targetId"] or None
        if isinstance(edit.get("fields"), dict):
            merged = {**item["fields"], **edit["fields"]}
            clean, errs = docsvc.validate_review_fields(merged)
            if errs and item["decision"] == "confirm":
                errors.append(f"\"{merged.get('title') or 'untitled'}\": {'; '.join(errs)}")
            before_hours = item["fields"].get("estimatedHours")
            item["fields"] = {**item["fields"], **clean}
            if "estimatedHours" in edit["fields"] and item["fields"].get("estimatedHours") != before_hours:
                # a number the student typed is theirs, not the document's or the ai's
                item["estimatedHoursOrigin"] = "student" if item["fields"].get("estimatedHours") is not None else None
        for coll in ("requirements", "milestones"):
            for sub in edit.get(coll, []) or []:
                for target in item.get(coll, []):
                    if target["key"] == sub.get("key"):
                        if "include" in sub:
                            target["include"] = bool(sub["include"])
                        if coll == "milestones" and "dueDate" in sub:
                            d = docsvc.parse_iso_date(sub["dueDate"] or "")
                            if sub["dueDate"] and not d:
                                errors.append(f"milestone \"{target['title']}\": date must be YYYY-MM-DD")
                            target["dueDate"] = d.isoformat() if d else None

    obj_by_key = {o["key"]: o for o in extraction.get("objectives", [])}
    for edit in body.get("objectives", []) or []:
        item = obj_by_key.get(edit.get("key"))
        if item is None:
            continue
        if edit.get("decision") in ("pending", "confirm", "reject"):
            item["decision"] = edit["decision"]
        if isinstance(edit.get("text"), str) and edit["text"].strip():
            item["text"] = edit["text"].strip()[:500]
        if isinstance(edit.get("assessmentKeys"), list):
            item["assessmentKeys"] = [k for k in edit["assessmentKeys"] if k in by_key]
    return errors


@bp.route("/api/documents/<doc_id>/review", methods=["PATCH"])
@require_auth
def save_review(doc_id):
    uid = g.user_id
    ref = _doc_ref(uid, doc_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("document not found", 404)
    meta = snap.to_dict()
    extraction = meta.get("extraction")
    if not extraction:
        return bad_request("this document has no extraction to review")
    _merge_review(extraction, request.get_json(silent=True) or {})
    ref.update({"extraction": extraction})
    return jsonify({"extraction": to_jsonable(extraction)})


def _assessment_payload(item, doc_id):
    f = item["fields"]
    src = item.get("source") or {}
    return {
        "title": f["title"],
        "kind": f["kind"],
        "dueDate": date_to_storage(docsvc.parse_iso_date(f["dueDate"] or "")),
        "weight": f.get("weight"),
        "estimatedHours": f.get("estimatedHours"),
        "estimatedHoursOrigin": item.get("estimatedHoursOrigin") if f.get("estimatedHours") is not None else None,
        "requirements": [
            {"key": r["key"], "text": r["text"], "source": r.get("source")}
            for r in item.get("requirements", []) if r.get("include", True)
        ],
        "milestones": [
            {"key": m["key"], "title": m["title"], "dueDate": m.get("dueDate"), "dueDateText": m.get("dueDateText"), "source": m.get("source")}
            for m in item.get("milestones", []) if m.get("include", True)
        ],
        "deliverables": item.get("deliverables", []),
        "prerequisites": item.get("prerequisites", []),
        "source": {"documentId": doc_id, "page": src.get("page"), "quote": src.get("quote"), "verified": bool(src.get("verified"))},
        "sourceDocumentId": doc_id,
        "sourceKey": item["key"],
        "verification": "confirmed",
        "confirmedAt": firestore.SERVER_TIMESTAMP,
    }


@bp.route("/api/documents/<doc_id>/apply", methods=["POST"])
@require_auth
def apply_review(doc_id):
    """save the student's confirmed items. safe to retry: ids are deterministic."""
    uid = g.user_id
    ref = _doc_ref(uid, doc_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("document not found", 404)
    meta = snap.to_dict()
    extraction = meta.get("extraction")
    if not extraction or meta.get("status") not in ("needs_review", "reviewed"):
        return bad_request("this document has nothing ready to confirm")
    course_id = meta["courseId"]

    errors = _merge_review(extraction, request.get_json(silent=True) or {})

    projects = user_doc(uid).collection("projects")
    confirmed = [a for a in extraction["assessments"] if a["decision"] == "confirm"]
    key_to_project = {}
    ops, created, updated = [], [], []
    for item in confirmed:
        clean, errs = docsvc.validate_review_fields(item["fields"])
        if errs:
            errors.append(f"\"{item['fields'].get('title') or 'untitled'}\": {'; '.join(errs)}")
            continue
        item["fields"] = clean
        payload = _assessment_payload(item, doc_id)
        if item["action"] == "update":
            target_id = item.get("targetId")
            tsnap = projects.document(target_id).get() if target_id else None
            if tsnap is None or not tsnap.exists or tsnap.to_dict().get("courseId") != course_id:
                errors.append(f"\"{clean['title']}\": the assessment to update no longer exists — choose 'create new' instead")
                continue
            ops.append(("merge", projects.document(target_id), payload))
            key_to_project[item["key"]] = target_id
            updated.append(target_id)
        else:
            pid = f"{doc_id}-{item['key']}"
            pref = projects.document(pid)
            if pref.get().exists:
                updated.append(pid)
            else:
                payload.update({
                    "courseId": course_id, "status": "active", "extractedText": None,
                    "totalEstimatedMinutes": None, "createdAt": firestore.SERVER_TIMESTAMP,
                    "origin": "document",
                })
                created.append(pid)
            ops.append(("merge", pref, payload))
            key_to_project[item["key"]] = pid

    # assessments applied in an earlier run still resolve for objective links
    for k, pid in (meta.get("applied") or {}).items():
        key_to_project.setdefault(k, pid)

    objectives_saved = []
    for o in extraction.get("objectives", []):
        if o["decision"] != "confirm":
            continue
        oid = f"{doc_id}-{o['key']}"
        src = o.get("source") or {}
        ops.append(("merge", user_doc(uid).collection("objectives").document(oid), {
            "courseId": course_id,
            "text": o["text"],
            "assessmentIds": [key_to_project[k] for k in o.get("assessmentKeys", []) if k in key_to_project],
            "source": {"documentId": doc_id, "page": src.get("page"), "quote": src.get("quote"), "verified": bool(src.get("verified"))},
            "origin": "document",
            "status": "confirmed",
            "updatedAt": firestore.SERVER_TIMESTAMP,
        }))
        objectives_saved.append(oid)

    if errors:
        # nothing is written unless every confirmed item is valid
        ref.update({"extraction": extraction})
        return jsonify({"error": "some confirmed items need fixing first", "details": errors}), 400

    if len(ops) > 450:
        return bad_request("too many items to confirm at once — confirm them in smaller groups")

    applied = dict(meta.get("applied") or {})
    applied.update(key_to_project)
    pending = sum(1 for a in extraction["assessments"] if a["decision"] == "pending") + \
        sum(1 for o in extraction["objectives"] if o["decision"] == "pending")
    ops.append(("update", ref, {
        "extraction": extraction,
        "applied": applied,
        "status": "reviewed",
        "pendingItems": pending,
        "reviewedAt": firestore.SERVER_TIMESTAMP,
    }))
    # one atomic batch: either every confirmed item is saved or none are
    batch = get_db().batch()
    for kind, r, data in ops:
        if kind == "merge":
            batch.set(r, data, merge=True)
        else:
            batch.update(r, data)
    batch.commit()

    return jsonify({
        "created": created, "updated": updated, "objectives": objectives_saved,
        "rejected": sum(1 for a in extraction["assessments"] if a["decision"] == "reject"),
        "pending": pending,
    })


# -----------------------------------------------------------------------------
# learning objectives
# -----------------------------------------------------------------------------
@bp.route("/api/courses/<course_id>/objectives", methods=["GET"])
@require_auth
def list_objectives(course_id):
    uid = g.user_id
    if _course(uid, course_id) is None:
        return bad_request("course not found", 404)
    rows = [to_jsonable(o) for o in stream_dicts(user_doc(uid).collection("objectives").where("courseId", "==", course_id))]
    rows.sort(key=lambda o: o.get("text", "").lower())
    return jsonify({"objectives": rows})


def _valid_assessment_ids(uid, course_id, ids):
    if not isinstance(ids, list):
        return None
    out = []
    for pid in ids[:20]:
        if not isinstance(pid, str):
            return None
        s = user_doc(uid).collection("projects").document(pid).get()
        if not s.exists or s.to_dict().get("courseId") != course_id:
            return None
        out.append(pid)
    return out


@bp.route("/api/courses/<course_id>/objectives", methods=["POST"])
@require_auth
def create_objective(course_id):
    uid = g.user_id
    if _course(uid, course_id) is None:
        return bad_request("course not found", 404)
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    if not text:
        return bad_request("objective text is required")
    ids = _valid_assessment_ids(uid, course_id, body.get("assessmentIds", []))
    if ids is None:
        return bad_request("assessmentIds must reference assessments in this course")
    ref = user_doc(uid).collection("objectives").document()
    data = {"courseId": course_id, "text": text[:500], "assessmentIds": ids, "source": None,
            "origin": "student", "status": "confirmed", "updatedAt": firestore.SERVER_TIMESTAMP}
    ref.set(data)
    return jsonify(to_jsonable({**data, "id": ref.id, "updatedAt": None})), 201


@bp.route("/api/objectives/<objective_id>", methods=["PATCH"])
@require_auth
def update_objective(objective_id):
    uid = g.user_id
    ref = user_doc(uid).collection("objectives").document(objective_id)
    snap = ref.get()
    if not snap.exists:
        return bad_request("objective not found", 404)
    cur = snap.to_dict()
    body = request.get_json(silent=True) or {}
    updates = {}
    if "text" in body:
        text = (body.get("text") or "").strip()
        if not text:
            return bad_request("objective text can't be empty")
        updates["text"] = text[:500]
    if "assessmentIds" in body:
        ids = _valid_assessment_ids(uid, cur["courseId"], body["assessmentIds"])
        if ids is None:
            return bad_request("assessmentIds must reference assessments in this course")
        updates["assessmentIds"] = ids
    if not updates:
        return bad_request("nothing to update")
    updates["updatedAt"] = firestore.SERVER_TIMESTAMP
    ref.update(updates)
    fresh = ref.get().to_dict()
    fresh["id"] = objective_id
    return jsonify(to_jsonable(fresh))


@bp.route("/api/objectives/<objective_id>", methods=["DELETE"])
@require_auth
def delete_objective(objective_id):
    ref = user_doc(g.user_id).collection("objectives").document(objective_id)
    if not ref.get().exists:
        return bad_request("objective not found", 404)
    ref.delete()
    return jsonify({"deleted": objective_id})
