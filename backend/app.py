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
# entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # debug=True gives us auto-reload on file changes + nice tracebacks in dev
    # NEVER set debug=True in production (it exposes a python REPL via the error page)
    app.run(host="0.0.0.0", port=port, debug=True)