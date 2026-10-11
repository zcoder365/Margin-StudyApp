import os
import io
import logging
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv

from firebase_admin import firestore

load_dotenv()

from core import (  # noqa: E402  (load_dotenv must run first)
    get_db, user_doc, require_auth, serialize_timestamps, to_jsonable,
    commit_in_chunks, stream_dicts, as_date,
)
from ai import get_ai, AIError  # noqa: E402
from services.documents import ASSESSMENT_KINDS, validate_review_fields, DocumentError  # noqa: E402
from routes.documents import bp as documents_bp, store_document, delete_document_data  # noqa: E402

log = logging.getLogger("margin")


# -----------------------------------------------------------------------------
# flask app setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-do-not-use-in-prod")
# uploads are capped at 10MB (plus multipart overhead); larger requests get a 413
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
CORS(app, resources={r"/api/*": {"origins": allowed_origins}}, supports_credentials=True)


app.register_blueprint(documents_bp)


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "file is too large — the limit is 10MB"}), 413


# -----------------------------------------------------------------------------
# health
# -----------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "margin-api"})


@app.route("/api/me", methods=["GET"])
@require_auth
def me():
    snap = user_doc(g.user_id).get()
    data = snap.to_dict() if snap.exists else {}
    return jsonify({
        "userId": g.user_id,
        "email": g.user_email,
        "displayName": data.get("displayName", ""),
        "weekStartDay": data.get("weekStartDay", 1),
        "workIntervalMinutes": data.get("workIntervalMinutes", 25),
        "darkMode": data.get("darkMode", False),
    })


@app.route("/api/me", methods=["PATCH"])
@require_auth
def update_me():
    body = request.get_json(silent=True) or {}
    updates = {}
    if "displayName" in body:
        updates["displayName"] = body["displayName"].strip()
    if "weekStartDay" in body and body["weekStartDay"] in (0, 1):
        updates["weekStartDay"] = body["weekStartDay"]
    if "darkMode" in body:
        updates["darkMode"] = bool(body["darkMode"])
    if "workIntervalMinutes" in body:
        val = body["workIntervalMinutes"]
        if isinstance(val, int) and 1 <= val <= 120:
            updates["workIntervalMinutes"] = val
    if not updates:
        return jsonify({"error": "nothing to update"}), 400
    user_doc(g.user_id).update(updates)
    return jsonify({"userId": g.user_id, **updates})


# -----------------------------------------------------------------------------
# user init
# -----------------------------------------------------------------------------
@app.route("/api/users/init", methods=["POST"])
@require_auth
def init_user():
    user_ref = user_doc(g.user_id)
    user_snapshot = user_ref.get()

    if user_snapshot.exists:
        return jsonify({
            "userId": g.user_id,
            "isNewUser": False,
            "user": user_snapshot.to_dict(),
        })

    batch = get_db().batch()
    body = request.get_json(silent=True) or {}
    display_name = body.get("displayName", "")

    term_ref = user_ref.collection("terms").document()
    batch.set(term_ref, {
        "name": "current term",
        "startDate": firestore.SERVER_TIMESTAMP,
        "endDate": None,
        "isActive": True,
    })

    batch.set(user_ref, {
        "email": g.user_email,
        "displayName": display_name,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "currentTermId": term_ref.id,
    })

    batch.commit()

    return jsonify({
        "userId": g.user_id,
        "isNewUser": True,
        "user": {
            "email": g.user_email,
            "displayName": display_name,
            "currentTermId": term_ref.id,
        },
    })


# -----------------------------------------------------------------------------
# cascading deletes
# -----------------------------------------------------------------------------
# collections whose documents carry a courseId and belong to exactly one course
COURSE_SCOPED_COLLECTIONS = (
    "objectives", "concepts", "questions", "attempts", "diagnosticSessions", "studySessions",
)


def delete_project_data(uid, project_id):
    ops = [("delete", d.reference, None)
           for d in user_doc(uid).collection("tasks").where("projectId", "==", project_id).stream()]
    ops.append(("delete", user_doc(uid).collection("projects").document(project_id), None))
    commit_in_chunks(ops)


def delete_course_data(uid, course_id):
    """delete a course and everything that belongs to it (documents, diagnostics, history)."""
    for p in user_doc(uid).collection("projects").where("courseId", "==", course_id).stream():
        delete_project_data(uid, p.id)
    for d in user_doc(uid).collection("documents").where("courseId", "==", course_id).stream():
        delete_document_data(uid, d.id)
    ops = []
    for name in COURSE_SCOPED_COLLECTIONS:
        ops += [("delete", d.reference, None)
                for d in user_doc(uid).collection(name).where("courseId", "==", course_id).stream()]
    grading = user_doc(uid).collection("courses").document(course_id)
    ops += [("delete", d.reference, None) for d in grading.collection("grades").stream()]
    ops.append(("delete", grading.collection("grading").document("scheme"), None))
    ops.append(("delete", grading, None))
    commit_in_chunks(ops)


# -----------------------------------------------------------------------------
# terms
# -----------------------------------------------------------------------------
@app.route("/api/terms", methods=["GET"])
@require_auth
def list_terms():
    user_snap = user_doc(g.user_id).get()
    current_term_id = user_snap.to_dict().get("currentTermId") if user_snap.exists else None
    terms = []
    for doc in user_doc(g.user_id).collection("terms").stream():
        t = doc.to_dict()
        t["id"] = doc.id
        serialize_timestamps(t, ["startDate", "endDate"])
        terms.append(t)
    return jsonify({"terms": terms, "currentTermId": current_term_id})


@app.route("/api/terms", methods=["POST"])
@require_auth
def create_term():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "term name is required"}), 400

    term_ref = user_doc(g.user_id).collection("terms").document()
    term_ref.set({
        "name": name,
        "startDate": firestore.SERVER_TIMESTAMP,
        "endDate": None,
        "isActive": True,
    })

    # update user's currentTermId if they want this to be active
    if body.get("setAsCurrent", False):
        user_doc(g.user_id).update({"currentTermId": term_ref.id})

    return jsonify({"id": term_ref.id, "name": name}), 201


@app.route("/api/terms/<term_id>", methods=["PATCH"])
@require_auth
def update_term(term_id):
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    set_as_current = body.get("setAsCurrent", False)

    if not name and not set_as_current:
        return jsonify({"error": "nothing to update"}), 400

    term_ref = user_doc(g.user_id).collection("terms").document(term_id)
    snap = term_ref.get()
    if not snap.exists:
        return jsonify({"error": "term not found"}), 404

    if name:
        term_ref.update({"name": name})
    if set_as_current:
        user_doc(g.user_id).update({"currentTermId": term_id})

    result = snap.to_dict()
    result["id"] = term_id
    if name:
        result["name"] = name
    return jsonify(result)


@app.route("/api/terms/<term_id>", methods=["DELETE"])
@require_auth
def delete_term(term_id):
    uid = g.user_id

    # cascade: delete every course (and its data) belonging to this term
    courses = list(user_doc(uid).collection("courses").where("termId", "==", term_id).stream())
    for course_doc in courses:
        delete_course_data(uid, course_doc.id)

    user_doc(uid).collection("terms").document(term_id).delete()

    # if this was the current term, clear currentTermId
    user_snap = user_doc(uid).get()
    if user_snap.exists and user_snap.to_dict().get("currentTermId") == term_id:
        user_doc(uid).update({"currentTermId": None})

    return jsonify({"deleted": term_id})


# -----------------------------------------------------------------------------
# courses
# -----------------------------------------------------------------------------
@app.route("/api/courses", methods=["GET"])
@require_auth
def list_courses():
    term_id = request.args.get("termId")
    if term_id == "all":
        courses_ref = user_doc(g.user_id).collection("courses")
    else:
        if not term_id:
            user_snapshot = user_doc(g.user_id).get()
            if not user_snapshot.exists:
                return jsonify({"error": "user not initialized"}), 400
            term_id = user_snapshot.to_dict().get("currentTermId")
        courses_ref = user_doc(g.user_id).collection("courses").where("termId", "==", term_id)
    courses = []
    for doc in courses_ref.stream():
        c = doc.to_dict()
        c["id"] = doc.id
        courses.append(c)

    return jsonify({"courses": courses, "termId": term_id})


@app.route("/api/courses", methods=["POST"])
@require_auth
def create_course():
    body = request.get_json(silent=True) or {}
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "course name is required"}), 400

    color = body.get("color", "#cccccc")

    user_snapshot = user_doc(g.user_id).get()
    if not user_snapshot.exists:
        return jsonify({"error": "user not initialized"}), 400
    term_id = user_snapshot.to_dict().get("currentTermId")

    course_ref = user_doc(g.user_id).collection("courses").document()
    course_ref.set({
        "name": name,
        "color": color,
        "termId": term_id,
        "createdAt": firestore.SERVER_TIMESTAMP,
    })

    return jsonify({"id": course_ref.id, "name": name, "color": color, "termId": term_id}), 201


@app.route("/api/courses/<course_id>", methods=["PATCH"])
@require_auth
def update_course(course_id):
    body = request.get_json(silent=True) or {}
    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    updates = {}
    if "name" in body:
        updates["name"] = body["name"].strip()
    if "color" in body:
        updates["color"] = body["color"]
    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    course_ref.update(updates)
    return jsonify({"id": course_id, **updates})


@app.route("/api/courses/<course_id>", methods=["DELETE"])
@require_auth
def delete_course(course_id):
    uid = g.user_id
    if not user_doc(uid).collection("courses").document(course_id).get().exists:
        return jsonify({"error": "course not found"}), 404
    delete_course_data(uid, course_id)
    return jsonify({"deleted": course_id})


# -----------------------------------------------------------------------------
# projects (assignments)
# -----------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
@require_auth
def list_projects():
    course_id = request.args.get("courseId")
    projects_ref = user_doc(g.user_id).collection("projects")
    if course_id:
        projects_ref = projects_ref.where("courseId", "==", course_id)

    projects = []
    for doc in projects_ref.stream():
        p = doc.to_dict()
        p["id"] = doc.id
        if request.args.get("light") == "1":
            p.pop("extractedText", None)
        projects.append(to_jsonable(p))

    return jsonify({"projects": projects})


@app.route("/api/projects/<project_id>", methods=["GET"])
@require_auth
def get_project(project_id):
    snap = user_doc(g.user_id).collection("projects").document(project_id).get()
    if not snap.exists:
        return jsonify({"error": "project not found"}), 404
    p = snap.to_dict()
    p["id"] = project_id
    return jsonify(to_jsonable(p))


def assessment_field_updates(body):
    """validate the optional assessment fields (kind, weight, estimatedHours) → (updates, error)."""
    fields = {k: body[k] for k in ("kind", "weight", "estimatedHours") if k in body}
    if not fields:
        return {}, None
    probe = {"title": "x", "kind": fields.get("kind", "other"), **{k: v for k, v in fields.items() if k != "kind"}}
    clean, errors = validate_review_fields(probe)
    if errors:
        return None, "; ".join(errors)
    updates = {k: clean[k] for k in fields}
    if "estimatedHours" in fields:
        updates["estimatedHoursOrigin"] = "student" if clean["estimatedHours"] is not None else None
    return updates, None


@app.route("/api/projects", methods=["POST"])
@require_auth
def create_project():
    body = request.get_json(silent=True) or {}
    course_id = body.get("courseId", "").strip()
    title = body.get("title", "").strip()

    if not course_id:
        return jsonify({"error": "courseId is required"}), 400
    if not title:
        return jsonify({"error": "project title is required"}), 400

    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    due_date = None
    if body.get("dueDate"):
        try:
            due_date = datetime.fromisoformat(body["dueDate"])
        except ValueError:
            return jsonify({"error": "dueDate must be ISO format (YYYY-MM-DD)"}), 400

    extra, err = assessment_field_updates(body)
    if err:
        return jsonify({"error": err}), 400

    project_ref = user_doc(g.user_id).collection("projects").document()
    project_data = {
        "courseId": course_id,
        "title": title,
        "dueDate": due_date,
        "extractedText": None,
        "totalEstimatedMinutes": None,
        "status": "active",
        "origin": "manual",
        **extra,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    project_ref.set(project_data)

    row = {k: v for k, v in project_data.items() if k != "createdAt"}
    row["id"] = project_ref.id
    return jsonify(to_jsonable(row)), 201


@app.route("/api/projects/<project_id>", methods=["PATCH"])
@require_auth
def update_project(project_id):
    body = request.get_json(silent=True) or {}
    project_ref = user_doc(g.user_id).collection("projects").document(project_id)
    if not project_ref.get().exists:
        return jsonify({"error": "project not found"}), 404

    updates = {}
    if "title" in body:
        updates["title"] = body["title"].strip()
    if "dueDate" in body:
        try:
            updates["dueDate"] = datetime.fromisoformat(body["dueDate"]) if body["dueDate"] else None
        except ValueError:
            return jsonify({"error": "dueDate must be ISO format"}), 400
    if "status" in body:
        if body["status"] not in ("active", "completed"):
            return jsonify({"error": "status must be active or completed"}), 400
        updates["status"] = body["status"]
        if body["status"] == "completed":
            updates["completedAt"] = firestore.SERVER_TIMESTAMP
        else:
            updates["completedAt"] = None
    extra, err = assessment_field_updates(body)
    if err:
        return jsonify({"error": err}), 400
    updates.update(extra)
    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    project_ref.update(updates)
    fresh = project_ref.get().to_dict()
    fresh["id"] = project_id
    return jsonify(to_jsonable(fresh))


@app.route("/api/projects/<project_id>", methods=["DELETE"])
@require_auth
def delete_project(project_id):
    uid = g.user_id
    if not user_doc(uid).collection("projects").document(project_id).get().exists:
        return jsonify({"error": "project not found"}), 404
    delete_project_data(uid, project_id)
    return jsonify({"deleted": project_id})


# -----------------------------------------------------------------------------
# pdf upload + ai task breakdown
# -----------------------------------------------------------------------------
TASK_BREAKDOWN_PROMPT = (
    "You are a study assistant. Given the following assignment text, "
    "break it down into a list of specific, actionable tasks a student needs to complete. "
    "For each task, estimate how many minutes it will take.\n\n"
    "Respond with ONLY a JSON array. Each element must have:\n"
    "  \"title\": short task description (max 80 chars)\n"
    "  \"estimatedMinutes\": integer\n\n"
    "Assignment text:\n"
)


def normalize_task_breakdown(raw):
    """validate the model's task list; drop malformed entries instead of trusting them."""
    if isinstance(raw, dict):
        raw = raw.get("tasks", [])
    if not isinstance(raw, list):
        return []
    tasks = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()[:120]
        if not title:
            continue
        est = item.get("estimatedMinutes")
        if not isinstance(est, (int, float)) or est <= 0:
            est = 30
        tasks.append({"title": title, "estimatedMinutes": int(min(est, 600))})
    return tasks[:40]


def read_pdf_pages(pdf_bytes):
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return [page.extract_text() or "" for page in reader.pages]


@app.route("/api/projects/<project_id>/upload", methods=["POST"])
@require_auth
def upload_assignment_pdf(project_id):
    project_ref = user_doc(g.user_id).collection("projects").document(project_id)
    project_snapshot = project_ref.get()
    if not project_snapshot.exists:
        return jsonify({"error": "project not found"}), 404

    project_data = project_snapshot.to_dict()

    if "pdf" not in request.files:
        return jsonify({"error": "no pdf file provided — send as multipart field 'pdf'"}), 400

    pdf_file = request.files["pdf"]
    pdf_bytes = pdf_file.read()
    if not pdf_bytes.startswith(b"%PDF-"):
        return jsonify({"error": "that file doesn't look like a PDF"}), 400

    try:
        pages = read_pdf_pages(pdf_bytes)
        extracted_text = "\n\n".join(pages).strip()
    except Exception:
        return jsonify({"error": "could not read that PDF — it may be encrypted or damaged"}), 400

    # keep the original file so it can be viewed again later and processed for requirements
    course_id = project_data.get("courseId")
    course_snap = user_doc(g.user_id).collection("courses").document(course_id).get()
    document_id = None
    if course_snap.exists:
        try:
            document_id, _ = store_document(
                g.user_id, course_snap.to_dict(), course_id, pdf_bytes, pdf_file.filename,
                "pdf", "assignment", assessment_id=project_id)
        except DocumentError:
            document_id = None
        old_id = project_data.get("documentId")
        if document_id and old_id:
            old = user_doc(g.user_id).collection("documents").document(old_id).get()
            # replace the previous upload unless the student already reviewed it
            if old.exists and old.to_dict().get("status") == "stored":
                delete_document_data(g.user_id, old_id)

    total_estimated = 0
    raw_tasks = []
    warning = None

    if extracted_text:
        try:
            raw_tasks = normalize_task_breakdown(get_ai().generate_json(TASK_BREAKDOWN_PROMPT + extracted_text[:12000]))
            if not raw_tasks:
                warning = "the AI didn't return any usable tasks — your existing tasks were kept"
        except AIError as e:
            log.warning("task generation failed: %s", e.kind)
            warning = f"couldn't generate tasks ({e}) — your existing tasks were kept"
        total_estimated = sum(t["estimatedMinutes"] for t in raw_tasks)
    else:
        warning = "no text could be read from this PDF (it may be scanned) — no tasks were generated"

    batch = get_db().batch()
    project_updates = {"extractedText": extracted_text}
    if document_id:
        project_updates["documentId"] = document_id
    if raw_tasks:
        project_updates["totalEstimatedMinutes"] = total_estimated
        # replace the previous ai-generated tasks only once new ones exist; completed ones are kept
        existing_tasks = user_doc(g.user_id).collection("tasks").where("projectId", "==", project_id).stream()
        for doc in existing_tasks:
            d = doc.to_dict()
            if d.get("aiGenerated") and d.get("status") != "done":
                batch.delete(doc.reference)
    batch.update(project_ref, project_updates)

    # create tasks in firestore
    created_tasks = []

    for i, task in enumerate(raw_tasks):
        task_ref = user_doc(g.user_id).collection("tasks").document()
        task_data = {
            "projectId": project_id,
            "courseId": course_id,
            "title": task["title"],
            "estimatedMinutes": task["estimatedMinutes"],
            "timeSpent": None,
            "status": "pending",
            "dueDate": datetime.fromisoformat(datetime.utcnow().date().isoformat()),
            "scheduledDate": None,
            "scheduledBlockId": None,
            "rescheduleCount": 0,
            "originalScheduledDate": None,
            "completedAt": None,
            "aiGenerated": True,
            "order": i,
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
        batch.set(task_ref, task_data)
        row = {k: v for k, v in task_data.items() if k != "createdAt"}
        row["id"] = task_ref.id
        row["dueDate"] = task_data["dueDate"].isoformat()
        created_tasks.append(row)

    batch.commit()

    # return the full task list so the client stays in sync when tasks were kept
    tasks = []
    for doc in user_doc(g.user_id).collection("tasks").where("projectId", "==", project_id).stream():
        t = doc.to_dict()
        t["id"] = doc.id
        serialize_timestamps(t, ["dueDate", "scheduledDate", "originalScheduledDate", "completedAt", "createdAt"])
        tasks.append(t)
    tasks.sort(key=lambda t: t.get("order", 0))

    return jsonify({
        "tasks": tasks,
        "createdTasks": created_tasks,
        "extractedText": extracted_text,
        "totalEstimatedMinutes": total_estimated if raw_tasks else project_data.get("totalEstimatedMinutes"),
        "documentId": document_id,
        "warning": warning,
    }), 201


# -----------------------------------------------------------------------------
# grading
# -----------------------------------------------------------------------------
@app.route("/api/courses/<course_id>/grading", methods=["GET"])
@require_auth
def get_grading(course_id):
    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    scheme_snap = course_ref.collection("grading").document("scheme").get()
    categories = scheme_snap.to_dict().get("categories", []) if scheme_snap.exists else []

    grades = []
    for doc in course_ref.collection("grades").stream():
        entry = doc.to_dict()
        entry["id"] = doc.id
        serialize_timestamps(entry, ["createdAt"])
        grades.append(entry)

    return jsonify({"categories": categories, "grades": grades})


@app.route("/api/courses/<course_id>/grading", methods=["POST"])
@require_auth
def save_grading_scheme(course_id):
    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    body = request.get_json(silent=True) or {}
    categories = body.get("categories", [])

    for cat in categories:
        if not cat.get("name") or cat.get("weight") is None:
            return jsonify({"error": "each category needs a name and weight"}), 400

    course_ref.collection("grading").document("scheme").set({"categories": categories})
    return jsonify({"categories": categories})


@app.route("/api/courses/<course_id>/grades", methods=["POST"])
@require_auth
def add_grade(course_id):
    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    body = request.get_json(silent=True) or {}
    category_id = body.get("categoryId", "").strip()
    name = body.get("name", "").strip()
    score = body.get("score")
    total = body.get("total")

    if not category_id or not name:
        return jsonify({"error": "categoryId and name are required"}), 400
    if score is None or total is None:
        return jsonify({"error": "score and total are required"}), 400

    try:
        score, total = float(score), float(total)
    except (TypeError, ValueError):
        return jsonify({"error": "score and total must be numbers"}), 400
    if total <= 0:
        return jsonify({"error": "total must be greater than 0"}), 400

    grade_ref = course_ref.collection("grades").document()
    grade_data = {
        "categoryId": category_id,
        "name": name,
        "score": score,
        "total": total,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    grade_ref.set(grade_data)

    row = {k: v for k, v in grade_data.items() if k != "createdAt"}
    row["id"] = grade_ref.id
    return jsonify(row), 201


@app.route("/api/courses/<course_id>/grades/<grade_id>", methods=["PATCH"])
@require_auth
def update_grade(course_id, grade_id):
    grade_ref = (
        user_doc(g.user_id).collection("courses").document(course_id)
        .collection("grades").document(grade_id)
    )
    if not grade_ref.get().exists:
        return jsonify({"error": "grade not found"}), 404

    body = request.get_json(silent=True) or {}
    updates = {}
    if "name" in body:
        updates["name"] = body["name"].strip()
    try:
        if "score" in body:
            updates["score"] = float(body["score"])
        if "total" in body:
            updates["total"] = float(body["total"])
    except (TypeError, ValueError):
        return jsonify({"error": "score and total must be numbers"}), 400
    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    grade_ref.update(updates)
    fresh = grade_ref.get().to_dict()
    fresh["id"] = grade_id
    return jsonify(fresh)


@app.route("/api/courses/<course_id>/grades/<grade_id>", methods=["DELETE"])
@require_auth
def delete_grade(course_id, grade_id):
    grade_ref = (
        user_doc(g.user_id).collection("courses").document(course_id)
        .collection("grades").document(grade_id)
    )
    if not grade_ref.get().exists:
        return jsonify({"error": "grade not found"}), 404
    grade_ref.delete()
    return jsonify({"deleted": grade_id})


# -----------------------------------------------------------------------------
# tasks
# -----------------------------------------------------------------------------
@app.route("/api/tasks", methods=["GET"])
@require_auth
def list_tasks():
    project_id = request.args.get("projectId")
    tasks_ref = user_doc(g.user_id).collection("tasks")
    if project_id:
        tasks_ref = tasks_ref.where("projectId", "==", project_id)
    tasks_ref = tasks_ref.order_by("order")

    tasks = []
    for doc in tasks_ref.stream():
        t = doc.to_dict()
        t["id"] = doc.id
        serialize_timestamps(t, ["dueDate", "scheduledDate", "originalScheduledDate", "completedAt", "createdAt"])
        tasks.append(t)

    return jsonify({"tasks": tasks})


@app.route("/api/tasks", methods=["POST"])
@require_auth
def create_task():
    body = request.get_json(silent=True) or {}
    project_id = body.get("projectId", "").strip()
    title = body.get("title", "").strip()

    if not project_id:
        return jsonify({"error": "projectId is required"}), 400
    if not title:
        return jsonify({"error": "task title is required"}), 400

    project_ref = user_doc(g.user_id).collection("projects").document(project_id)
    project_snapshot = project_ref.get()
    if not project_snapshot.exists:
        return jsonify({"error": "project not found"}), 404

    course_id = project_snapshot.to_dict().get("courseId")

    order = body.get("order")
    if order is None:
        existing_count = len(list(
            user_doc(g.user_id).collection("tasks").where("projectId", "==", project_id).stream()
        ))
        order = existing_count

    task_ref = user_doc(g.user_id).collection("tasks").document()
    raw_due = body.get("dueDate") or datetime.utcnow().date().isoformat()
    try:
        due_date_val = datetime.fromisoformat(raw_due)
    except ValueError:
        return jsonify({"error": "dueDate must be ISO format (YYYY-MM-DD)"}), 400

    task_data = {
        "projectId": project_id,
        "courseId": course_id,
        "title": title,
        "estimatedMinutes": body.get("estimatedMinutes", 30),
        "timeSpent": None,
        "status": "pending",
        "dueDate": due_date_val,
        "scheduledDate": None,
        "scheduledBlockId": None,
        "rescheduleCount": 0,
        "originalScheduledDate": None,
        "completedAt": None,
        "aiGenerated": body.get("aiGenerated", False),
        "order": order,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    task_ref.set(task_data)

    row = {k: v for k, v in task_data.items() if k != "createdAt"}
    row["id"] = task_ref.id
    row["dueDate"] = due_date_val.isoformat()
    return jsonify(row), 201


@app.route("/api/tasks/<task_id>", methods=["PATCH"])
@require_auth
def update_task(task_id):
    body = request.get_json(silent=True) or {}

    task_ref = user_doc(g.user_id).collection("tasks").document(task_id)
    task_snapshot = task_ref.get()
    if not task_snapshot.exists:
        return jsonify({"error": "task not found"}), 404

    current_data = task_snapshot.to_dict()
    updates = {}

    if "title" in body:
        updates["title"] = body["title"].strip()

    if "estimatedMinutes" in body:
        val = body["estimatedMinutes"]
        if val is not None and (not isinstance(val, (int, float)) or val <= 0):
            return jsonify({"error": "estimatedMinutes must be a positive number"}), 400
        updates["estimatedMinutes"] = val

    if "status" in body:
        new_status = body["status"]
        if new_status not in ("pending", "scheduled", "done"):
            return jsonify({"error": "invalid status — must be pending, scheduled, or done"}), 400
        updates["status"] = new_status
        if new_status == "done":
            updates["completedAt"] = firestore.SERVER_TIMESTAMP

    if "timeSpent" in body:
        # timeSpent is in minutes; null means not yet recorded
        val = body["timeSpent"]
        if val is not None and (not isinstance(val, (int, float)) or val < 0):
            return jsonify({"error": "timeSpent must be a non-negative number (minutes)"}), 400
        updates["timeSpent"] = val

    if "dueDate" in body:
        try:
            new_date = datetime.fromisoformat(body["dueDate"]) if body["dueDate"] else None
        except ValueError:
            return jsonify({"error": "dueDate must be ISO format"}), 400
        updates["dueDate"] = new_date

    if "scheduledDate" in body:
        try:
            new_date = datetime.fromisoformat(body["scheduledDate"]) if body["scheduledDate"] else None
        except ValueError:
            return jsonify({"error": "scheduledDate must be ISO format"}), 400

        existing_date = current_data.get("scheduledDate")
        if existing_date and new_date and existing_date != new_date:
            updates["rescheduleCount"] = current_data.get("rescheduleCount", 0) + 1
        elif not existing_date and new_date:
            updates["originalScheduledDate"] = new_date

        updates["scheduledDate"] = new_date

    if "scheduledBlockId" in body:
        updates["scheduledBlockId"] = body["scheduledBlockId"]

    if not updates:
        return jsonify({"error": "no valid fields to update"}), 400

    task_ref.update(updates)

    fresh = task_ref.get().to_dict()
    fresh["id"] = task_id
    serialize_timestamps(fresh, ["dueDate", "scheduledDate", "originalScheduledDate", "completedAt", "createdAt"])
    return jsonify(fresh)


@app.route("/api/tasks/<task_id>", methods=["DELETE"])
@require_auth
def delete_task(task_id):
    task_ref = user_doc(g.user_id).collection("tasks").document(task_id)
    if not task_ref.get().exists:
        return jsonify({"error": "task not found"}), 404
    task_ref.delete()
    return jsonify({"deleted": task_id})


# -----------------------------------------------------------------------------
# schedule (user's weekly busy blocks)
# -----------------------------------------------------------------------------
@app.route("/api/schedule", methods=["GET"])
@require_auth
def get_schedule():
    snap = user_doc(g.user_id).collection("schedule").document("weekly").get()
    if not snap.exists:
        return jsonify({"blocks": []})
    return jsonify(snap.to_dict())


@app.route("/api/schedule", methods=["POST"])
@require_auth
def save_schedule():
    body = request.get_json(silent=True) or {}
    blocks = body.get("blocks", [])

    for block in blocks:
        if not all(k in block for k in ("day", "startTime", "endTime")):
            return jsonify({"error": "each block needs 'day' (0-6), 'startTime', and 'endTime' (HH:MM)"}), 400

    user_doc(g.user_id).collection("schedule").document("weekly").set({"blocks": blocks})
    return jsonify({"blocks": blocks})


# -----------------------------------------------------------------------------
# auto-schedule: assign due dates to tasks based on user schedule + due date
# -----------------------------------------------------------------------------
@app.route("/api/projects/<project_id>/auto-schedule", methods=["POST"])
@require_auth
def auto_schedule(project_id):
    project_ref = user_doc(g.user_id).collection("projects").document(project_id)
    project_snapshot = project_ref.get()
    if not project_snapshot.exists:
        return jsonify({"error": "project not found"}), 404

    project_data = project_snapshot.to_dict()
    raw_due = project_data.get("dueDate")
    if not raw_due:
        return jsonify({"error": "project needs a dueDate for auto-scheduling"}), 400

    due_date = raw_due.date() if hasattr(raw_due, "date") else raw_due

    # get pending/scheduled tasks for this project, in order
    tasks_snap = (
        user_doc(g.user_id).collection("tasks")
        .where("projectId", "==", project_id)
        .order_by("order")
        .stream()
    )
    tasks = [{"id": doc.id, **doc.to_dict()} for doc in tasks_snap]
    pending = [t for t in tasks if t.get("status") != "done"]

    if not pending:
        return jsonify({"tasks": [], "message": "no pending tasks to schedule"})

    # load user's schedule to find busy days
    schedule_snap = user_doc(g.user_id).collection("schedule").document("weekly").get()
    busy_blocks = schedule_snap.to_dict().get("blocks", []) if schedule_snap.exists else []

    # compute busy hours per weekday (0=Mon … 6=Sun)
    busy_hours_per_day = {i: 0.0 for i in range(7)}
    for block in busy_blocks:
        try:
            day = int(block["day"])
            start_h = int(block["startTime"].split(":")[0]) + int(block["startTime"].split(":")[1]) / 60
            end_h = int(block["endTime"].split(":")[0]) + int(block["endTime"].split(":")[1]) / 60
            busy_hours_per_day[day] += max(0.0, end_h - start_h)
        except (KeyError, ValueError):
            pass

    # a day is available if it has at least 2 free hours (out of a reasonable 8h study window)
    STUDY_WINDOW_HOURS = 8.0
    MIN_FREE_HOURS = 2.0

    today = datetime.now().date()
    start_date = min(today, due_date)  # if due date has already passed, start from due date

    # collect available dates from start_date to due_date (inclusive)
    available_dates = []
    current = start_date
    while current <= due_date:
        weekday = current.weekday()  # 0=Mon, 6=Sun
        free_hours = STUDY_WINDOW_HOURS - busy_hours_per_day.get(weekday, 0)
        if free_hours >= MIN_FREE_HOURS:
            available_dates.append((current, free_hours))
        current += timedelta(days=1)

    # if user is completely blocked every day, fall back to just using the due date
    if not available_dates:
        available_dates = [(due_date, MIN_FREE_HOURS)]

    # distribute tasks across available days, weighted by free hours
    # we go forward in time so the student starts early
    # fill each day's capacity before moving to the next
    MINUTES_PER_FREE_HOUR = 45  # assume 45 productive minutes per free hour

    day_iter = iter(available_dates)
    current_day, current_free = next(day_iter)
    current_capacity = current_free * MINUTES_PER_FREE_HOUR

    batch = get_db().batch()
    updated_tasks = []

    for task in pending:
        est = task.get("estimatedMinutes") or 30
        task_ref = user_doc(g.user_id).collection("tasks").document(task["id"])

        batch.update(task_ref, {
            "dueDate": datetime.combine(current_day, datetime.min.time()),
            "status": "scheduled",
        })
        updated_tasks.append({
            **{k: v for k, v in task.items() if k not in ("createdAt", "completedAt")},
            "dueDate": current_day.isoformat(),
            "status": "scheduled",
        })

        current_capacity -= est
        # if this day is full, advance to next available day
        if current_capacity < MIN_FREE_HOURS * MINUTES_PER_FREE_HOUR * 0.25:
            try:
                current_day, current_free = next(day_iter)
                current_capacity = current_free * MINUTES_PER_FREE_HOUR
            except StopIteration:
                # no more days — remaining tasks pile onto the due date
                current_day = due_date
                current_capacity = float("inf")

    batch.commit()
    return jsonify({"tasks": updated_tasks})


# -----------------------------------------------------------------------------
# calendar
# -----------------------------------------------------------------------------
@app.route("/api/calendar", methods=["GET"])
@require_auth
def get_calendar():
    uid = g.user_id
    term_id = request.args.get("termId")
    if not term_id:
        user_snap = user_doc(uid).get()
        term_id = user_snap.to_dict().get("currentTermId") if user_snap.exists else None
    if not term_id:
        return jsonify({"tasks": [], "deadlines": []})

    # courses for this term
    courses = {}
    for doc in user_doc(uid).collection("courses").where("termId", "==", term_id).stream():
        courses[doc.id] = {**doc.to_dict(), "id": doc.id}

    # projects for those courses
    projects = {}
    for course_id in courses:
        for doc in user_doc(uid).collection("projects").where("courseId", "==", course_id).stream():
            projects[doc.id] = {**doc.to_dict(), "id": doc.id, "courseId": course_id}

    # tasks for those projects, enriched with course data
    result = []
    for project_id, project in projects.items():
        course = courses[project["courseId"]]
        for doc in user_doc(uid).collection("tasks").where("projectId", "==", project_id).stream():
            t = doc.to_dict()
            t["id"] = doc.id
            t["courseId"] = project["courseId"]
            t["courseName"] = course.get("name", "")
            t["courseColor"] = course.get("color", "#ccc")
            t["projectTitle"] = project.get("title", "")
            result.append(to_jsonable(t))

    # assessment deadlines and milestones (confirmed data only) for the planner
    deadlines = []
    for project_id, project in projects.items():
        course = courses[project["courseId"]]
        base = {"projectId": project_id, "courseId": project["courseId"], "courseName": course.get("name", ""),
                "courseColor": course.get("color", "#ccc"), "projectTitle": project.get("title", ""),
                "kind": project.get("kind"), "status": project.get("status", "active")}
        due = as_date(project.get("dueDate"))
        if due:
            deadlines.append({**base, "type": "deadline", "title": project.get("title", ""), "date": due.isoformat()})
        for m in project.get("milestones") or []:
            md = as_date(m.get("dueDate"))
            if md:
                deadlines.append({**base, "type": "milestone", "title": m.get("title", ""), "date": md.isoformat()})

    return jsonify({"tasks": result, "deadlines": deadlines})


# -----------------------------------------------------------------------------
# entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # the werkzeug debugger allows code execution, so only enable it explicitly for local dev
    debug = os.environ.get("FLASK_DEBUG") == "1" or os.environ.get("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
