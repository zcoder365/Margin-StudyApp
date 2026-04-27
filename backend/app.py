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
# entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    # debug=True gives us auto-reload on file changes + nice tracebacks in dev
    # NEVER set debug=True in production (it exposes a python REPL via the error page)
    app.run(host="0.0.0.0", port=port, debug=True)