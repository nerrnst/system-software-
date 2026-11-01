import argparse
import json
import hashlib
import math
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from http.cookies import CookieError, SimpleCookie
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


MAX_BODY_BYTES = 64 * 1024
MAX_RESULTS = 500
MAX_BATCH_SIZE = 500
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
SESSION_LIFETIME_SECONDS = 12 * 60 * 60
PASSWORD_HASH_ITERATIONS = 600_000
SESSION_COOKIE = "stridesignal_session"


class InvalidReading(ValueError):
    pass


def _identifier(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise InvalidReading(f"{field} must be a non-empty string of at most 128 characters")
    return value.strip()


def _number(value, field, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidReading(f"{field} must be a number")
    value = float(value)
    if not math.isfinite(value):
        raise InvalidReading(f"{field} must be finite")
    if minimum is not None and value < minimum:
        raise InvalidReading(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise InvalidReading(f"{field} must be at most {maximum}")
    return value


def normalize_reading(payload):
    if not isinstance(payload, dict):
        raise InvalidReading("Request body must be a JSON object")

    required = {
        "participant_id",
        "device_id",
        "captured_at",
        "acceleration_m_s2",
        "angular_velocity_rad_s",
    }
    allowed = required | {"heart_rate_bpm"}
    missing = required - payload.keys()
    extra = payload.keys() - allowed
    if missing:
        raise InvalidReading(f"Missing fields: {', '.join(sorted(missing))}")
    if extra:
        raise InvalidReading(f"Unexpected fields: {', '.join(sorted(extra))}")

    captured_at = payload["captured_at"]
    if not isinstance(captured_at, str):
        raise InvalidReading("captured_at must be an ISO 8601 timestamp with a timezone")
    try:
        parsed_at = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidReading("captured_at must be a valid ISO 8601 timestamp") from error
    if parsed_at.tzinfo is None or parsed_at.utcoffset() is None:
        raise InvalidReading("captured_at must include a timezone")
    normalized_at = parsed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    normalized = {
        "participant_id": _identifier(payload["participant_id"], "participant_id"),
        "device_id": _identifier(payload["device_id"], "device_id"),
        "captured_at": normalized_at,
    }
    for field in ("acceleration_m_s2", "angular_velocity_rad_s"):
        axes = payload[field]
        if not isinstance(axes, dict) or set(axes) != {"x", "y", "z"}:
            raise InvalidReading(f"{field} must contain exactly x, y, and z")
        for axis in ("x", "y", "z"):
            normalized[f"{field}_{axis}"] = _number(axes[axis], f"{field}.{axis}")

    heart_rate = payload.get("heart_rate_bpm")
    if heart_rate is not None:
        normalized["heart_rate_bpm"] = _number(
            heart_rate, "heart_rate_bpm", minimum=1, maximum=300
        )
    else:
        normalized["heart_rate_bpm"] = None
    return normalized


class ReadingStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            # Additive migrations preserve existing records; legacy rows stay unassigned.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER REFERENCES users(id),
                    participant_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    acceleration_m_s2_x REAL NOT NULL,
                    acceleration_m_s2_y REAL NOT NULL,
                    acceleration_m_s2_z REAL NOT NULL,
                    angular_velocity_rad_s_x REAL NOT NULL,
                    angular_velocity_rad_s_y REAL NOT NULL,
                    angular_velocity_rad_s_z REAL NOT NULL,
                    heart_rate_bpm REAL
                )
                """
            )
            reading_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(readings)")
            }
            if "owner_user_id" not in reading_columns:
                connection.execute(
                    "ALTER TABLE readings ADD COLUMN owner_user_id INTEGER REFERENCES users(id)"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    is_premium INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            user_columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
            for name, declaration in (
                ("role", "TEXT NOT NULL DEFAULT 'user'"),
                ("is_active", "INTEGER NOT NULL DEFAULT 1"),
                ("is_premium", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in user_columns:
                    connection.execute(f"ALTER TABLE users ADD COLUMN {name} {declaration}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    expires_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_captured_at "
                "ON readings (captured_at DESC, id DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_readings_owner_participant_captured "
                "ON readings (owner_user_id, participant_id, captured_at DESC, id DESC)"
            )

    def _connect(self):
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _password_hash(password, salt):
        # Only salted, deliberately expensive hashes are stored, never plaintext passwords.
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_HASH_ITERATIONS
        ).hex()

    @staticmethod
    def _normalize_email(email):
        if not isinstance(email, str):
            raise ValueError("Enter a valid email address")
        email = email.strip().lower()
        if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise ValueError("Enter a valid email address")
        return email

    def create_user(self, email, password, role="user"):
        email = self._normalize_email(email)
        if role not in ("user", "admin"):
            raise ValueError("role must be user or admin")
        if not isinstance(password, str) or not PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH:
            raise ValueError(
                f"Password must be between {PASSWORD_MIN_LENGTH} and "
                f"{PASSWORD_MAX_LENGTH} characters"
            )
        salt = secrets.token_hex(16)
        password_hash = self._password_hash(password, salt)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT INTO users (email, password_salt, password_hash, created_at, role) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (email, salt, password_hash, created_at, role),
                )
                return {
                    "id": cursor.lastrowid,
                    "email": email,
                    "role": role,
                    "is_premium": False,
                }
        except sqlite3.IntegrityError as error:
            if "users.email" in str(error):
                raise ValueError("An account with that email already exists") from error
            raise

    def authenticate_user(self, email, password):
        email = email.strip().lower() if isinstance(email, str) else ""
        if not isinstance(password, str):
            password = ""
        if len(password) > PASSWORD_MAX_LENGTH:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, password_salt, password_hash, role, is_active, is_premium
                FROM users WHERE email = ?
                """,
                (email,),
            ).fetchone()
        if row is None:
            self._password_hash(password, "00" * 16)
            return None
        candidate = self._password_hash(password, row[2])
        if not secrets.compare_digest(candidate, row[3]):
            return None
        if not row[5]:
            return None
        return {
            "id": row[0], "email": row[1], "role": row[4], "is_premium": bool(row[6])
        }

    def create_session(self, user_id, token):
        now = datetime.now(timezone.utc)
        expires_at = now.timestamp() + SESSION_LIFETIME_SECONDS
        expiry = datetime.fromtimestamp(expires_at, timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(timespec="seconds"),))
            connection.execute(
                # Persist only a digest so a database dump does not contain usable cookies.
                "INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                (hashlib.sha256(token.encode("utf-8")).hexdigest(), user_id, expiry),
            )
        return SESSION_LIFETIME_SECONDS

    def get_session_user(self, token):
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.email, users.role, users.is_premium
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                    AND users.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        return {
            "id": row[0], "email": row[1], "role": row[2], "is_premium": bool(row[3])
        } if row else None

    def delete_session(self, token):
        if not token:
            return
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def reset_user_password(self, actor_id, user_id):
        password = secrets.token_urlsafe(24)
        salt = secrets.token_hex(16)
        password_hash = self._password_hash(password, salt)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (salt, password_hash, user_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Account not found")
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.audit(actor_id, "reset_account_password", str(user_id))
        return password

    def insert(self, reading, owner_user_id):
        return self.insert_many([reading], owner_user_id)[0]

    def insert_many(self, readings, owner_user_id):
        columns = (
            "owner_user_id",
            "participant_id",
            "device_id",
            "captured_at",
            "acceleration_m_s2_x",
            "acceleration_m_s2_y",
            "acceleration_m_s2_z",
            "angular_velocity_rad_s_x",
            "angular_velocity_rad_s_y",
            "angular_velocity_rad_s_z",
            "heart_rate_bpm",
        )
        with self._connect() as connection:
            ids = []
            for reading in readings:
                cursor = connection.execute(
                    f"INSERT INTO readings ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)})",
                    (owner_user_id,) + tuple(reading[column] for column in columns[1:]),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return an ID for an imported reading")
                ids.append(cursor.lastrowid)
            return ids

    def list(self, owner_user_id, participant_id=None, limit=100):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            # Ownership is part of every query, never a filter supplied by the browser.
            if participant_id is None:
                rows = connection.execute(
                    "SELECT * FROM readings WHERE owner_user_id = ? "
                    "ORDER BY captured_at DESC, id DESC LIMIT ?",
                    (owner_user_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM readings WHERE owner_user_id = ? AND participant_id = ? "
                    "ORDER BY captured_at DESC, id DESC LIMIT ?",
                    (owner_user_id, participant_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def admin_overview(self):
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users),
                    (SELECT COUNT(*) FROM users WHERE is_active = 1),
                    (SELECT COUNT(*) FROM users WHERE is_premium = 1),
                    (SELECT COUNT(*) FROM readings),
                    (SELECT COUNT(*) FROM readings WHERE owner_user_id IS NULL),
                    (SELECT COUNT(*) FROM sessions WHERE expires_at > ?)
                """,
                (datetime.now(timezone.utc).isoformat(timespec="seconds"),),
            ).fetchone()
            return {
                "users": totals[0],
                "active_users": totals[1],
                "premium_users": totals[2],
                "readings": totals[3],
                "unassigned_readings": totals[4],
                "active_sessions": totals[5],
                "database_bytes": Path(self.path).stat().st_size,
            }

    def admin_users(self):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT users.id, users.email, users.role, users.is_active,
                    users.is_premium, users.created_at, COUNT(readings.id) AS reading_count
                FROM users LEFT JOIN readings ON readings.owner_user_id = users.id
                GROUP BY users.id ORDER BY users.created_at DESC, users.id DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def admin_readings(self, limit=200):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT readings.*, users.email AS owner_email
                FROM readings LEFT JOIN users ON users.id = readings.owner_user_id
                ORDER BY readings.captured_at DESC, readings.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_admin_user(self, actor_id, user_id, changes):
        allowed = {"role", "is_active", "is_premium"}
        if not changes or changes.keys() - allowed:
            raise ValueError("Provide role, is_active, and/or is_premium only")
        if "role" in changes and changes["role"] not in ("user", "admin"):
            raise ValueError("role must be user or admin")
        for field in ("is_active", "is_premium"):
            if field in changes and not isinstance(changes[field], bool):
                raise ValueError(f"{field} must be a boolean")
        with self._connect() as connection:
            current = connection.execute(
                "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if current is None:
                raise KeyError("Account not found")
            new_role = changes.get("role", current[0])
            new_active = int(changes.get("is_active", bool(current[1])))
            if actor_id == user_id and (new_role != "admin" or not new_active):
                raise ValueError("You cannot remove your own administrator access")
            if current[0] == "admin" and current[1] and (
                new_role != "admin" or not new_active
            ):
                # Avoid locking the system by removing its final active administrator.
                admin_count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()[0]
                if admin_count <= 1:
                    raise ValueError("The last active administrator cannot be removed")
            assignments = ", ".join(f"{field} = ?" for field in changes)
            values = [
                int(value) if field in ("is_active", "is_premium") else value
                for field, value in changes.items()
            ]
            connection.execute(
                f"UPDATE users SET {assignments} WHERE id = ?", (*values, user_id)
            )
            if changes.get("is_active") is False:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        self.audit(actor_id, "update_account", str(user_id))

    def delete_admin_user(self, actor_id, user_id):
        with self._connect() as connection:
            user = connection.execute(
                "SELECT role, is_active FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if user is None:
                raise KeyError("Account not found")
            if actor_id == user_id:
                raise ValueError("You cannot delete your own account")
            if user[0] == "admin" and user[1]:
                # The last administrator must be replaced before this account is removed.
                count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
                ).fetchone()[0]
                if count <= 1:
                    raise ValueError("The last active administrator cannot be deleted")
            connection.execute("DELETE FROM readings WHERE owner_user_id = ?", (user_id,))
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        self.audit(actor_id, "delete_account", str(user_id))

    def update_admin_reading(self, actor_id, reading_id, reading):
        columns = (
            "participant_id", "device_id", "captured_at",
            "acceleration_m_s2_x", "acceleration_m_s2_y", "acceleration_m_s2_z",
            "angular_velocity_rad_s_x", "angular_velocity_rad_s_y",
            "angular_velocity_rad_s_z", "heart_rate_bpm",
        )
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE readings SET " + ", ".join(f"{column} = ?" for column in columns)
                + " WHERE id = ?",
                tuple(reading[column] for column in columns) + (reading_id,),
            )
            if cursor.rowcount == 0:
                raise KeyError("Reading not found")
        self.audit(actor_id, "update_reading", str(reading_id))

    def delete_admin_reading(self, actor_id, reading_id):
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM readings WHERE id = ?", (reading_id,))
            if cursor.rowcount == 0:
                raise KeyError("Reading not found")
        self.audit(actor_id, "delete_reading", str(reading_id))

    def audit(self, actor_id, action, target):
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO admin_audit (actor_user_id, action, target, created_at) "
                "VALUES (?, ?, ?, ?)",
                (actor_id, action, target, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )

    def admin_audit_log(self, limit=100):
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT admin_audit.id, users.email AS actor_email, admin_audit.action,
                    admin_audit.target, admin_audit.created_at
                FROM admin_audit LEFT JOIN users ON users.id = admin_audit.actor_user_id
                ORDER BY admin_audit.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def backup(self):
        # SQLite's backup API gives a consistent snapshot while the app may be writing.
        descriptor, backup_path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(descriptor)
        try:
            with sqlite3.connect(self.path) as source, sqlite3.connect(backup_path) as target:
                source.backup(target)
            return Path(backup_path).read_bytes()
        finally:
            Path(backup_path).unlink(missing_ok=True)


def make_handler(store):
    class Handler(BaseHTTPRequestHandler):
        def _send_file(self, path, content_type):
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Cache-Control",
                "no-cache" if path.name == "index.html" else "public, max-age=3600",
            )
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status, body):
            encoded = json.dumps(body, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def _session_token(self):
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
            except CookieError:
                return None
            morsel = cookie.get(SESSION_COOKIE)
            return morsel.value if morsel else None

        def _current_user(self):
            return store.get_session_user(self._session_token())

        def _require_admin(self):
            # UI visibility is not authorization; every admin API checks role server-side.
            user = self._current_user()
            if user is None:
                self._send_json(401, {"error": "Sign in to continue"})
                return None
            if user["role"] != "admin":
                self._send_json(403, {"error": "Administrator access is required"})
                return None
            return user

        def _resource_id(self, path, prefix):
            value = path.removeprefix(prefix)
            if not value.isdecimal() or int(value) < 1:
                self._send_json(404, {"error": "Not found"})
                return None
            return int(value)

        def _read_json_body(self):
            content_length = self.headers.get("Content-Length")
            try:
                length = int(content_length)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "A valid Content-Length header is required"})
                return None, False
            if length < 0 or length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "Request body must be at most 65536 bytes"})
                return None, False
            try:
                return json.loads(self.rfile.read(length)), True
            except (json.JSONDecodeError, UnicodeDecodeError) as error:
                self._send_json(400, {"error": str(error)})
                return None, False

        def _set_session_cookie(self, token, max_age):
            self.send_header(
                "Set-Cookie",
                # Strict same-site and HTTP-only limit cross-site and script access to sessions.
                f"{SESSION_COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; SameSite=Strict",
            )

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/v1/auth/logout":
                store.delete_session(self._session_token())
                encoded = json.dumps({"status": "signed_out"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header(
                    "Set-Cookie",
                    f"{SESSION_COOKIE}=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict",
                )
                self.end_headers()
                self.wfile.write(encoded)
                return
            if path.startswith("/v1/admin/"):
                admin = self._require_admin()
                if admin is None:
                    return
                payload, valid_body = self._read_json_body()
                if not valid_body:
                    return
                if path == "/v1/admin/users":
                    if (
                        not isinstance(payload, dict)
                        or payload.keys() - {"email", "password", "role", "is_premium"}
                        or not {"email", "password"} <= payload.keys()
                    ):
                        self._send_json(400, {"error": "Provide email, password, optional role and is_premium"})
                        return
                    role = payload.get("role", "user")
                    premium = payload.get("is_premium", False)
                    if role not in ("user", "admin") or not isinstance(premium, bool):
                        self._send_json(400, {"error": "role must be user/admin and is_premium must be boolean"})
                        return
                    try:
                        created = store.create_user(payload["email"], payload["password"], role)
                        if premium:
                            store.update_admin_user(
                                admin["id"], created["id"], {"is_premium": True}
                            )
                    except ValueError as error:
                        self._send_json(400, {"error": str(error)})
                        return
                    store.audit(admin["id"], "create_account", str(created["id"]))
                    self._send_json(201, {"id": created["id"], "status": "created"})
                    return
                password_prefix = "/v1/admin/users/"
                password_suffix = "/reset-password"
                if path.startswith(password_prefix) and path.endswith(password_suffix):
                    user_id = self._resource_id(
                        path[len(password_prefix):-len(password_suffix)],
                        "",
                    )
                    if user_id is None:
                        return
                    if payload != {}:
                        self._send_json(400, {"error": "Body must be an empty JSON object"})
                        return
                    try:
                        password = store.reset_user_password(admin["id"], user_id)
                    except KeyError as error:
                        self._send_json(404, {"error": str(error)})
                        return
                    self._send_json(200, {"temporary_password": password})
                    return
                self._send_json(404, {"error": "Not found"})
                return
            if path not in (
                "/v1/auth/register",
                "/v1/auth/login",
                "/v1/readings",
                "/v1/readings/batch",
            ):
                self._send_json(404, {"error": "Not found"})
                return

            user = None
            if path in ("/v1/readings", "/v1/readings/batch"):
                user = self._current_user()
                if user is None:
                    self._send_json(401, {"error": "Sign in to access your readings"})
                    return

            payload, valid_body = self._read_json_body()
            if not valid_body:
                return

            if path in ("/v1/auth/register", "/v1/auth/login"):
                if not isinstance(payload, dict) or set(payload) != {"email", "password"}:
                    self._send_json(400, {"error": "Body must contain only email and password"})
                    return
                if path == "/v1/auth/register":
                    try:
                        user = store.create_user(payload["email"], payload["password"])
                    except ValueError as error:
                        self._send_json(400, {"error": str(error)})
                        return
                    status = 201
                else:
                    user = store.authenticate_user(payload["email"], payload["password"])
                    if user is None:
                        self._send_json(401, {"error": "Email or password is incorrect"})
                        return
                    status = 200
                token = secrets.token_urlsafe(32)
                max_age = store.create_session(user["id"], token)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self._set_session_cookie(token, max_age)
                response = json.dumps(
                    {
                        "user": {
                            "email": user["email"],
                            "role": user["role"],
                            "is_premium": user["is_premium"],
                        }
                    }
                ).encode("utf-8")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return

            if path == "/v1/readings":
                try:
                    reading = normalize_reading(payload)
                except InvalidReading as error:
                    self._send_json(400, {"error": str(error)})
                    return
                reading_id = store.insert(reading, user["id"])
                self._send_json(201, {"id": reading_id, "status": "stored"})
                return

            if not isinstance(payload, dict) or set(payload) != {"readings"}:
                self._send_json(400, {"error": "Batch body must contain only a readings array"})
                return
            readings = payload["readings"]
            if not isinstance(readings, list) or not readings:
                self._send_json(400, {"error": "readings must be a non-empty array"})
                return
            if len(readings) > MAX_BATCH_SIZE:
                self._send_json(
                    400, {"error": f"Batch must contain at most {MAX_BATCH_SIZE} readings"}
                )
                return
            normalized = []
            for index, item in enumerate(readings):
                try:
                    normalized.append(normalize_reading(item))
                except InvalidReading as error:
                    self._send_json(
                        400,
                        {"error": f"Invalid reading at index {index}: {error}", "index": index},
                    )
                    return
            reading_ids = store.insert_many(normalized, user["id"])
            self._send_json(
                201,
                {"ids": reading_ids, "count": len(reading_ids), "status": "stored"},
            )

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/v1/auth/me":
                user = self._current_user()
                if user is None:
                    self._send_json(401, {"error": "Sign in to access your readings"})
                    return
                self._send_json(
                    200,
                    {
                        "user": {
                            "email": user["email"],
                            "role": user["role"],
                            "is_premium": user["is_premium"],
                        }
                    },
                )
                return
            # Serve only known frontend assets instead of accepting arbitrary filesystem paths.
            static_files = {
                "/": ("index.html", "text/html; charset=utf-8"),
                "/index.html": ("index.html", "text/html; charset=utf-8"),
                "/dashboard": ("dashboard.html", "text/html; charset=utf-8"),
                "/dashboard.html": ("dashboard.html", "text/html; charset=utf-8"),
                "/admin": ("admin.html", "text/html; charset=utf-8"),
                "/admin.html": ("admin.html", "text/html; charset=utf-8"),
                "/how-it-works": ("how-it-works.html", "text/html; charset=utf-8"),
                "/how-it-works.html": ("how-it-works.html", "text/html; charset=utf-8"),
                "/about": ("about.html", "text/html; charset=utf-8"),
                "/about.html": ("about.html", "text/html; charset=utf-8"),
                "/styles.css": ("styles.css", "text/css; charset=utf-8"),
                "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                "/admin.js": ("admin.js", "text/javascript; charset=utf-8"),
                "/home.js": ("home.js", "text/javascript; charset=utf-8"),
            }
            if parsed.path in static_files:
                filename, content_type = static_files[parsed.path]
                page = Path(__file__).parent / "static" / filename
                self._send_file(page, content_type)
                return
            if parsed.path.startswith("/v1/admin/"):
                user = self._require_admin()
                if user is None:
                    return
                if parsed.path == "/v1/admin/backup":
                    # The complete database includes private records and must stay admin-only.
                    backup = store.backup()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/octet-stream")
                    self.send_header(
                        "Content-Disposition",
                        'attachment; filename="stridesignal-backup.sqlite3"',
                    )
                    self.send_header("Content-Length", str(len(backup)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(backup)
                    store.audit(user["id"], "download_database_backup", "database")
                    return
                if parsed.path == "/v1/admin/overview":
                    self._send_json(200, {"overview": store.admin_overview()})
                    return
                if parsed.path == "/v1/admin/users":
                    self._send_json(200, {"users": store.admin_users()})
                    return
                if parsed.path == "/v1/admin/readings":
                    self._send_json(200, {"readings": store.admin_readings()})
                    return
                if parsed.path == "/v1/admin/audit":
                    self._send_json(200, {"events": store.admin_audit_log()})
                    return
                self._send_json(404, {"error": "Not found"})
                return
            if parsed.path != "/v1/readings":
                self._send_json(404, {"error": "Not found"})
                return
            user = self._current_user()
            if user is None:
                self._send_json(401, {"error": "Sign in to access your readings"})
                return
            try:
                query = parse_qs(parsed.query, strict_parsing=True) if parsed.query else {}
            except ValueError:
                self._send_json(400, {"error": "Malformed query string"})
                return
            unknown = query.keys() - {"participant_id", "limit"}
            if unknown:
                self._send_json(400, {"error": f"Unexpected query parameter: {sorted(unknown)[0]}"})
                return
            if any(len(values) != 1 for values in query.values()):
                self._send_json(400, {"error": "Query parameters may only be specified once"})
                return
            participant_id = query.get("participant_id", [None])[0]
            if participant_id is not None:
                try:
                    participant_id = _identifier(participant_id, "participant_id")
                except InvalidReading as error:
                    self._send_json(400, {"error": str(error)})
                    return
            limit_value = query.get("limit", ["100"])[0]
            try:
                limit = int(limit_value)
            except ValueError:
                self._send_json(400, {"error": "limit must be an integer from 1 to 500"})
                return
            if not 1 <= limit <= MAX_RESULTS:
                self._send_json(400, {"error": "limit must be an integer from 1 to 500"})
                return
            self._send_json(
                200,
                {
                    "readings": store.list(
                        owner_user_id=user["id"], participant_id=participant_id, limit=limit
                    )
                },
            )

        def do_DELETE(self):
            path = urlparse(self.path).path
            user = self._require_admin()
            if user is None:
                return
            if path.startswith("/v1/admin/users/"):
                user_id = self._resource_id(path, "/v1/admin/users/")
                if user_id is None:
                    return
                try:
                    store.delete_admin_user(user["id"], user_id)
                except KeyError as error:
                    self._send_json(404, {"error": str(error)})
                    return
                except ValueError as error:
                    self._send_json(400, {"error": str(error)})
                    return
                self._send_json(200, {"status": "deleted"})
                return
            if path.startswith("/v1/admin/readings/"):
                reading_id = self._resource_id(path, "/v1/admin/readings/")
                if reading_id is None:
                    return
                try:
                    store.delete_admin_reading(user["id"], reading_id)
                except KeyError as error:
                    self._send_json(404, {"error": str(error)})
                    return
                self._send_json(200, {"status": "deleted"})
                return
            self._send_json(404, {"error": "Not found"})

        def do_PATCH(self):
            path = urlparse(self.path).path
            user = self._require_admin()
            if user is None:
                return
            if not path.startswith("/v1/admin/users/"):
                self._send_json(404, {"error": "Not found"})
                return
            user_id = self._resource_id(path, "/v1/admin/users/")
            if user_id is None:
                return
            payload, valid_body = self._read_json_body()
            if not valid_body:
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "Body must be a JSON object"})
                return
            try:
                store.update_admin_user(user["id"], user_id, payload)
            except KeyError as error:
                self._send_json(404, {"error": str(error)})
                return
            except ValueError as error:
                self._send_json(400, {"error": str(error)})
                return
            self._send_json(200, {"status": "updated"})

        def do_PUT(self):
            path = urlparse(self.path).path
            user = self._require_admin()
            if user is None:
                return
            if not path.startswith("/v1/admin/readings/"):
                self._send_json(404, {"error": "Not found"})
                return
            reading_id = self._resource_id(path, "/v1/admin/readings/")
            if reading_id is None:
                return
            payload, valid_body = self._read_json_body()
            if not valid_body:
                return
            try:
                reading = normalize_reading(payload)
                store.update_admin_reading(user["id"], reading_id, reading)
            except InvalidReading as error:
                self._send_json(400, {"error": str(error)})
                return
            except KeyError as error:
                self._send_json(404, {"error": str(error)})
                return
            self._send_json(200, {"status": "updated"})

        def log_message(self, format_string, *args):
            super().log_message(format_string, *args)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run the StrideSignal local prototype.")
    parser.add_argument(
        "--create-admin",
        metavar="EMAIL",
        help="Create the first administrator and print its one-time generated password",
    )
    args = parser.parse_args(argv)
    database_path = os.environ.get("ACQUISITION_DB", "readings.sqlite3")
    store = ReadingStore(database_path)
    if args.create_admin:
        with store._connect() as connection:
            admin_count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1"
            ).fetchone()[0]
        if admin_count:
            parser.error("An active administrator already exists; use the admin panel.")
        password = secrets.token_urlsafe(24)
        try:
            user = store.create_user(args.create_admin, password, role="admin")
        except ValueError as error:
            parser.error(str(error))
        print(f"Administrator email: {user['email']}")
        print(f"One-time administrator password: {password}")
        print("Save this password securely; it will not be shown again.")
        return
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store))
    print(f"Collecting wearable readings at http://127.0.0.1:{port}/v1/readings")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping data acquisition server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
