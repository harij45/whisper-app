import base64
import hashlib
import hmac
import ipaddress
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

from cryptography.fernet import Fernet, InvalidToken
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    redirect,
    request,
    send_from_directory,
    session,
    stream_with_context,
)
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
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
IP_PRIVACY_KEY = os.environ.get("IP_PRIVACY_KEY", "")
MIN_IP_PRIVACY_KEY_LENGTH = 16
SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(app.root_path, "data", "whisper.db")
)
ROOM_TTL_MS = 24 * 60 * 60 * 1000
FEEDBACK_TTL_MS = 30 * 24 * 60 * 60 * 1000
IP_RETENTION_MS = 30 * 24 * 60 * 60 * 1000
AUDIT_RETENTION_MS = 180 * 24 * 60 * 60 * 1000
CLEANUP_INTERVAL_SECONDS = 60
MAX_MESSAGES_PER_ROOM = 500
MESSAGE_REPORT_REASONS = {
    "harassment",
    "hate",
    "self-harm",
    "spam",
    "threat",
    "other",
}
ALIAS_ADJECTIVES = (
    "Amber", "Arctic", "Ashen", "Azure", "Brave", "Bright", "Calm", "Cedar",
    "Cosmic", "Crimson", "Daring", "Dawn", "Deep", "Ember", "Frost", "Gentle",
    "Golden", "Hidden", "Indigo", "Lunar", "Misty", "Neon", "Noble", "Quiet",
    "Rapid", "Silver", "Solar", "Still", "Storm", "Swift", "Velvet", "Wild",
)
ALIAS_ANIMALS = (
    "Badger", "Bear", "Cobra", "Coyote", "Crane", "Crow", "Dolphin", "Eagle",
    "Falcon", "Ferret", "Fox", "Gecko", "Heron", "Jackal", "Kestrel", "Koala",
    "Lynx", "Manta", "Marten", "Otter", "Owl", "Panda", "Panther", "Raven",
    "Seal", "Shark", "Sparrow", "Tiger", "Viper", "Whale", "Wolf", "Wren",
)
ALIAS_TRAITS = (
    "Bold", "Calm", "Clear", "Clever", "Cool", "Daring", "Deep", "Dreaming",
    "Free", "Gentle", "Grand", "Happy", "Hushed", "Kind", "Light", "Lucky",
    "Mellow", "Nimble", "Noble", "Patient", "Proud", "Quick", "Quiet", "Rare",
    "Ready", "Sharp", "Silent", "Soft", "Steady", "True", "Warm", "Wise",
)
ALIAS_WORLDS = (
    "Brook", "Cloud", "Dawn", "Dusk", "Echo", "Field", "Flame", "Forest",
    "Grove", "Harbor", "Haven", "Hill", "Lake", "Leaf", "Light", "Meadow",
    "Moon", "Night", "Ocean", "Rain", "River", "Sky", "Snow", "Star",
    "Stone", "Sun", "Tide", "Vale", "Wave", "Wind", "Wood", "Zenith",
)

app.secret_key = hashlib.sha256(
    f"whisper-admin-session-v1:{ADMIN_PASSWORD}".encode("utf-8")
).digest()
app.config.update(
    SESSION_COOKIE_NAME="whisper_admin_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
    SESSION_COOKIE_SECURE=os.environ.get("RENDER", "").lower() == "true",
)

_rate_buckets = defaultdict(deque)
_rate_lock = Lock()
_rate_salt = secrets.token_bytes(32)
_cleanup_lock = Lock()
_last_cleanup_at = 0.0


def now_ms():
    return int(time.time() * 1000)


def client_ip():
    address = request.remote_addr or ""
    if os.environ.get("RENDER", "").lower() == "true":
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            address = forwarded.split(",", 1)[0].strip()

    try:
        return ipaddress.ip_address(address).compressed
    except ValueError:
        return "unknown"


def hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def rate_limit(limit, window_seconds):
    def decorator(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            address = client_ip()
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


def identity_column_exists(connection, column_name):
    if IS_POSTGRES:
        row = execute(
            connection,
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'identities'
              AND column_name = ?
            """,
            (column_name,),
        ).fetchone()
        return row is not None

    columns = execute(connection, "PRAGMA table_info(identities)").fetchall()
    return any(column["name"] == column_name for column in columns)


def ensure_identity_ip_columns(connection):
    columns = {
        "ip_hash": "VARCHAR(64)",
        "ip_encrypted": "TEXT",
        "ip_last_seen": "BIGINT",
    }
    for name, data_type in columns.items():
        if not identity_column_exists(connection, name):
            execute(
                connection,
                f"ALTER TABLE identities ADD COLUMN {name} {data_type}",
            )


def banned_ip_alias_column_exists(connection):
    if IS_POSTGRES:
        row = execute(
            connection,
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'banned_ips'
              AND column_name = ?
            """,
            ("alias",),
        ).fetchone()
        return row is not None

    columns = execute(connection, "PRAGMA table_info(banned_ips)").fetchall()
    return any(column["name"] == "alias" for column in columns)


def ensure_banned_ip_alias_column(connection):
    if not banned_ip_alias_column_exists(connection):
        execute(
            connection,
            "ALTER TABLE banned_ips ADD COLUMN alias VARCHAR(40)",
        )


def table_column_exists(connection, table_name, column_name):
    if IS_POSTGRES:
        row = execute(
            connection,
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
              AND column_name = ?
            """,
            (table_name, column_name),
        ).fetchone()
        return row is not None

    columns = execute(connection, f"PRAGMA table_info({table_name})").fetchall()
    return any(column["name"] == column_name for column in columns)


def ensure_ban_expiry_column(connection):
    if not table_column_exists(connection, "banned_ips", "expires_at"):
        execute(
            connection,
            "ALTER TABLE banned_ips ADD COLUMN expires_at BIGINT",
        )


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
    identity_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    ban_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    message_report_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    audit_id = (
        "BIGSERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )

    with database() as connection:
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS identities (
                id {identity_id},
                token_hash VARCHAR(64) NOT NULL UNIQUE,
                alias VARCHAR(40) NOT NULL UNIQUE,
                created_at BIGINT NOT NULL,
                last_seen BIGINT NOT NULL,
                ip_hash VARCHAR(64),
                ip_encrypted TEXT,
                ip_last_seen BIGINT
            )
            """,
        )
        ensure_identity_ip_columns(connection)
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
            """
            CREATE TABLE IF NOT EXISTS site_settings (
                key VARCHAR(50) PRIMARY KEY,
                value VARCHAR(1000) NOT NULL,
                updated_at BIGINT NOT NULL
            )
            """,
        )
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS banned_ips (
                id {ban_id},
                ip_hash VARCHAR(64) NOT NULL UNIQUE,
                ip_encrypted TEXT NOT NULL,
                alias VARCHAR(40),
                reason VARCHAR(500) NOT NULL,
                created_at BIGINT NOT NULL,
                expires_at BIGINT
            )
            """,
        )
        ensure_banned_ip_alias_column(connection)
        ensure_ban_expiry_column(connection)
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS message_reports (
                id {message_report_id},
                message_id BIGINT NOT NULL,
                room_code VARCHAR(8) NOT NULL,
                reporter_token_hash VARCHAR(64) NOT NULL,
                reason VARCHAR(40) NOT NULL,
                details VARCHAR(500) NOT NULL,
                status VARCHAR(20) NOT NULL DEFAULT 'open',
                moderator_note VARCHAR(1000) NOT NULL DEFAULT '',
                created_at BIGINT NOT NULL,
                resolved_at BIGINT,
                UNIQUE (message_id, reporter_token_hash),
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (room_code) REFERENCES rooms(code) ON DELETE CASCADE
            )
            """,
        )
        execute(
            connection,
            f"""
            CREATE TABLE IF NOT EXISTS admin_audit_log (
                id {audit_id},
                action VARCHAR(80) NOT NULL,
                target VARCHAR(200) NOT NULL,
                details VARCHAR(1000) NOT NULL,
                created_at BIGINT NOT NULL
            )
            """,
        )
        execute(
            connection,
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO NOTHING
            """,
            ("site_notice", "", now_ms()),
        )
        execute(
            connection,
            """
            INSERT INTO site_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO NOTHING
            """,
            ("site_paused", "0", now_ms()),
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
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS identities_last_seen "
            "ON identities(last_seen)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS identities_ip_hash "
            "ON identities(ip_hash)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS message_reports_status_created "
            "ON message_reports(status, created_at)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS message_reports_room_code "
            "ON message_reports(room_code)",
        )
        execute(
            connection,
            "CREATE INDEX IF NOT EXISTS admin_audit_created_at "
            "ON admin_audit_log(created_at)",
        )
        migrate_legacy_aliases(connection)


def purge_expired_content(connection):
    global _last_cleanup_at

    current = time.monotonic()
    if current - _last_cleanup_at < CLEANUP_INTERVAL_SECONDS:
        return

    with _cleanup_lock:
        current = time.monotonic()
        if current - _last_cleanup_at < CLEANUP_INTERVAL_SECONDS:
            return

        _purge_expired_content(connection)
        _last_cleanup_at = current


def _purge_expired_content(connection):
    room_cutoff = now_ms() - ROOM_TTL_MS
    feedback_cutoff = now_ms() - FEEDBACK_TTL_MS
    ip_cutoff = now_ms() - IP_RETENTION_MS
    audit_cutoff = now_ms() - AUDIT_RETENTION_MS
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
    execute(
        connection,
        "DELETE FROM banned_ips WHERE expires_at IS NOT NULL AND expires_at <= ?",
        (now_ms(),),
    )
    execute(
        connection,
        "DELETE FROM admin_audit_log WHERE created_at < ?",
        (audit_cutoff,),
    )
    execute(
        connection,
        """
        UPDATE identities
        SET ip_hash = NULL, ip_encrypted = NULL, ip_last_seen = NULL
        WHERE ip_last_seen IS NOT NULL AND ip_last_seen < ?
        """,
        (ip_cutoff,),
    )


def clean_text(value, maximum):
    if not isinstance(value, str):
        return ""
    return value.strip()[:maximum]


def secure_equal(first, second):
    return hmac.compare_digest(
        str(first).encode("utf-8"),
        str(second).encode("utf-8"),
    )


def get_site_setting(connection, key, default=""):
    row = execute(
        connection,
        "SELECT value FROM site_settings WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row is not None else default


def set_site_setting(connection, key, value):
    execute(
        connection,
        """
        INSERT INTO site_settings (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT (key) DO UPDATE
        SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (key, value, now_ms()),
    )


def record_admin_action(connection, action, target, details=""):
    execute(
        connection,
        """
        INSERT INTO admin_audit_log (action, target, details, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            clean_text(action, 80),
            clean_text(target, 200),
            clean_text(details, 1000),
            now_ms(),
        ),
    )


def get_site_config(connection):
    return {
        "notice": get_site_setting(connection, "site_notice", ""),
        "paused": get_site_setting(connection, "site_paused", "0") == "1",
    }


def delete_room_data(connection, room_code):
    execute(connection, "DELETE FROM reports WHERE room_code = ?", (room_code,))
    execute(connection, "DELETE FROM messages WHERE room_code = ?", (room_code,))
    return execute(connection, "DELETE FROM rooms WHERE code = ?", (room_code,))


def admin_auth_response(status=401):
    message = (
        "Admin access is not configured. Add an ADMIN_PASSWORD of at least "
        "16 characters in Render."
        if status == 503
        else "Your admin session has ended. Sign in again."
    )
    response = jsonify({"error": message})
    response.status_code = status
    return response


def admin_auth_failure_response():
    address = client_ip()
    visitor = hashlib.sha256(
        _rate_salt + address.encode("utf-8")
    ).hexdigest()
    key = ("admin_auth_failures", visitor)
    current = time.monotonic()
    window_seconds = 15 * 60

    with _rate_lock:
        bucket = _rate_buckets[key]
        while bucket and bucket[0] <= current - window_seconds:
            bucket.popleft()
        if len(bucket) >= 10:
            retry_after = max(
                1,
                math.ceil(bucket[0] + window_seconds - current),
            )
            response = jsonify(
                {"error": "Too many failed admin sign-in attempts."}
            )
            response.status_code = 429
            response.headers["Retry-After"] = str(retry_after)
            return response
        bucket.append(current)

    return jsonify({"error": "The admin password is incorrect."}), 401


def admin_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if len(ADMIN_PASSWORD) < 16:
            return admin_auth_response(503)
        if session.get("is_admin") is not True:
            if request.path.startswith("/api/"):
                return admin_auth_response()
            return redirect("/admin/login")
        return function(*args, **kwargs)

    return wrapped


def admin_action_required(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        if request.headers.get("X-Admin-Action") != "Whisper-Admin":
            return jsonify({"error": "Invalid admin action request."}), 403
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            return jsonify({"error": "Cross-site admin actions are blocked."}), 403

        origin = request.headers.get("Origin")
        if origin and not secure_equal(origin.rstrip("/"), request.host_url.rstrip("/")):
            return jsonify({"error": "Admin action origin does not match."}), 403
        return function(*args, **kwargs)

    return wrapped


def generate_alias():
    adjective = secrets.choice(ALIAS_ADJECTIVES)
    trait = secrets.choice(ALIAS_TRAITS)
    world = secrets.choice(ALIAS_WORLDS)
    animal = secrets.choice(ALIAS_ANIMALS)
    return f"{adjective}{trait}{world}{animal}"


def migrate_legacy_aliases(connection):
    identities = execute(connection, "SELECT id, alias FROM identities").fetchall()
    used_aliases = {identity["alias"] for identity in identities}

    for identity in identities:
        old_alias = identity["alias"]
        _name, separator, suffix = old_alias.rpartition("-")
        if separator != "-" or len(suffix) != 8 or not suffix.isalnum():
            continue

        for _attempt in range(100):
            new_alias = generate_alias()
            if new_alias not in used_aliases:
                break
        else:
            raise RuntimeError("Could not migrate a legacy anonymous name.")

        execute(
            connection,
            "UPDATE messages SET sender = ? WHERE sender = ?",
            (new_alias, old_alias),
        )
        execute(
            connection,
            "UPDATE identities SET alias = ? WHERE id = ?",
            (new_alias, identity["id"]),
        )
        used_aliases.add(new_alias)


def ip_privacy_enabled():
    return len(IP_PRIVACY_KEY) >= MIN_IP_PRIVACY_KEY_LENGTH


def ip_hash(address):
    if not ip_privacy_enabled() or address == "unknown":
        return None
    return hmac.new(
        IP_PRIVACY_KEY.encode("utf-8"),
        f"whisper-ip-ban-v1:{address}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def ip_cipher():
    key_bytes = hashlib.sha256(
        f"whisper-ip-encryption-v1:{IP_PRIVACY_KEY}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt_ip(address):
    if not ip_privacy_enabled() or address == "unknown":
        return None
    return ip_cipher().encrypt(address.encode("utf-8")).decode("ascii")


def decrypt_ip(encrypted_address):
    if not ip_privacy_enabled() or not encrypted_address:
        return None
    try:
        return ip_cipher().decrypt(
            encrypted_address.encode("ascii"),
            ttl=None,
        ).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, ValueError):
        return None


def is_ip_banned(connection, address):
    address_hash = ip_hash(address)
    if not address_hash:
        return False
    row = execute(
        connection,
        """
        SELECT 1
        FROM banned_ips
        WHERE ip_hash = ?
          AND (expires_at IS NULL OR expires_at > ?)
        """,
        (address_hash, now_ms()),
    ).fetchone()
    return row is not None


def reject_banned_ip(connection, address):
    if not is_ip_banned(connection, address):
        return None
    return (
        jsonify(
            {
                "error": "You have been blocked.",
                "code": "IP_BLOCKED",
            }
        ),
        403,
    )


def find_identity_alias(connection, identity_token):
    if len(identity_token) < 20:
        return None
    identity = execute(
        connection,
        "SELECT alias FROM identities WHERE token_hash = ?",
        (hash_token(identity_token),),
    ).fetchone()
    return identity["alias"] if identity is not None else None


def update_identity_ip(connection, identity_token, address, timestamp):
    address_hash = ip_hash(address)
    encrypted_address = encrypt_ip(address)
    if not address_hash or not encrypted_address:
        return
    execute(
        connection,
        """
        UPDATE identities
        SET ip_hash = ?, ip_encrypted = ?, ip_last_seen = ?, last_seen = ?
        WHERE token_hash = ?
        """,
        (
            address_hash,
            encrypted_address,
            timestamp,
            timestamp,
            hash_token(identity_token),
        ),
    )


def reserve_identity(connection, identity_token, address):
    token_hash = hash_token(identity_token)
    timestamp = now_ms()
    address_hash = ip_hash(address)
    encrypted_address = encrypt_ip(address)
    existing_alias = find_identity_alias(connection, identity_token)
    if existing_alias:
        if address_hash and encrypted_address:
            update_identity_ip(connection, identity_token, address, timestamp)
        else:
            execute(
                connection,
                "UPDATE identities SET last_seen = ? WHERE token_hash = ?",
                (timestamp, token_hash),
            )
        return existing_alias

    for _attempt in range(20):
        alias = generate_alias()
        if IS_POSTGRES:
            inserted = execute(
                connection,
                """
                INSERT INTO identities
                    (
                        token_hash, alias, created_at, last_seen,
                        ip_hash, ip_encrypted, ip_last_seen
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                RETURNING alias
                """,
                (
                    token_hash,
                    alias,
                    timestamp,
                    timestamp,
                    address_hash,
                    encrypted_address,
                    timestamp if address_hash else None,
                ),
            ).fetchone()
            if inserted is not None:
                return inserted["alias"]
        else:
            inserted = execute(
                connection,
                """
                INSERT OR IGNORE INTO identities
                    (
                        token_hash, alias, created_at, last_seen,
                        ip_hash, ip_encrypted, ip_last_seen
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    token_hash,
                    alias,
                    timestamp,
                    timestamp,
                    address_hash,
                    encrypted_address,
                    timestamp if address_hash else None,
                ),
            )
            if inserted.rowcount == 1:
                return alias

        existing_alias = find_identity_alias(connection, identity_token)
        if existing_alias:
            return existing_alias

    raise RuntimeError("Could not reserve a unique alias.")


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


def sse_event(event, data, event_id=None):
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event}")
    lines.append(
        "data: "
        + json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    )
    return "\n".join(lines) + "\n\n"


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
    if response.mimetype == "text/event-stream":
        response.headers["Cache-Control"] = "no-cache, no-transform"
    elif request.path.startswith("/api/") or request.path in {
        "/admin",
        "/admin/login",
        "/admin.js",
    }:
        response.headers["Cache-Control"] = "no-store, max-age=0"
    if request.path in {"/admin", "/admin/login", "/admin.js"} or (
        request.path.startswith("/api/admin/")
    ):
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
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


@app.get("/admin")
@admin_required
def admin_page():
    return send_from_directory(app.root_path, "admin.html")


@app.get("/admin/login")
def admin_login_page():
    if session.get("is_admin") is True:
        return redirect("/admin")
    return send_from_directory(app.root_path, "admin-login.html")


@app.get("/admin.js")
@admin_required
def admin_script():
    return send_from_directory(
        app.root_path,
        "admin.js",
        mimetype="application/javascript",
    )


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(app.root_path, "favicon.png", mimetype="image/png")


@app.get("/<path:filename>")
def static_file(filename):
    public_files = {
        "index.html",
        "feedback.html",
        "style.css",
        "script.js",
        "feedback.js",
        "admin-login.js",
        "logo.png",
        "report.png",
        "favicon.png",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
        "manifest.webmanifest",
        "sw.js",
    }
    if filename not in public_files:
        abort(404)
    if filename == "manifest.webmanifest":
        return send_from_directory(
            app.root_path,
            filename,
            mimetype="application/manifest+json",
        )
    if filename == "sw.js":
        return send_from_directory(
            app.root_path,
            filename,
            mimetype="application/javascript",
        )
    return send_from_directory(app.root_path, filename)


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/admin/login")
@rate_limit(20, 15 * 60)
@admin_action_required
def admin_login():
    if len(ADMIN_PASSWORD) < 16:
        return admin_auth_response(503)

    data = request.get_json(silent=True) or {}
    username = clean_text(data.get("username"), 100)
    password = data.get("password") if isinstance(data.get("password"), str) else ""
    if not (
        secure_equal(username, ADMIN_USERNAME)
        and secure_equal(password, ADMIN_PASSWORD)
    ):
        return admin_auth_failure_response()

    session.clear()
    session["is_admin"] = True
    with database() as connection:
        record_admin_action(connection, "admin_signed_in", ADMIN_USERNAME)
    return jsonify({"signedIn": True})


@app.post("/api/admin/logout")
@admin_required
@admin_action_required
def admin_logout():
    with database() as connection:
        record_admin_action(connection, "admin_signed_out", ADMIN_USERNAME)
    session.clear()
    return ("", 204)


@app.get("/api/site-config")
@rate_limit(120, 60)
def site_config():
    with database() as connection:
        config = get_site_config(connection)
    return jsonify(config)


@app.get("/api/turnstile-config")
@rate_limit(120, 60)
def turnstile_config():
    if not TURNSTILE_SITE_KEY or not TURNSTILE_SECRET_KEY:
        return jsonify({"error": "Spam protection is not configured yet."}), 503
    return jsonify({"siteKey": TURNSTILE_SITE_KEY})


@app.post("/api/identity")
@rate_limit(60, 3600)
def assign_identity():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    identity_token = clean_text(data.get("identityToken"), 200)
    if len(identity_token) < 20:
        return jsonify({"error": "A valid identity token is required."}), 400

    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        alias = reserve_identity(connection, identity_token, address)
    return jsonify({"alias": alias})


@app.get("/api/rooms")
@rate_limit(120, 60)
def list_rooms():
    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        rows = execute(
            connection,
            "SELECT code, help_text, created_at FROM rooms ORDER BY created_at DESC",
        ).fetchall()
    return jsonify({"rooms": [public_room(row) for row in rows]})


@app.post("/api/rooms")
@rate_limit(6, 60)
def create_room():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    help_text = clean_text(data.get("helpText"), 500)
    identity_token = clean_text(data.get("identityToken"), 200)
    owner_token = clean_text(data.get("ownerToken"), 200)

    if not help_text or len(identity_token) < 20 or len(owner_token) < 20:
        return (
            jsonify(
                {"error": "Room text, identity token, and owner token are required."}
            ),
            400,
        )

    created_at = now_ms()
    alphabet = string.ascii_uppercase + string.digits

    room_code = "".join(secrets.choice(alphabet) for _ in range(8))

    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        if get_site_setting(connection, "site_paused", "0") == "1":
            return (
                jsonify(
                    {
                        "error": (
                            "Whisper is temporarily paused. New rooms cannot "
                            "be created right now."
                        )
                    }
                ),
                503,
            )
        sender = find_identity_alias(connection, identity_token)
        if sender is None:
            return jsonify({"error": "Refresh the page to restore your identity."}), 401
        update_identity_ip(connection, identity_token, address, created_at)
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
    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
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


@app.get("/api/rooms/<room_code>/events")
@rate_limit(30, 60)
def stream_room_events(room_code):
    room_code = room_code.upper()
    if len(room_code) != 8 or not room_code.isalnum():
        return jsonify({"error": "Invalid room code."}), 400

    try:
        query_after = int(request.args.get("after", "0"))
        known_count = int(request.args.get("count", "0"))
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid event cursor."}), 400

    header_after = request.headers.get("Last-Event-ID", "")
    if header_after.isdigit():
        query_after = max(query_after, int(header_after))
    last_message_id = max(0, query_after)
    known_count = max(0, known_count)
    address = client_ip()

    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        room = execute(
            connection,
            "SELECT 1 FROM rooms WHERE code = ?",
            (room_code,),
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404

    @stream_with_context
    def generate_events():
        nonlocal last_message_id, known_count
        deadline = time.monotonic() + 55
        heartbeat_at = time.monotonic() + 15
        yield "retry: 1000\n\n"

        while time.monotonic() < deadline:
            with database() as connection:
                if is_ip_banned(connection, address):
                    yield sse_event(
                        "blocked",
                        {"error": "You have been blocked.", "code": "IP_BLOCKED"},
                    )
                    return

                room_exists = execute(
                    connection,
                    "SELECT 1 FROM rooms WHERE code = ?",
                    (room_code,),
                ).fetchone()
                if room_exists is None:
                    yield sse_event("room_closed", {"roomCode": room_code})
                    return

                rows = execute(
                    connection,
                    """
                    SELECT id, sender, text, created_at
                    FROM messages
                    WHERE room_code = ? AND id > ?
                    ORDER BY id
                    """,
                    (room_code, last_message_id),
                ).fetchall()
                current_count = execute(
                    connection,
                    "SELECT COUNT(*) AS count FROM messages WHERE room_code = ?",
                    (room_code,),
                ).fetchone()["count"]

            expected_count = known_count + len(rows)
            if current_count != expected_count:
                yield sse_event(
                    "sync",
                    {"roomCode": room_code, "messageCount": current_count},
                )

            for message in rows:
                public = public_message(message)
                last_message_id = public["id"]
                yield sse_event("message", public, public["id"])

            known_count = current_count
            current_time = time.monotonic()
            if current_time >= heartbeat_at:
                yield sse_event("heartbeat", {"roomCode": room_code})
                heartbeat_at = current_time + 15
            time.sleep(0.75)

    return Response(
        generate_events(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/rooms/<room_code>/messages")
@rate_limit(45, 60)
def add_message(room_code):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    identity_token = clean_text(data.get("identityToken"), 200)
    text = clean_text(data.get("text"), 1000)

    if len(identity_token) < 20 or not text:
        return jsonify({"error": "Identity token and message are required."}), 400

    created_at = now_ms()
    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        if get_site_setting(connection, "site_paused", "0") == "1":
            return (
                jsonify(
                    {
                        "error": (
                            "Whisper is temporarily paused. Messages cannot "
                            "be sent right now."
                        )
                    }
                ),
                503,
            )
        sender = find_identity_alias(connection, identity_token)
        if sender is None:
            return jsonify({"error": "Refresh the page to restore your identity."}), 401
        update_identity_ip(connection, identity_token, address, created_at)
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

    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
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

        delete_room_data(connection, room_code)

    return ("", 204)


@app.post("/api/rooms/<room_code>/report")
@rate_limit(5, 3600)
def report_room(room_code):
    room_code = room_code.upper()
    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
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


@app.post("/api/rooms/<room_code>/messages/<int:message_id>/report")
@rate_limit(10, 3600)
def report_message(room_code, message_id):
    room_code = room_code.upper()
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    identity_token = clean_text(data.get("identityToken"), 200)
    reason = clean_text(data.get("reason"), 40).lower()
    details = clean_text(data.get("details"), 500)

    if len(identity_token) < 20:
        return jsonify({"error": "A valid identity is required."}), 400
    if reason not in MESSAGE_REPORT_REASONS:
        return jsonify({"error": "Select a valid report reason."}), 400
    if reason == "other" and not details:
        return jsonify({"error": "Please briefly describe the issue."}), 400

    address = client_ip()
    with database() as connection:
        purge_expired_content(connection)
        banned_response = reject_banned_ip(connection, address)
        if banned_response:
            return banned_response
        reporter = find_identity_alias(connection, identity_token)
        if reporter is None:
            return jsonify({"error": "Refresh the page to restore your identity."}), 401
        update_identity_ip(connection, identity_token, address, now_ms())
        message = execute(
            connection,
            """
            SELECT sender
            FROM messages
            WHERE id = ? AND room_code = ?
            """,
            (message_id, room_code),
        ).fetchone()
        if message is None:
            return jsonify({"error": "Message not found."}), 404
        if secure_equal(message["sender"], reporter):
            return jsonify({"error": "You cannot report your own message."}), 400

        timestamp = now_ms()
        if IS_POSTGRES:
            inserted = execute(
                connection,
                """
                INSERT INTO message_reports
                    (
                        message_id, room_code, reporter_token_hash,
                        reason, details, status, moderator_note,
                        created_at, resolved_at
                    )
                VALUES (?, ?, ?, ?, ?, 'open', '', ?, NULL)
                ON CONFLICT (message_id, reporter_token_hash) DO NOTHING
                RETURNING id
                """,
                (
                    message_id,
                    room_code,
                    hash_token(identity_token),
                    reason,
                    details,
                    timestamp,
                ),
            ).fetchone()
            created = inserted is not None
        else:
            inserted = execute(
                connection,
                """
                INSERT OR IGNORE INTO message_reports
                    (
                        message_id, room_code, reporter_token_hash,
                        reason, details, status, moderator_note,
                        created_at, resolved_at
                    )
                VALUES (?, ?, ?, ?, ?, 'open', '', ?, NULL)
                """,
                (
                    message_id,
                    room_code,
                    hash_token(identity_token),
                    reason,
                    details,
                    timestamp,
                ),
            )
            created = inserted.rowcount == 1

    return jsonify({"reported": True, "created": created}), 201 if created else 200


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


@app.get("/api/admin/overview")
@rate_limit(180, 60)
@admin_required
def admin_overview():
    with database() as connection:
        purge_expired_content(connection)
        stats = {}
        for label, table in (
            ("rooms", "rooms"),
            ("messages", "messages"),
            ("identities", "identities"),
            ("reports", "reports"),
            ("bans", "banned_ips"),
        ):
            stats[label] = execute(
                connection,
                f"SELECT COUNT(*) AS count FROM {table}",
            ).fetchone()["count"]
        stats["messageReports"] = execute(
            connection,
            """
            SELECT COUNT(*) AS count
            FROM message_reports
            WHERE status = 'open'
            """,
        ).fetchone()["count"]

        room_rows = execute(
            connection,
            """
            SELECT
                rooms.code,
                rooms.help_text,
                rooms.created_at,
                (SELECT COUNT(*) FROM messages
                    WHERE messages.room_code = rooms.code) AS message_count,
                (SELECT COUNT(*) FROM reports
                    WHERE reports.room_code = rooms.code) AS report_count,
                (SELECT COUNT(*) FROM message_reports
                    WHERE message_reports.room_code = rooms.code
                      AND message_reports.status = 'open')
                    AS message_report_count
            FROM rooms
            ORDER BY rooms.created_at DESC
            LIMIT 200
            """,
        ).fetchall()
        ban_rows = execute(
            connection,
            """
            SELECT
                banned_ips.id,
                banned_ips.ip_encrypted,
                COALESCE(
                    banned_ips.alias,
                    (
                        SELECT MIN(identities.alias)
                        FROM identities
                        WHERE identities.ip_hash = banned_ips.ip_hash
                    )
                ) AS alias,
                banned_ips.reason,
                banned_ips.created_at,
                banned_ips.expires_at
            FROM banned_ips
            ORDER BY banned_ips.created_at DESC
            """,
        ).fetchall()
        message_report_rows = execute(
            connection,
            """
            SELECT
                message_reports.id,
                message_reports.message_id,
                message_reports.room_code,
                message_reports.reason,
                message_reports.details,
                message_reports.moderator_note,
                message_reports.created_at,
                messages.sender,
                messages.text
            FROM message_reports
            JOIN messages ON messages.id = message_reports.message_id
            WHERE message_reports.status = 'open'
            ORDER BY message_reports.created_at DESC
            LIMIT 200
            """,
        ).fetchall()
        audit_rows = execute(
            connection,
            """
            SELECT id, action, target, details, created_at
            FROM admin_audit_log
            ORDER BY created_at DESC
            LIMIT 100
            """,
        ).fetchall()
        config = get_site_config(connection)

    rooms = [
        {
            "code": row["code"],
            "help": row["help_text"],
            "createdAt": row["created_at"],
            "messageCount": row["message_count"],
            "reportCount": row["report_count"],
            "messageReportCount": row["message_report_count"],
        }
        for row in room_rows
    ]
    bans = [
        {
            "id": row["id"],
            "alias": row["alias"],
            "ipAddress": decrypt_ip(row["ip_encrypted"]),
            "reason": row["reason"],
            "createdAt": row["created_at"],
            "expiresAt": row["expires_at"],
        }
        for row in ban_rows
    ]
    message_reports = [
        {
            "id": row["id"],
            "messageId": row["message_id"],
            "roomCode": row["room_code"],
            "reason": row["reason"],
            "details": row["details"],
            "moderatorNote": row["moderator_note"],
            "createdAt": row["created_at"],
            "sender": row["sender"],
            "text": row["text"],
        }
        for row in message_report_rows
    ]
    audit_log = [
        {
            "id": row["id"],
            "action": row["action"],
            "target": row["target"],
            "details": row["details"],
            "createdAt": row["created_at"],
        }
        for row in audit_rows
    ]
    return jsonify(
        {
            "stats": stats,
            "rooms": rooms,
            "bans": bans,
            "messageReports": message_reports,
            "auditLog": audit_log,
            "config": config,
            "ipModerationConfigured": ip_privacy_enabled(),
        }
    )


@app.get("/api/admin/rooms/<room_code>")
@rate_limit(180, 60)
@admin_required
def admin_room_detail(room_code):
    room_code = room_code.upper()
    if len(room_code) != 8 or not room_code.isalnum():
        return jsonify({"error": "Invalid room code."}), 400

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
            SELECT
                messages.id,
                messages.sender,
                messages.text,
                messages.created_at,
                identities.id AS identity_id,
                identities.ip_encrypted,
                banned_ips.id AS ban_id,
                (
                    SELECT COUNT(*)
                    FROM message_reports
                    WHERE message_reports.message_id = messages.id
                      AND message_reports.status = 'open'
                ) AS message_report_count
            FROM messages
            LEFT JOIN identities ON identities.alias = messages.sender
            LEFT JOIN banned_ips ON banned_ips.ip_hash = identities.ip_hash
            WHERE messages.room_code = ?
            ORDER BY messages.id
            """,
            (room_code,),
        ).fetchall()
        report_count = execute(
            connection,
            "SELECT COUNT(*) AS count FROM reports WHERE room_code = ?",
            (room_code,),
        ).fetchone()["count"]

    admin_messages = [
        {
            **public_message(message),
            "identityId": message["identity_id"],
            "ipAddress": decrypt_ip(message["ip_encrypted"]),
            "banId": message["ban_id"],
            "messageReportCount": message["message_report_count"],
        }
        for message in messages
    ]
    return jsonify(
        {
            "room": public_room(room),
            "messages": admin_messages,
            "reportCount": report_count,
            "ipModerationConfigured": ip_privacy_enabled(),
        }
    )


@app.post("/api/admin/bans")
@rate_limit(30, 60)
@admin_required
@admin_action_required
def admin_ban_identity():
    if not ip_privacy_enabled():
        return (
            jsonify(
                {
                    "error": (
                        "IP moderation is not configured. Add an "
                        "IP_PRIVACY_KEY of at least 16 characters in Render "
                        "first."
                    )
                }
            ),
            503,
        )

    data = request.get_json(silent=True) or {}
    identity_id = data.get("identityId")
    reason = clean_text(data.get("reason"), 500) or "No reason provided"
    duration_hours = data.get("durationHours")
    if not isinstance(identity_id, int):
        return jsonify({"error": "A valid user is required."}), 400
    if duration_hours is not None and (
        not isinstance(duration_hours, int)
        or isinstance(duration_hours, bool)
        or not 1 <= duration_hours <= 8760
    ):
        return (
            jsonify({"error": "Ban duration must be between 1 and 8,760 hours."}),
            400,
        )
    expires_at = (
        now_ms() + duration_hours * 60 * 60 * 1000
        if duration_hours is not None
        else None
    )

    with database() as connection:
        identity = execute(
            connection,
            """
            SELECT alias, ip_hash, ip_encrypted
            FROM identities
            WHERE id = ?
            """,
            (identity_id,),
        ).fetchone()
        if identity is None:
            return jsonify({"error": "Anonymous user not found."}), 404
        if not identity["ip_hash"] or not identity["ip_encrypted"]:
            return (
                jsonify(
                    {
                        "error": (
                            "No recent network address is available for this "
                            "anonymous user."
                        )
                    }
                ),
                409,
            )

        execute(
            connection,
            """
            INSERT INTO banned_ips
                (ip_hash, ip_encrypted, alias, reason, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (ip_hash) DO UPDATE
            SET
                ip_encrypted = excluded.ip_encrypted,
                alias = excluded.alias,
                reason = excluded.reason,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (
                identity["ip_hash"],
                identity["ip_encrypted"],
                identity["alias"],
                reason,
                now_ms(),
                expires_at,
            ),
        )
        duration_label = (
            f"{duration_hours} hours"
            if duration_hours is not None
            else "permanent"
        )
        record_admin_action(
            connection,
            "ban_created",
            identity["alias"],
            f"Duration: {duration_label}. Reason: {reason}",
        )

    return (
        jsonify(
            {
                "banned": True,
                "alias": identity["alias"],
                "expiresAt": expires_at,
            }
        ),
        201,
    )


@app.delete("/api/admin/bans/<int:ban_id>")
@rate_limit(30, 60)
@admin_required
@admin_action_required
def admin_remove_ban(ban_id):
    with database() as connection:
        ban = execute(
            connection,
            "SELECT alias FROM banned_ips WHERE id = ?",
            (ban_id,),
        ).fetchone()
        if ban is None:
            return jsonify({"error": "Ban not found."}), 404
        deleted = execute(
            connection,
            "DELETE FROM banned_ips WHERE id = ?",
            (ban_id,),
        )
        record_admin_action(
            connection,
            "ban_removed",
            ban["alias"] or f"ban:{ban_id}",
        )
    return ("", 204)


@app.delete("/api/admin/rooms/<room_code>")
@rate_limit(60, 60)
@admin_required
@admin_action_required
def admin_delete_room(room_code):
    room_code = room_code.upper()
    if len(room_code) != 8 or not room_code.isalnum():
        return jsonify({"error": "Invalid room code."}), 400

    with database() as connection:
        room = execute(
            connection,
            "SELECT code FROM rooms WHERE code = ?",
            (room_code,),
        ).fetchone()
        if room is None:
            return jsonify({"error": "Room not found."}), 404
        deleted = delete_room_data(connection, room_code)
        record_admin_action(connection, "room_deleted", room_code)
    return ("", 204)


@app.delete("/api/admin/messages/<int:message_id>")
@rate_limit(120, 60)
@admin_required
@admin_action_required
def admin_delete_message(message_id):
    with database() as connection:
        message = execute(
            connection,
            "SELECT room_code, sender FROM messages WHERE id = ?",
            (message_id,),
        ).fetchone()
        if message is None:
            return jsonify({"error": "Message not found."}), 404
        deleted = execute(
            connection,
            "DELETE FROM messages WHERE id = ?",
            (message_id,),
        )
        record_admin_action(
            connection,
            "message_deleted",
            f"message:{message_id}",
            f"Room {message['room_code']}; sender {message['sender']}",
        )
    return ("", 204)


@app.put("/api/admin/message-reports/<int:report_id>")
@rate_limit(120, 60)
@admin_required
@admin_action_required
def admin_update_message_report(report_id):
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        data = {}
    action = clean_text(data.get("action"), 20).lower()
    moderator_note = clean_text(data.get("moderatorNote"), 1000)
    if action not in {"save", "resolve"}:
        return jsonify({"error": "Choose save or resolve."}), 400

    with database() as connection:
        report = execute(
            connection,
            """
            SELECT message_id, room_code, status
            FROM message_reports
            WHERE id = ?
            """,
            (report_id,),
        ).fetchone()
        if report is None:
            return jsonify({"error": "Message report not found."}), 404

        if action == "resolve":
            execute(
                connection,
                """
                UPDATE message_reports
                SET status = 'resolved', moderator_note = ?, resolved_at = ?
                WHERE id = ?
                """,
                (moderator_note, now_ms(), report_id),
            )
            audit_action = "message_report_resolved"
        else:
            execute(
                connection,
                """
                UPDATE message_reports
                SET moderator_note = ?
                WHERE id = ?
                """,
                (moderator_note, report_id),
            )
            audit_action = "moderator_note_saved"

        record_admin_action(
            connection,
            audit_action,
            f"report:{report_id}",
            (
                f"Message {report['message_id']} in room "
                f"{report['room_code']}; moderator note: "
                f"{moderator_note or '(empty)'}"
            ),
        )

    return jsonify(
        {
            "reportId": report_id,
            "status": "resolved" if action == "resolve" else report["status"],
            "moderatorNote": moderator_note,
        }
    )


@app.delete("/api/admin/reports")
@rate_limit(30, 60)
@admin_required
@admin_action_required
def admin_clear_reports():
    data = request.get_json(silent=True) or {}
    room_code = clean_text(data.get("roomCode"), 8).upper()

    with database() as connection:
        if room_code:
            if len(room_code) != 8 or not room_code.isalnum():
                return jsonify({"error": "Invalid room code."}), 400
            deleted = execute(
                connection,
                "DELETE FROM reports WHERE room_code = ?",
                (room_code,),
            )
            target = room_code
        else:
            deleted = execute(connection, "DELETE FROM reports")
            target = "all rooms"
        record_admin_action(
            connection,
            "room_reports_cleared",
            target,
            f"{deleted.rowcount} reports removed",
        )
    return ("", 204)


@app.put("/api/admin/settings")
@rate_limit(30, 60)
@admin_required
@admin_action_required
def admin_update_settings():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "Invalid settings."}), 400

    notice = clean_text(data.get("notice"), 500)
    paused = data.get("paused")
    if not isinstance(paused, bool):
        return jsonify({"error": "Paused must be true or false."}), 400

    with database() as connection:
        set_site_setting(connection, "site_notice", notice)
        set_site_setting(connection, "site_paused", "1" if paused else "0")
        record_admin_action(
            connection,
            "site_settings_updated",
            "site",
            f"Paused: {'yes' if paused else 'no'}; notice: {'set' if notice else 'cleared'}",
        )
        config = get_site_config(connection)
    return jsonify(config)


@app.delete("/api/admin/rooms")
@rate_limit(3, 3600)
@admin_required
@admin_action_required
def admin_delete_all_rooms():
    data = request.get_json(silent=True) or {}
    if data.get("confirmation") != "DELETE ALL ROOMS":
        return jsonify({"error": "The confirmation text did not match."}), 400

    with database() as connection:
        room_count = execute(
            connection,
            "SELECT COUNT(*) AS count FROM rooms",
        ).fetchone()["count"]
        execute(connection, "DELETE FROM reports")
        execute(connection, "DELETE FROM messages")
        execute(connection, "DELETE FROM rooms")
        record_admin_action(
            connection,
            "all_rooms_deleted",
            "all rooms",
            f"{room_count} rooms removed",
        )
    return ("", 204)


init_database()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    host = os.environ.get("HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
