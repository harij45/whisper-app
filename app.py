import hashlib
import os
import random
import sqlite3
import string
import time
from contextlib import contextmanager

from flask import Flask, abort, jsonify, request, send_from_directory


app = Flask(__name__, static_folder=None)

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(app.root_path, "data", "whisper.db")
)


def now_ms():
    return int(time.time() * 1000)


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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
            "CREATE INDEX IF NOT EXISTS messages_room_code_id "
            "ON messages(room_code, id)",
        )


def clean_text(value, maximum):
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


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
        "logo.png",
        "report.png",
    }
    if filename not in public_files:
        abort(404)
    return send_from_directory(app.root_path, filename)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.get("/api/rooms")
def list_rooms():
    with database() as connection:
        rows = execute(
            connection,
            "SELECT code, help_text, created_at FROM rooms ORDER BY created_at DESC",
        ).fetchall()
    return jsonify({"rooms": [public_room(row) for row in rows]})


@app.post("/api/rooms")
def create_room():
    data = request.get_json(silent=True) or {}
    help_text = clean_text(data.get("helpText"), 500)
    sender = clean_text(data.get("sender"), 40)
    owner_token = clean_text(data.get("ownerToken"), 200)

    if not help_text or not sender or len(owner_token) < 20:
        return jsonify({"error": "Room text, sender, and owner token are required."}), 400

    created_at = now_ms()
    alphabet = string.ascii_uppercase + string.digits

    room_code = "".join(random.SystemRandom().choices(alphabet, k=8))

    with database() as connection:
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
def get_room(room_code):
    room_code = room_code.upper()
    with database() as connection:
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
def add_message(room_code):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    sender = clean_text(data.get("sender"), 40)
    text = clean_text(data.get("text"), 1000)

    if not sender or not text:
        return jsonify({"error": "Sender and message are required."}), 400

    created_at = now_ms()
    with database() as connection:
        room = execute(
            connection, "SELECT code FROM rooms WHERE code = ?", (room_code,)
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404

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
def delete_room(room_code):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    owner_token = clean_text(data.get("ownerToken"), 200)

    with database() as connection:
        room = execute(
            connection,
            "SELECT owner_token_hash FROM rooms WHERE code = ?",
            (room_code,),
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404
        if not owner_token or hash_token(owner_token) != room["owner_token_hash"]:
            return jsonify({"error": "Only the room creator can close this room."}), 403

        execute(connection, "DELETE FROM reports WHERE room_code = ?", (room_code,))
        execute(connection, "DELETE FROM messages WHERE room_code = ?", (room_code,))
        execute(connection, "DELETE FROM rooms WHERE code = ?", (room_code,))

    return ("", 204)


@app.post("/api/rooms/<room_code>/report")
def report_room(room_code):
    room_code = room_code.upper()
    with database() as connection:
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


init_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
