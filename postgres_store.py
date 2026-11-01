import hashlib
import json
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import errors
from psycopg.rows import dict_row


PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
PASSWORD_HASH_ITERATIONS = 600_000
SESSION_LIFETIME_SECONDS = 12 * 60 * 60


class ReadingStore:
    def __init__(self, database_url):
        if not database_url:
            raise ValueError("DATABASE_URL is required")

        self.database_url = database_url

        with self._connect() as connection:
            connection.execute("SELECT 1")

    def _connect(self):
        return psycopg.connect(
            self.database_url,
            row_factory=dict_row,
            connect_timeout=15,
        )

    @staticmethod
    def _password_hash(password, salt):
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            PASSWORD_HASH_ITERATIONS,
        ).hex()

    @staticmethod
    def _normalize_email(email):
        if not isinstance(email, str):
            raise ValueError("Enter a valid email address")

        email = email.strip().lower()

        if len(email) > 254 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email
        ):
            raise ValueError("Enter a valid email address")

        return email

    def create_user(self, email, password, role="user"):
        email = self._normalize_email(email)

        if role not in ("user", "admin"):
            raise ValueError("role must be user or admin")

        if not isinstance(password, str) or not (
            PASSWORD_MIN_LENGTH <= len(password) <= PASSWORD_MAX_LENGTH
        ):
            raise ValueError(
                f"Password must be between {PASSWORD_MIN_LENGTH} and "
                f"{PASSWORD_MAX_LENGTH} characters"
            )

        salt = secrets.token_hex(16)
        password_hash = self._password_hash(password, salt)
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO users
                    (email, password_salt, password_hash, created_at, role)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, email, role, is_premium
                    """,
                    (email, salt, password_hash, created_at, role),
                ).fetchone()

            return {
                "id": row["id"],
                "email": row["email"],
                "role": row["role"],
                "is_premium": row["is_premium"],
            }

        except errors.UniqueViolation as error:
            raise ValueError(
                "An account with that email already exists"
            ) from error

    def authenticate_user(self, email, password):
        email = email.strip().lower() if isinstance(email, str) else ""

        if not isinstance(password, str):
            password = ""

        if len(password) > PASSWORD_MAX_LENGTH:
            return None

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id, email, password_salt, password_hash,
                       role, is_active, is_premium
                FROM users
                WHERE email = %s
                """,
                (email,),
            ).fetchone()

        if row is None:
            self._password_hash(password, "00" * 16)
            return None

        candidate = self._password_hash(password, row["password_salt"])

        if not secrets.compare_digest(candidate, row["password_hash"]):
            return None

        if not row["is_active"]:
            return None

        return {
            "id": row["id"],
            "email": row["email"],
            "role": row["role"],
            "is_premium": row["is_premium"],
        }

    def create_session(self, user_id, token):
        now = datetime.now(timezone.utc)
        expiry = datetime.fromtimestamp(
            now.timestamp() + SESSION_LIFETIME_SECONDS,
            timezone.utc,
        ).isoformat(timespec="seconds")

        now_text = now.isoformat(timespec="seconds")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= %s",
                (now_text,),
            )
            connection.execute(
                """
                INSERT INTO sessions (token_hash, user_id, expires_at)
                VALUES (%s, %s, %s)
                """,
                (token_hash, user_id, expiry),
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
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = %s
                  AND sessions.expires_at > %s
                  AND users.is_active = TRUE
                """,
                (token_hash, now),
            ).fetchone()

            connection.execute(
                "DELETE FROM sessions WHERE expires_at <= %s",
                (now,),
            )

        if row is None:
            return None

        return {
            "id": row["id"],
            "email": row["email"],
            "role": row["role"],
            "is_premium": row["is_premium"],
        }

    def delete_session(self, token):
        if not token:
            return

        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()

        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sessions WHERE token_hash = %s",
                (token_hash,),
            )

    def reset_user_password(self, actor_id, user_id):
        password = secrets.token_urlsafe(24)
        salt = secrets.token_hex(16)
        password_hash = self._password_hash(password, salt)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE users
                SET password_salt = %s, password_hash = %s
                WHERE id = %s
                """,
                (salt, password_hash, user_id),
            )

            if cursor.rowcount == 0:
                raise KeyError("Account not found")

            connection.execute(
                "DELETE FROM sessions WHERE user_id = %s",
                (user_id,),
            )

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

        ids = []

        with self._connect() as connection:
            for reading in readings:
                values = (owner_user_id,) + tuple(
                    reading[column] for column in columns[1:]
                )

                row = connection.execute(
                    f"""
                    INSERT INTO readings ({", ".join(columns)})
                    VALUES ({", ".join(["%s"] * len(columns))})
                    RETURNING id
                    """,
                    values,
                ).fetchone()

                ids.append(row["id"])

        return ids

    def list(self, owner_user_id, participant_id=None, limit=100):
        with self._connect() as connection:
            if participant_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM readings
                    WHERE owner_user_id = %s
                    ORDER BY captured_at DESC, id DESC
                    LIMIT %s
                    """,
                    (owner_user_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM readings
                    WHERE owner_user_id = %s AND participant_id = %s
                    ORDER BY captured_at DESC, id DESC
                    LIMIT %s
                    """,
                    (owner_user_id, participant_id, limit),
                ).fetchall()

        return rows

    def admin_overview(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS users,
                    (SELECT COUNT(*) FROM users WHERE is_active = TRUE)
                        AS active_users,
                    (SELECT COUNT(*) FROM users WHERE is_premium = TRUE)
                        AS premium_users,
                    (SELECT COUNT(*) FROM readings) AS readings,
                    (SELECT COUNT(*) FROM readings
                        WHERE owner_user_id IS NULL) AS unassigned_readings,
                    (SELECT COUNT(*) FROM sessions
                        WHERE expires_at > %s) AS active_sessions,
                    pg_database_size(current_database()) AS database_bytes
                """,
                (now,),
            ).fetchone()

        return totals

    def admin_users(self):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT users.id, users.email, users.role,
                       users.is_active, users.is_premium,
                       users.created_at,
                       COUNT(readings.id) AS reading_count
                FROM users
                LEFT JOIN readings ON readings.owner_user_id = users.id
                GROUP BY users.id
                ORDER BY users.created_at DESC, users.id DESC
                """
            ).fetchall()

    def admin_readings(self, limit=200):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT readings.*, users.email AS owner_email
                FROM readings
                LEFT JOIN users ON users.id = readings.owner_user_id
                ORDER BY readings.captured_at DESC, readings.id DESC
                LIMIT %s
                """,
                (limit,),
            ).fetchall()

    def update_admin_user(self, actor_id, user_id, changes):
        allowed = {"role", "is_active", "is_premium"}

        if not changes or changes.keys() - allowed:
            raise ValueError(
                "Provide role, is_active, and/or is_premium only"
            )

        if "role" in changes and changes["role"] not in ("user", "admin"):
            raise ValueError("role must be user or admin")

        for field in ("is_active", "is_premium"):
            if field in changes and not isinstance(changes[field], bool):
                raise ValueError(f"{field} must be a boolean")

        with self._connect() as connection:
            current = connection.execute(
                """
                SELECT role, is_active FROM users WHERE id = %s
                """,
                (user_id,),
            ).fetchone()

            if current is None:
                raise KeyError("Account not found")

            new_role = changes.get("role", current["role"])
            new_active = changes.get("is_active", current["is_active"])

            if actor_id == user_id and (
                new_role != "admin" or not new_active
            ):
                raise ValueError(
                    "You cannot remove your own administrator access"
                )

            if (
                current["role"] == "admin"
                and current["is_active"]
                and (new_role != "admin" or not new_active)
            ):
                count = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM users
                    WHERE role = 'admin' AND is_active = TRUE
                    """
                ).fetchone()["count"]

                if count <= 1:
                    raise ValueError(
                        "The last active administrator cannot be removed"
                    )

            assignments = ", ".join(
                f"{field} = %s" for field in changes
            )
            values = tuple(changes.values()) + (user_id,)

            connection.execute(
                f"UPDATE users SET {assignments} WHERE id = %s",
                values,
            )

            if changes.get("is_active") is False:
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = %s",
                    (user_id,),
                )

        self.audit(actor_id, "update_account", str(user_id))

    def delete_admin_user(self, actor_id, user_id):
        with self._connect() as connection:
            user = connection.execute(
                """
                SELECT role, is_active FROM users WHERE id = %s
                """,
                (user_id,),
            ).fetchone()

            if user is None:
                raise KeyError("Account not found")

            if actor_id == user_id:
                raise ValueError("You cannot delete your own account")

            if user["role"] == "admin" and user["is_active"]:
                count = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM users
                    WHERE role = 'admin' AND is_active = TRUE
                    """
                ).fetchone()["count"]

                if count <= 1:
                    raise ValueError(
                        "The last active administrator cannot be deleted"
                    )

            connection.execute(
                "DELETE FROM readings WHERE owner_user_id = %s",
                (user_id,),
            )
            connection.execute(
                "DELETE FROM sessions WHERE user_id = %s",
                (user_id,),
            )
            connection.execute(
                "DELETE FROM users WHERE id = %s",
                (user_id,),
            )

        self.audit(actor_id, "delete_account", str(user_id))

    def update_admin_reading(self, actor_id, reading_id, reading):
        columns = (
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

        values = tuple(reading[column] for column in columns) + (reading_id,)

        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE readings SET "
                + ", ".join(f"{column} = %s" for column in columns)
                + " WHERE id = %s",
                values,
            )

            if cursor.rowcount == 0:
                raise KeyError("Reading not found")

        self.audit(actor_id, "update_reading", str(reading_id))

    def delete_admin_reading(self, actor_id, reading_id):
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM readings WHERE id = %s",
                (reading_id,),
            )

            if cursor.rowcount == 0:
                raise KeyError("Reading not found")

        self.audit(actor_id, "delete_reading", str(reading_id))

    def audit(self, actor_id, action, target):
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO admin_audit
                (actor_user_id, action, target, created_at)
                VALUES (%s, %s, %s, %s)
                """,
                (actor_id, action, target, created_at),
            )

    def admin_audit_log(self):
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT admin_audit.id, admin_audit.actor_user_id,
                       admin_audit.action, admin_audit.target,
                       admin_audit.created_at,
                       users.email AS actor_email
                FROM admin_audit
                LEFT JOIN users ON users.id = admin_audit.actor_user_id
                ORDER BY admin_audit.created_at DESC, admin_audit.id DESC
                LIMIT 200
                """
            ).fetchall()

    def backup(self):
        tables = ("users", "readings", "sessions", "admin_audit")
        backup_data = {}

        with self._connect() as connection:
            for table in tables:
                rows = connection.execute(
                    f"SELECT * FROM {table} ORDER BY 1"
                ).fetchall()
                backup_data[table] = rows

        return json.dumps(
            backup_data,
            indent=2,
            default=str,
        ).encode("utf-8")
