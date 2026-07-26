import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict, deque
from contextlib import contextmanager
from functools import wraps
from threading import Lock

from flask import Flask, abort, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix


app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
WEB3FORMS_ACCESS_KEY = os.environ.get("WEB3FORMS_ACCESS_KEY", "").strip()
WEB3FORMS_ENDPOINT = "https://api.web3forms.com/submit"
TURNSTILE_SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
TURNSTILE_SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
TURNSTILE_VERIFY_ENDPOINT = (
    "https://challenges.cloudflare.com/turnstile/v0/siteverify"
)
SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(app.root_path, "data", "whisper.db")
)
ROOM_TTL_MS = 24 * 60 * 60 * 1000
FEEDBACK_TTL_MS = 30 * 24 * 60 * 60 * 1000
MAX_MESSAGES_PER_ROOM = 500

_rate_buckets = defaultdict(deque)
_rate_lock = Lock()
_rate_salt = secrets.token_bytes(32)


def now_ms():
    return int(time.time() * 1000)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def rate_limit(limit, window_seconds):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            address = request.remote_addr or "unknown"
            visitor = hashlib.sha256(
                _rate_salt + address.encode("utf-8")
            ).hexdigest()
            key = (function.__name__, visitor)
            current = time.monotonic()
            cutoff = current - window_seconds

            with _rate_lock:
                bucket = _rate_buckets[key]
                while bucket and bucket[0] <= cutoff:
                    bucket.popleft()

                if len(bucket) >= limit:
                    retry_after = max(1, math.ceil(bucket[0] + window_seconds - current))
                    response = jsonify(
                        {"error": "Too many requests. Please try again shortly."}
                    )
                    response.status_code = 429
                    response.headers["Retry-After"] = str(retry_after)
                    return response

                bucket.append(current)

                if len(_rate_buckets) > 10_000:
                    stale_keys = [
                        bucket_key
                        for bucket_key, timestamps in _rate_buckets.items()
                        if not timestamps or timestamps[-1] <= current - 3600
                    ][:1000]
                    for stale_key in stale_keys:
                        _rate_buckets.pop(stale_key, None)

            return function(*args, **kwargs)

        return wrapped

    return decorator


@contextmanager
def database():
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row

        connection = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
        connection = sqlite3.connect(SQLITE_PATH)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def execute(connection, sql, parameters=()):
    if IS_POSTGRES:
        sql = sql.replace("?", "%s")
    return connection.execute(sql, parameters)


def init_database():
    room_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    message_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    report_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    feedback_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    with database() as connection:
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS rooms (
                id {room_id},
                code VARCHAR(8) NOT NULL UNIQUE,
                help_text VARCHAR(500) NOT NULL,
                owner_token_hash VARCHAR(64) NOT NULL,
                created_at BIGINT NOT NULL
            )
            """,
        )
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS messages (
                id {message_id},
                room_code VARCHAR(8) NOT NULL,
                sender VARCHAR(40) NOT NULL,
                text VARCHAR(1000) NOT NULL,
                created_at BIGINT NOT NULL,
                FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
            )
            """,
        )
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS reports (
                id {report_id},
                room_code VARCHAR(8) NOT NULL,
                created_at BIGINT NOT NULL,
                FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
            )
            """,
        )
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS feedback (
                id {feedback_id},
                text VARCHAR(1000) NOT NULL,
                created_at BIGINT NOT NULL
            )
            """,
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS messages_room_code_id "
            "ON messages(room_code, id)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS rooms_created_at ON rooms(created_at)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS feedback_created_at ON feedback(created_at)",
        )


def purge_expired_content(connection):
    room_cutoff = now_ms() - ROOM_TTL_MS
    feedback_cutoff = now_ms() - FEEDBACK_TTL_MS
    execute(
        connection,
        """
        DELETE FROM reports
        WHERE room_code IN (SELECT code FROM rooms WHERE created_at < ?)
        """,
        (room_cutoff,),
    )
    execute(
        connection,
        """
        DELETE FROM messages
        WHERE room_code IN (SELECT code FROM rooms WHERE created_at < ?)
        """,
        (room_cutoff,),
    )
    execute(connection, "DELETE FROM rooms WHERE created_at < ?", (room_cutoff,))
    execute(connection, "DELETE FROM feedback WHERE created_at < ?", (feedback_cutoff,))


def clean_text(value, maximum):
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def deliver_feedback(text):
    if not WEB3FORMS_ACCESS_KEY:
        raise RuntimeError("Web3Forms is not configured.")

    payload = json.dumps(
        {
            "access_key": WEB3FORMS_ACCESS_KEY,
            "subject": "New feedback for Whisper",
            "from_name": "Whisper",
            "message": text,
        }
    ).encode("utf-8")
    web3forms_request = urllib.request.Request(
        WEB3FORMS_ENDPOINT,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Whisper/1.0",
        },
        method="POST",
    )

    # The URL is a fixed HTTPS constant; no user input can select the destination.
    with urllib.request.urlopen(web3forms_request, timeout=10) as response:  # nosec B310
        result = json.loads(response.read(64 * 1024))

    if not isinstance(result, dict) or result.get("success") is not True:
        raise RuntimeError("Web3Forms rejected the submission.")


def verify_turnstile(token, expected_hostname):
    if not TURNSTILE_SECRET_KEY:
        raise RuntimeError("Turnstile is not configured.")

    payload = urllib.parse.urlencode(
        {
            "secret": TURNSTILE_SECRET_KEY,
            "response": token,
        }
    ).encode("utf-8")
    turnstile_request = urllib.request.Request(
        TURNSTILE_VERIFY_ENDPOINT,
        data=payload,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Whisper/1.0",
        },
        method="POST",
    )

    # The URL is a fixed HTTPS constant; no user input can select the destination.
    with urllib.request.urlopen(turnstile_request, timeout=10) as response:  # nosec B310
        result = json.loads(response.read(64 * 1024))

    if not isinstance(result, dict) or result.get("success") is not True:
        return False
    if result.get("action") != "feedback":
        return False

    verified_hostname = str(result.get("hostname", "")).lower()
    return hmac.compare_digest(verified_hostname, expected_hostname.lower())


def row_dict(row):
    return dict(row) if row is not None else None


def public_room(row):
    room = row_dict(row)
    return {
        "code": room["code"],
        "help": room["help_text"],
        "createdAt": room["created_at"],
    }


def public_message(row):
    message = row_dict(row)
    return {
        "id": message["id"],
        "sender": message["sender"],
        "text": message["text"],
        "createdAt": message["created_at"],
    }


@app.after_request
def add_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://challenges.cloudflare.com; "
        "style-src 'self'; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-src https://challenges.cloudflare.com; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


@app.errorhandler(413)
def request_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Request is too large."}), 413
    return "Request is too large.", 413


@app.get("/")
def index():
    return send_from_directory(app.root_path, "index.html")


@app.get("/<path:filename>")
def static_file(filename):
    public_files = {
        "index.html",
        "feedback.html",
        "style.css",
        "script.js",
        "feedback.js",
        "logo.png",
        "report.png",
    }
    if filename not in public_files:
        abort(404)
    return send_from_directory(app.root_path, filename)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/turnstile-config")
@rate_limit(120, 60)
def turnstile_config():
    if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
        return jsonify({"error": "Spam protection is not configured yet."}), 503
    return jsonify({"siteKey": TURNSTILE_SITE_KEY})


@app.get("/api/rooms")
@rate_limit(120, 60)
def list_rooms():
    with database() as connection:
        purge_expired_content(connection)
        rows = execute(
            connection,
            "SELECT code, help_text, created_at FROM rooms ORDER BY created_at DESC",
        ).fetchall()
    return jsonify({"rooms": [public_room(row) for row in rows]})


@app.post("/api/rooms")
@rate_limit(6, 60)
def create_room():
    data = request.get_json(silent=True) or {}
    help_text = clean_text(data.get("helpText"), 500)
    sender = clean_text(data.get("sender"), 40)
    owner_token = clean_text(data.get("ownerToken"), 200)

    if not help_text or not sender or len(owner_token) < 20:
        return jsonify({"error": "Room text, sender, and owner token are required."}), 400

    created_at = now_ms()
    alphabet = string.ascii_uppercase + string.digits

    room_code = "".join(secrets.choice(alphabet) for _ in range(8))

    with database() as connection:
        purge_expired_content(connection)
        execute(
            connection,
            """
            INSERT INTO rooms
                (code, help_text, owner_token_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (room_code, help_text, hash_token(owner_token), created_at),
        )
        execute(
            connection,
            """
            INSERT INTO messages (room_code, sender, text, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (room_code, sender, help_text, created_at),
        )

    return (
        jsonify(
            {
                "room": {
                    "code": room_code,
                    "help": help_text,
                    "createdAt": created_at,
                }
            }
        ),
        201,
    )


@app.get("/api/rooms/<room_code>")
@rate_limit(120, 60)
def get_room(room_code):
    room_code = room_code.upper()
    with database() as connection:
        purge_expired_content(connection)
        room = execute(
            connection,
            "SELECT code, help_text, created_at FROM rooms WHERE code = ?",
            (room_code,),
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404

        messages = execute(
            connection,
            """
            SELECT id, sender, text, created_at
            FROM messages
            WHERE room_code = ?
            ORDER BY id
            """,
            (room_code,),
        ).fetchall()

    return jsonify(
        {
            "room": public_room(room),
            "messages": [public_message(message) for message in messages],
        }
    )


@app.post("/api/rooms/<room_code>/messages")
@rate_limit(45, 60)
def add_message(room_code):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    sender = clean_text(data.get("sender"), 40)
    text = clean_text(data.get("text"), 1000)

    if not sender or not text:
        return jsonify({"error": "Sender and message are required."}), 400

    created_at = now_ms()
    with database() as connection:
        purge_expired_content(connection)
        room = execute(
            connection, "SELECT code FROM rooms WHERE code = ?", (room_code,)
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404

        message_count = execute(
            connection,
            "SELECT COUNT(*) AS count FROM messages WHERE room_code = ?",
            (room_code,),
        ).fetchone()["count"]
        if message_count >= MAX_MESSAGES_PER_ROOM:
            return jsonify({"error": "This room has reached its message limit."}), 409

        if IS_POSTGRES:
            inserted = execute(
                connection,
                """
                INSERT INTO messages (room_code, sender, text, created_at)
                VALUES (?, ?, ?, ?)
                RETURNING id
                """,
                (room_code, sender, text, created_at),
            ).fetchone()
            message_id = inserted["id"]
        else:
            cursor = execute(
                connection,
                """
                INSERT INTO messages (room_code, sender, text, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (room_code, sender, text, created_at),
            )
            message_id = cursor.lastrowid

    return (
        jsonify(
            {
                "message": {
                    "id": message_id,
                    "sender": sender,
                    "text": text,
                    "createdAt": created_at,
                }
            }
        ),
        201,
    )


@app.delete("/api/rooms/<room_code>")
@rate_limit(10, 60)
def delete_room(room_code):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    owner_token = clean_text(data.get("ownerToken"), 200)

    with database() as connection:
        purge_expired_content(connection)
        room = execute(
            connection,
            "SELECT owner_token_hash FROM rooms WHERE code = ?",
            (room_code,),
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404
        if not owner_token or not hmac.compare_digest(
            hash_token(owner_token), room["owner_token_hash"]
        ):
            return jsonify({"error": "Only the room creator can close this room."}), 403

        execute(connection, "DELETE FROM reports WHERE room_code = ?", (room_code,))
        execute(connection, "DELETE FROM messages WHERE room_code = ?", (room_code,))
        execute(connection, "DELETE FROM rooms WHERE code = ?", (room_code,))

    return ("", 204)


@app.post("/api/rooms/<room_code>/report")
@rate_limit(5, 3600)
def report_room(room_code):
    room_code = room_code.upper()
    with database() as connection:
        purge_expired_content(connection)
        room = execute(
            connection, "SELECT code FROM rooms WHERE code = ?", (room_code,)
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404
        execute(
            connection,
            "INSERT INTO reports (room_code, created_at) VALUES (?, ?)",
            (room_code, now_ms()),
        )
    return jsonify({"reported": True}), 201


@app.post("/api/feedback")
@rate_limit(3, 3600)
def submit_feedback():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    text = clean_text(data.get("text"), 1000)
    turnstile_token = clean_text(data.get("turnstileToken"), 2048)
    if not text:
        return jsonify({"error": "Feedback is required."}), 400
    if not turnstile_token:
        return jsonify({"error": "Please complete the spam check."}), 400

    if not WEB3FORMS_ACCESS_KEY:
        return jsonify({"error": "Feedback email is not configured yet."}), 503
    if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
        return jsonify({"error": "Spam protection is not configured yet."}), 503

    try:
        expected_hostname = request.host.partition(":")[0]
        if not verify_turnstile(turnstile_token, expected_hostname):
            return (
                jsonify(
                    {"error": "Spam verification failed. Please complete it again."}
                ),
                400,
            )
        deliver_feedback(text)
    except urllib.error.HTTPError as error:
        app.logger.warning("A feedback provider returned HTTP %s.", error.code)
        status = 429 if error.code == 429 else 502
        return (
            jsonify({"error": "Feedback could not be sent. Please try again later."}),
            status,
        )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError):
        app.logger.warning("Feedback verification or delivery failed.")
        return (
            jsonify({"error": "Feedback could not be sent. Please try again later."}),
            502,
        )

    return jsonify({"submitted": True}), 201


init_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
