"""
margin — flask backend
======================
entry point for the api. handles firebase auth verification, firestore reads/writes,
and anthropic claude calls for pdf → task breakdown.
"""

import os
import re
import base64
import json
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv

import anthropic
import firebase_admin
from firebase_admin import credentials, auth, firestore

load_dotenv()


# -----------------------------------------------------------------------------
# firebase admin SDK initialization
# -----------------------------------------------------------------------------
def init_firebase():
    creds_value = os.environ.get("FIREBASE_CREDENTIALS")
    if not creds_value:
        raise RuntimeError("FIREBASE_CREDENTIALS env var is not set.")

    if os.path.isfile(creds_value):
        cred = credentials.Certificate(creds_value)
    else:
        try:
            decoded = base64.b64decode(creds_value).decode("utf-8")
            cred_dict = json.loads(decoded)
            cred = credentials.Certificate(cred_dict)
        except Exception as e:
            raise RuntimeError(f"FIREBASE_CREDENTIALS is neither a valid file path nor valid base64 json: {e}")

    firebase_admin.initialize_app(cred)


init_firebase()
db = firestore.client()

# anthropic client for pdf → task breakdown
anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


# -----------------------------------------------------------------------------
# flask app setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-do-not-use-in-prod")

allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
CORS(app, resources={r"/api/*": {"origins": allowed_origins}}, supports_credentials=True)


# -----------------------------------------------------------------------------
# auth decorator
# -----------------------------------------------------------------------------
def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "missing or malformed Authorization header"}), 401

        id_token = auth_header.split("Bearer ", 1)[1].strip()

        try:
            decoded = auth.verify_id_token(id_token)
            g.user_id = decoded["uid"]
            g.user_email = decoded.get("email")
        except auth.InvalidIdTokenError:
            return jsonify({"error": "invalid id token"}), 401
        except auth.ExpiredIdTokenError:
            return jsonify({"error": "id token expired, please re-authenticate"}), 401
        except Exception as e:
            return jsonify({"error": f"auth failed: {str(e)}"}), 401

        return f(*args, **kwargs)
    return decorated


# -----------------------------------------------------------------------------
# helpers
# -----------------------------------------------------------------------------
def user_doc(user_id):
    return db.collection("users").document(user_id)


def serialize_timestamps(data, fields):
    """convert firestore timestamp fields to ISO strings for JSON serialization."""
    for field in fields:
        if data.get(field):
            val = data[field]
            if hasattr(val, "isoformat"):
                data[field] = val.isoformat()
    return data


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
    return jsonify({"userId": g.user_id, "email": g.user_email, "displayName": data.get("displayName", "")})


@app.route("/api/me", methods=["PATCH"])
@require_auth
def update_me():
    body = request.get_json(silent=True) or {}
    updates = {}
    if "displayName" in body:
        updates["displayName"] = body["displayName"].strip()
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

    batch = db.batch()
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

    # cascade: delete all tasks, projects, and courses belonging to this term
    courses = list(user_doc(uid).collection("courses").where("termId", "==", term_id).stream())
    for course_doc in courses:
        cid = course_doc.id
        projects = list(user_doc(uid).collection("projects").where("courseId", "==", cid).stream())
        for project_doc in projects:
            for task_doc in user_doc(uid).collection("tasks").where("projectId", "==", project_doc.id).stream():
                task_doc.reference.delete()
            project_doc.reference.delete()
        course_doc.reference.delete()

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
    projects = list(user_doc(uid).collection("projects").where("courseId", "==", course_id).stream())
    for project_doc in projects:
        for task_doc in user_doc(uid).collection("tasks").where("projectId", "==", project_doc.id).stream():
            task_doc.reference.delete()
        project_doc.reference.delete()

    user_doc(uid).collection("courses").document(course_id).delete()
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
        serialize_timestamps(p, ["dueDate", "createdAt"])
        projects.append(p)

    return jsonify({"projects": projects})


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

    project_ref = user_doc(g.user_id).collection("projects").document()
    project_data = {
        "courseId": course_id,
        "title": title,
        "dueDate": due_date,
        "extractedText": None,
        "totalEstimatedMinutes": None,
        "status": "active",
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    project_ref.set(project_data)

    return jsonify({
        "id": project_ref.id,
        "courseId": course_id,
        "title": title,
        "dueDate": due_date.isoformat() if due_date else None,
        "status": "active",
        "extractedText": None,
        "totalEstimatedMinutes": None,
    }), 201


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
    if not updates:
        return jsonify({"error": "nothing to update"}), 400

    project_ref.update(updates)
    fresh = project_ref.get().to_dict()
    fresh["id"] = project_id
    serialize_timestamps(fresh, ["dueDate", "createdAt"])
    return jsonify(fresh)


@app.route("/api/projects/<project_id>", methods=["DELETE"])
@require_auth
def delete_project(project_id):
    uid = g.user_id
    for task_doc in user_doc(uid).collection("tasks").where("projectId", "==", project_id).stream():
        task_doc.reference.delete()
    user_doc(uid).collection("projects").document(project_id).delete()
    return jsonify({"deleted": project_id})


# -----------------------------------------------------------------------------
# pdf upload + claude task breakdown
# -----------------------------------------------------------------------------
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

    # extract text with pypdf — no AI needed for now
    import io
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = [page.extract_text() or "" for page in reader.pages]
        extracted_text = "\n\n".join(pages).strip()
    except Exception as e:
        return jsonify({"error": f"could not read pdf: {str(e)}"}), 500

    total_estimated = 0
    raw_tasks = []  # user adds tasks manually for now

    # delete any existing ai-generated tasks for this project before adding new ones
    existing_tasks = user_doc(g.user_id).collection("tasks").where("projectId", "==", project_id).where("aiGenerated", "==", True).stream()
    delete_batch = db.batch()
    for doc in existing_tasks:
        delete_batch.delete(doc.reference)
    delete_batch.commit()

    # save extracted text + estimate to project
    project_ref.update({
        "extractedText": extracted_text,
        "totalEstimatedMinutes": total_estimated,
    })

    # create tasks in firestore
    course_id = project_data.get("courseId")
    create_batch = db.batch()
    created_tasks = []

    for i, task in enumerate(raw_tasks):
        task_ref = user_doc(g.user_id).collection("tasks").document()
        task_data = {
            "projectId": project_id,
            "courseId": course_id,
            "title": task.get("title", f"Task {i + 1}"),
            "estimatedMinutes": task.get("estimatedMinutes", 30),
            "timeSpent": None,
            "status": "pending",
            "dueDate": None,
            "scheduledDate": None,
            "scheduledBlockId": None,
            "rescheduleCount": 0,
            "originalScheduledDate": None,
            "completedAt": None,
            "aiGenerated": True,
            "order": i,
            "createdAt": firestore.SERVER_TIMESTAMP,
        }
        create_batch.set(task_ref, task_data)
        row = {k: v for k, v in task_data.items() if k != "createdAt"}
        row["id"] = task_ref.id
        created_tasks.append(row)

    create_batch.commit()

    return jsonify({
        "tasks": created_tasks,
        "extractedText": extracted_text,
        "totalEstimatedMinutes": total_estimated,
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

    grade_ref = course_ref.collection("grades").document()
    grade_data = {
        "categoryId": category_id,
        "name": name,
        "score": float(score),
        "total": float(total),
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
    if "score" in body:
        updates["score"] = float(body["score"])
    if "total" in body:
        updates["total"] = float(body["total"])
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
    task_data = {
        "projectId": project_id,
        "courseId": course_id,
        "title": title,
        "estimatedMinutes": body.get("estimatedMinutes", 30),
        "timeSpent": None,
        "status": "pending",
        "dueDate": None,
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

    batch = db.batch()
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
        return jsonify({"tasks": []})

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
            serialize_timestamps(t, ["createdAt"])
            result.append(t)

    return jsonify({"tasks": result})


# -----------------------------------------------------------------------------
# entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
