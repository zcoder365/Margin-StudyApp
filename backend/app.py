"""
margin — flask backend
======================
entry point for the api. handles firebase auth verification, firestore reads/writes,
and (later) anthropic claude calls for pdf → task breakdown.
"""

import os
import base64
import json
from functools import wraps

from flask import Flask, request, jsonify, g
from flask_cors import CORS
from dotenv import load_dotenv

import firebase_admin
from firebase_admin import credentials, auth, firestore

# load .env into environment vars (FIREBASE_CREDENTIALS, ANTHROPIC_API_KEY, etc.)
load_dotenv()


# -----------------------------------------------------------------------------
# firebase admin SDK initialization
# -----------------------------------------------------------------------------
# we support two ways of providing creds:
#   1. local dev: FIREBASE_CREDENTIALS points to a json file path
#   2. production (railway): FIREBASE_CREDENTIALS is the full json, base64-encoded
#      (railway env vars are single-line, so we encode to dodge newline issues)

def init_firebase():
    """initialize the firebase admin SDK once, at app startup."""
    creds_value = os.environ.get("FIREBASE_CREDENTIALS")
    if not creds_value:
        raise RuntimeError("FIREBASE_CREDENTIALS env var is not set. check your .env file.")

    # if the value looks like a file path that exists, load from disk (local dev)
    if os.path.isfile(creds_value):
        cred = credentials.Certificate(creds_value)
    else:
        # otherwise assume it's base64-encoded json (production)
        try:
            decoded = base64.b64decode(creds_value).decode("utf-8")
            cred_dict = json.loads(decoded)
            cred = credentials.Certificate(cred_dict)
        except Exception as e:
            raise RuntimeError(f"FIREBASE_CREDENTIALS is neither a valid file path nor valid base64 json: {e}")

    firebase_admin.initialize_app(cred)


init_firebase()
db = firestore.client()  # firestore client, used everywhere for reads/writes


# -----------------------------------------------------------------------------
# flask app setup
# -----------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-do-not-use-in-prod")

# CORS: allow the frontend origins listed in .env to make requests
# (during dev this is http://localhost:3000, in prod it's your render/railway domain)
allowed_origins = os.environ.get("ALLOWED_ORIGINS", "").split(",")
CORS(app, resources={r"/api/*": {"origins": allowed_origins}}, supports_credentials=True)


# -----------------------------------------------------------------------------
# auth decorator — verifies firebase ID tokens on protected routes
# -----------------------------------------------------------------------------
def require_auth(f):
    """
    decorator that checks for a valid firebase ID token in the Authorization header.
    if valid, attaches the decoded user info to flask's `g` object so route handlers
    can access g.user_id and g.user_email.

    expected header format: "Authorization: Bearer <id_token>"
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        # header must be present and start with "Bearer "
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "missing or malformed Authorization header"}), 401

        # extract just the token part (everything after "Bearer ")
        id_token = auth_header.split("Bearer ", 1)[1].strip()

        try:
            # firebase admin verifies the signature, expiration, and issuer for us 🔒
            decoded = auth.verify_id_token(id_token)
            g.user_id = decoded["uid"]
            g.user_email = decoded.get("email")
        except auth.InvalidIdTokenError:
            return jsonify({"error": "invalid id token"}), 401
        except auth.ExpiredIdTokenError:
            return jsonify({"error": "id token expired, please re-authenticate"}), 401
        except Exception as e:
            # catch-all for any other firebase auth errors
            return jsonify({"error": f"auth failed: {str(e)}"}), 401

        return f(*args, **kwargs)
    return decorated


# -----------------------------------------------------------------------------
# routes
# -----------------------------------------------------------------------------
@app.route("/api/health", methods=["GET"])
def health():
    """simple health check — useful for confirming the server is up. no auth required."""
    return jsonify({"status": "ok", "service": "margin-api"})


@app.route("/api/me", methods=["GET"])
@require_auth
def me():
    """returns the authenticated user's basic info. tests that auth is wired up."""
    return jsonify({
        "userId": g.user_id,
        "email": g.user_email,
    })

# -----------------------------------------------------------------------------
# helper: get user doc reference
# -----------------------------------------------------------------------------
def user_doc(user_id):
    """shorthand for users/{userId} doc reference. saves us from typing this everywhere."""
    return db.collection("users").document(user_id)


# -----------------------------------------------------------------------------
# user routes
# -----------------------------------------------------------------------------
@app.route("/api/users/init", methods=["POST"])
@require_auth
def init_user():
    """
    called by the frontend after a successful google sign-in.
    creates the user's profile doc + a default term IF they don't already exist.
    idempotent — safe to call on every login (won't overwrite existing data).
    """
    user_ref = user_doc(g.user_id)
    user_snapshot = user_ref.get()

    # if user already exists, just return their current state (no overwriting)
    if user_snapshot.exists:
        return jsonify({
            "userId": g.user_id,
            "isNewUser": False,
            "user": user_snapshot.to_dict(),
        })

    # otherwise this is a brand new user — set up their account
    # use a batch write so user creation + default term creation happen atomically
    # (either both succeed or neither does — no half-set-up accounts 🔒)
    batch = db.batch()

    # parse displayName from the request body if provided (frontend sends this from google)
    body = request.get_json(silent=True) or {}
    display_name = body.get("displayName", "")

    # create the default term first so we can reference its id in the user doc
    term_ref = user_ref.collection("terms").document()  # auto-generates an id
    batch.set(term_ref, {
        "name": "current term",
        "startDate": firestore.SERVER_TIMESTAMP,  # user can edit later
        "endDate": None,
        "isActive": True,
    })

    # now create the user doc, pointing currentTermId at the term we just made
    batch.set(user_ref, {
        "email": g.user_email,
        "displayName": display_name,
        "createdAt": firestore.SERVER_TIMESTAMP,
        "currentTermId": term_ref.id,
    })

    # commit both writes atomically
    batch.commit()

    # return the freshly-created user state to the frontend
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
# course routes
# -----------------------------------------------------------------------------
@app.route("/api/courses", methods=["GET"])
@require_auth
def list_courses():
    """
    returns all courses for the current user, optionally filtered by termId.
    query param: ?termId=xxx (optional — defaults to current term)
    """
    # figure out which term to filter by
    term_id = request.args.get("termId")
    if not term_id:
        # default to user's current term
        user_snapshot = user_doc(g.user_id).get()
        if not user_snapshot.exists:
            return jsonify({"error": "user not initialized — call /api/users/init first"}), 400
        term_id = user_snapshot.to_dict().get("currentTermId")

    # query courses subcollection where termId matches
    courses_ref = user_doc(g.user_id).collection("courses").where("termId", "==", term_id)
    courses = []
    for doc in courses_ref.stream():
        course_data = doc.to_dict()
        course_data["id"] = doc.id  # include the doc id so frontend can reference it
        courses.append(course_data)

    return jsonify({"courses": courses, "termId": term_id})


@app.route("/api/courses", methods=["POST"])
@require_auth
def create_course():
    """
    creates a new course for the current user, in their current term.
    body: { "name": "COMP 307", "color": "#a8d5ba" }
    """
    body = request.get_json(silent=True) or {}

    # validate required fields — fail fast with a helpful error
    name = body.get("name", "").strip()
    if not name:
        return jsonify({"error": "course name is required"}), 400

    color = body.get("color", "#cccccc")  # default gray if no color provided

    # get the user's current term to attach this course to
    user_snapshot = user_doc(g.user_id).get()
    if not user_snapshot.exists:
        return jsonify({"error": "user not initialized — call /api/users/init first"}), 400
    term_id = user_snapshot.to_dict().get("currentTermId")

    # create the course doc with auto-generated id
    course_ref = user_doc(g.user_id).collection("courses").document()
    course_data = {
        "name": name,
        "color": color,
        "termId": term_id,
        "createdAt": firestore.SERVER_TIMESTAMP,
    }
    course_ref.set(course_data)

    # return the created course (with its id) so frontend can update its state
    return jsonify({
        "id": course_ref.id,
        "name": name,
        "color": color,
        "termId": term_id,
    }), 201
    
# -----------------------------------------------------------------------------
# project routes
# -----------------------------------------------------------------------------
@app.route("/api/projects", methods=["GET"])
@require_auth
def list_projects():
    """
    returns all projects for the current user, optionally filtered by courseId.
    query param: ?courseId=xxx (optional — without it, returns ALL projects)
    """
    course_id = request.args.get("courseId")

    # build the query — if courseId is provided, filter; otherwise get everything
    projects_ref = user_doc(g.user_id).collection("projects")
    if course_id:
        projects_ref = projects_ref.where("courseId", "==", course_id)

    projects = []
    for doc in projects_ref.stream():
        project_data = doc.to_dict()
        project_data["id"] = doc.id

        # firestore returns timestamps as DatetimeWithNanoseconds objects, which
        # don't serialize to JSON cleanly. convert them to ISO strings here.
        # (we'll need this pattern for tasks too — could refactor into a helper later)
        if project_data.get("dueDate"):
            project_data["dueDate"] = project_data["dueDate"].isoformat()
        if project_data.get("createdAt"):
            project_data["createdAt"] = project_data["createdAt"].isoformat()

        projects.append(project_data)

    return jsonify({"projects": projects})


@app.route("/api/projects", methods=["POST"])
@require_auth
def create_project():
    """
    creates a new project under a course.
    body: { "courseId": "xxx", "title": "midterm project", "dueDate": "2026-05-15" (optional) }
    """
    body = request.get_json(silent=True) or {}

    course_id = body.get("courseId", "").strip()
    title = body.get("title", "").strip()

    # validate required fields
    if not course_id:
        return jsonify({"error": "courseId is required"}), 400
    if not title:
        return jsonify({"error": "project title is required"}), 400

    # verify the course actually belongs to this user — paranoid but cheap.
    # without this check, a malicious frontend could create projects under
    # courseIds that don't exist (or worse, that belong to other users in
    # some future schema variant). always validate foreign keys 🔒
    course_ref = user_doc(g.user_id).collection("courses").document(course_id)
    if not course_ref.get().exists:
        return jsonify({"error": "course not found"}), 404

    # parse due date if provided. expecting ISO format like "2026-05-15"
    due_date = None
    if body.get("dueDate"):
        from datetime import datetime
        try:
            due_date = datetime.fromisoformat(body["dueDate"])
        except ValueError:
            return jsonify({"error": "dueDate must be ISO format (YYYY-MM-DD)"}), 400

    # create the project doc
    project_ref = user_doc(g.user_id).collection("projects").document()
    project_data = {
        "courseId": course_id,
        "title": title,
        "dueDate": due_date,             # None if not provided, that's fine
        "sourcePdfUrl": None,            # populated later when pdf upload is built
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
    }), 201

# -----------------------------------------------------------------------------
# task routes
# -----------------------------------------------------------------------------
@app.route("/api/tasks", methods=["GET"])
@require_auth
def list_tasks():
    """
    returns tasks, optionally filtered by projectId.
    query param: ?projectId=xxx (optional)
    """
    project_id = request.args.get("projectId")

    tasks_ref = user_doc(g.user_id).collection("tasks")
    if project_id:
        tasks_ref = tasks_ref.where("projectId", "==", project_id)

    # order by the `order` field so tasks render in the user's chosen order
    # (or AI's order, on first generation). tie-break by createdAt for stability.
    tasks_ref = tasks_ref.order_by("order")

    tasks = []
    for doc in tasks_ref.stream():
        task_data = doc.to_dict()
        task_data["id"] = doc.id

        # serialize all the timestamp fields to ISO strings for JSON
        for ts_field in ("scheduledDate", "originalScheduledDate", "completedAt", "createdAt"):
            if task_data.get(ts_field):
                task_data[ts_field] = task_data[ts_field].isoformat()

        tasks.append(task_data)

    return jsonify({"tasks": tasks})


@app.route("/api/tasks", methods=["POST"])
@require_auth
def create_task():
    """
    creates a new task under a project.
    body: { "projectId": "xxx", "title": "outline intro", "order": 0 (optional) }
    """
    body = request.get_json(silent=True) or {}

    project_id = body.get("projectId", "").strip()
    title = body.get("title", "").strip()

    if not project_id:
        return jsonify({"error": "projectId is required"}), 400
    if not title:
        return jsonify({"error": "task title is required"}), 400

    # fetch the project to (1) verify it exists for this user and (2) grab
    # its courseId so we can denormalize it onto the task (per our schema design)
    project_ref = user_doc(g.user_id).collection("projects").document(project_id)
    project_snapshot = project_ref.get()
    if not project_snapshot.exists:
        return jsonify({"error": "project not found"}), 404

    course_id = project_snapshot.to_dict().get("courseId")

    # if `order` isn't provided, put this task at the end of the list.
    # we do this by counting existing tasks for this project — simple but works
    # at v1 scale (don't do this with millions of tasks lol)
    order = body.get("order")
    if order is None:
        existing_count = len(list(
            user_doc(g.user_id).collection("tasks").where("projectId", "==", project_id).stream()
        ))
        order = existing_count

    task_ref = user_doc(g.user_id).collection("tasks").document()
    task_data = {
        "projectId": project_id,
        "courseId": course_id,           # denormalized for color-coding
        "title": title,
        "status": "pending",
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

    return jsonify({
        "id": task_ref.id,
        **{k: v for k, v in task_data.items() if k != "createdAt"},  # skip the sentinel
    }), 201


@app.route("/api/tasks/<task_id>", methods=["PATCH"])
@require_auth
def update_task(task_id):
    """
    updates a task's status, title, or scheduling info.
    body: any subset of { "status", "title", "scheduledDate", "scheduledBlockId" }
    """
    body = request.get_json(silent=True) or {}

    task_ref = user_doc(g.user_id).collection("tasks").document(task_id)
    task_snapshot = task_ref.get()
    if not task_snapshot.exists:
        return jsonify({"error": "task not found"}), 404

    current_data = task_snapshot.to_dict()
    updates = {}

    # only update fields that are actually in the request body. avoids accidentally
    # blanking out fields when the frontend sends a partial update.
    if "title" in body:
        updates["title"] = body["title"].strip()

    if "status" in body:
        new_status = body["status"]
        if new_status not in ("pending", "scheduled", "done"):
            return jsonify({"error": "invalid status"}), 400
        updates["status"] = new_status

        # if marking as done, set the completedAt timestamp
        if new_status == "done":
            updates["completedAt"] = firestore.SERVER_TIMESTAMP

    if "scheduledDate" in body:
        from datetime import datetime
        try:
            new_date = datetime.fromisoformat(body["scheduledDate"]) if body["scheduledDate"] else None
        except ValueError:
            return jsonify({"error": "scheduledDate must be ISO format"}), 400

        # rescheduling logic — if there's already a scheduledDate AND we're changing it,
        # increment rescheduleCount. this is what powers the gentle "moved 2x" nudge 💛
        existing_date = current_data.get("scheduledDate")
        if existing_date and new_date and existing_date != new_date:
            updates["rescheduleCount"] = current_data.get("rescheduleCount", 0) + 1
        elif not existing_date and new_date:
            # first time being scheduled — record the original date for sentimental purposes
            updates["originalScheduledDate"] = new_date

        updates["scheduledDate"] = new_date

    if "scheduledBlockId" in body:
        updates["scheduledBlockId"] = body["scheduledBlockId"]

    if not updates:
        return jsonify({"error": "no valid fields to update"}), 400

    task_ref.update(updates)

    # return the fresh state so the frontend can update its UI
    fresh = task_ref.get().to_dict()
    fresh["id"] = task_id
    for ts_field in ("scheduledDate", "originalScheduledDate", "completedAt", "createdAt"):
        if fresh.get(ts_field):
            fresh[ts_field] = fresh[ts_field].isoformat()
    return jsonify(fresh)

# -----------------------------------------------------------------------------
# entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # debug=True gives us auto-reload on file changes + nice tracebacks in dev
    # NEVER set debug=True in production (it exposes a python REPL via the error page)
    app.run(host="0.0.0.0", port=port, debug=True)