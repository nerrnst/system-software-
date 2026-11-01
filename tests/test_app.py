import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from app import ReadingStore, make_handler


def sample_reading(**overrides):
    reading = {
        "participant_id": "athlete-01",
        "device_id": "wearable-01",
        "captured_at": "2026-10-31T08:15:30+03:00",
        "acceleration_m_s2": {"x": 0.1, "y": 0.2, "z": 9.8},
        "angular_velocity_rad_s": {"x": 0.01, "y": 0.02, "z": 0.03},
        "heart_rate_bpm": 120,
    }
    reading.update(overrides)
    return reading


class AcquisitionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ReadingStore(Path(self.temp_dir.name) / "readings.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.store))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.session_cookie = self.register("owner@example.test", "test-password-123")
        # Most test cases use the owner as an admin; separate requests test regular-user denial.
        with self.store._connect() as connection:
            connection.execute(
                "UPDATE users SET role = 'admin' WHERE email = ?",
                ("owner@example.test",),
            )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp_dir.cleanup()

    def post(self, payload):
        request = Request(
            f"{self.base_url}/v1/readings",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": self.session_cookie,
            },
            method="POST",
        )
        return urlopen(request)

    def post_batch(self, readings):
        request = Request(
            f"{self.base_url}/v1/readings/batch",
            data=json.dumps({"readings": readings}).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": self.session_cookie,
            },
            method="POST",
        )
        return urlopen(request)

    def get(self, path):
        request = Request(
            f"{self.base_url}{path}", headers={"Cookie": self.session_cookie}
        )
        return urlopen(request)

    def register(self, email, password):
        request = Request(
            f"{self.base_url}/v1/auth/register",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return response.headers["Set-Cookie"].split(";", 1)[0]

    def request_json(self, path, method="GET", payload=None, cookie=None):
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Cookie": cookie or self.session_cookie}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        return urlopen(request)

    def test_dashboard_is_served(self):
        with urlopen(self.base_url) as response:
            page = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Better movement insight starts", page)
        self.assertIn('href="./dashboard.html"', page)
        self.assertIn('id="hero-next"', page)
        self.assertIn('src="./home.js?v=3"', page)

        with urlopen(self.base_url + "/dashboard") as response:
            dashboard = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn("Add a reading", dashboard)
        self.assertIn('href="./styles.css?v=11"', page)
        self.assertIn('href="./styles.css?v=11"', dashboard)
        self.assertIn('src="./app.js?v=5"', dashboard)
        self.assertIn('id="auth-form"', dashboard)
        with urlopen(self.base_url + "/admin") as response:
            admin_page = response.read().decode()
        self.assertIn("Administration", admin_page)
        self.assertIn("id=\"users-body\"", admin_page)

    def test_information_pages_are_served(self):
        for path, marker in (
            ("/how-it-works", "Check data quality"),
            ("/about", "Research prototype—not medical software"),
        ):
            with self.subTest(path=path), urlopen(self.base_url + path) as response:
                page = response.read().decode()
                self.assertEqual(response.status, 200)
                self.assertIn(marker, page)

    def test_frontend_assets_are_served_with_content_types(self):
        for path, content_type, marker in (
            ("/styles.css?v=11", "text/css", ".admin-section"),
            ("/app.js?v=5", "text/javascript", "fetch(`/v1/readings"),
            ("/admin.js?v=1", "text/javascript", "v1/admin/overview"),
            ("/home.js?v=3", "text/javascript", "setInterval"),
        ):
            with self.subTest(path=path), urlopen(self.base_url + path) as response:
                content = response.read().decode()
                self.assertIn(content_type, response.headers["Content-Type"])
                self.assertIn("max-age=3600", response.headers["Cache-Control"])
                self.assertIn(marker, content)

        with urlopen(self.base_url + "/home.js?v=3") as response:
            script = response.read().decode()
        self.assertIn("FEATURE 02 · BATCH IMPORT", script)
        self.assertIn('creative: "validation"', script)

        with urlopen(self.base_url) as response:
            self.assertEqual(response.headers["Cache-Control"], "no-cache")

    def test_stores_and_lists_normalized_reading(self):
        response = self.post(sample_reading())
        self.assertEqual(response.status, 201)
        self.assertEqual(json.load(response)["status"], "stored")

        with self.get("/v1/readings?participant_id=athlete-01") as result:
            body = json.load(result)
        self.assertEqual(len(body["readings"]), 1)
        self.assertEqual(body["readings"][0]["captured_at"], "2026-10-31T05:15:30Z")
        self.assertEqual(body["readings"][0]["heart_rate_bpm"], 120.0)

    def test_rejects_missing_timezone(self):
        reading = sample_reading(captured_at="2026-10-31T08:15:30")
        with self.assertRaises(HTTPError) as error:
            self.post(reading)
        self.assertEqual(error.exception.code, 400)
        self.assertIn("timezone", json.load(error.exception)["error"])

    def test_rejects_invalid_sensor_axes(self):
        reading = sample_reading()
        reading["acceleration_m_s2"] = {"x": 0.1, "y": 0.2}
        with self.assertRaises(HTTPError) as error:
            self.post(reading)
        self.assertEqual(error.exception.code, 400)

    def test_limits_query_results(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/v1/readings?limit=501")
        self.assertEqual(error.exception.code, 400)

    def test_rejects_malformed_query_string(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/v1/readings?limit")
        self.assertEqual(error.exception.code, 400)

    def test_imports_multiple_readings(self):
        response = self.post_batch(
            [
                sample_reading(),
                sample_reading(
                    participant_id="athlete-02",
                    captured_at="2026-10-31T08:15:31+03:00",
                ),
            ]
        )
        self.assertEqual(response.status, 201)
        body = json.load(response)
        self.assertEqual(body["count"], 2)
        self.assertEqual(len(body["ids"]), 2)

        with self.get("/v1/readings?limit=10") as result:
            self.assertEqual(len(json.load(result)["readings"]), 2)

    def test_batch_validation_is_atomic(self):
        invalid = sample_reading(captured_at="not-a-timestamp")
        with self.assertRaises(HTTPError) as error:
            self.post_batch([sample_reading(), invalid])
        self.assertEqual(error.exception.code, 400)
        self.assertEqual(json.load(error.exception)["index"], 1)

        with self.get("/v1/readings") as result:
            self.assertEqual(json.load(result)["readings"], [])

    def test_rejects_empty_batch(self):
        with self.assertRaises(HTTPError) as error:
            self.post_batch([])
        self.assertEqual(error.exception.code, 400)

    def test_reading_api_requires_authentication(self):
        for path, method, payload in (
            ("/v1/readings", "GET", None),
            ("/v1/readings", "POST", sample_reading()),
            ("/v1/readings/batch", "POST", {"readings": [sample_reading()]}),
        ):
            request = Request(
                f"{self.base_url}{path}",
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            with self.subTest(path=path, method=method), self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 401)

    def test_users_can_only_read_their_own_records(self):
        self.post(sample_reading())
        second_cookie = self.register("second@example.test", "another-password-456")
        second_post = Request(
            f"{self.base_url}/v1/readings",
            data=json.dumps(sample_reading()).encode(),
            headers={
                "Content-Type": "application/json",
                "Cookie": second_cookie,
            },
            method="POST",
        )
        with urlopen(second_post) as response:
            self.assertEqual(response.status, 201)

        request = Request(
            f"{self.base_url}/v1/readings",
            headers={"Cookie": second_cookie},
        )
        with urlopen(request) as response:
            second_readings = json.load(response)["readings"]
        self.assertEqual(len(second_readings), 1)
        self.assertEqual(second_readings[0]["participant_id"], "athlete-01")

        request = Request(
            f"{self.base_url}/v1/readings",
            headers={"Cookie": self.session_cookie},
        )
        with urlopen(request) as response:
            first_readings = json.load(response)["readings"]
        self.assertEqual(len(first_readings), 1)
        self.assertEqual(first_readings[0]["participant_id"], "athlete-01")

    def test_login_and_logout_manage_session_cookie(self):
        request = Request(
            f"{self.base_url}/v1/auth/login",
            data=json.dumps(
                {"email": "owner@example.test", "password": "test-password-123"}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            login_cookie = response.headers["Set-Cookie"]
            self.assertIn("HttpOnly", login_cookie)
            self.assertIn("SameSite=Strict", login_cookie)
            self.assertEqual(json.load(response)["user"]["email"], "owner@example.test")

        request = Request(
            f"{self.base_url}/v1/auth/logout",
            headers={"Cookie": login_cookie.split(";", 1)[0]},
            data=b"",
            method="POST",
        )
        with urlopen(request) as response:
            self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        request = Request(
            f"{self.base_url}/v1/readings",
            headers={"Cookie": login_cookie.split(";", 1)[0]},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 401)

    def test_registration_rejects_weak_password_and_duplicate_email(self):
        for email, password, expected in (
            ("weak@example.test", "short", "between 12"),
            ("OWNER@example.test", "new-password-123", "already exists"),
        ):
            request = Request(
                f"{self.base_url}/v1/auth/register",
                data=json.dumps({"email": email, "password": password}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self.subTest(email=email), self.assertRaises(HTTPError) as error:
                urlopen(request)
            self.assertEqual(error.exception.code, 400)
            self.assertIn(expected, json.load(error.exception)["error"])

    def test_existing_unowned_readings_are_preserved_but_not_exposed(self):
        database = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(database) as connection:
            connection.execute(
                """
                CREATE TABLE readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
            connection.execute(
                """
                INSERT INTO readings (
                    participant_id, device_id, captured_at, acceleration_m_s2_x,
                    acceleration_m_s2_y, acceleration_m_s2_z,
                    angular_velocity_rad_s_x, angular_velocity_rad_s_y,
                    angular_velocity_rad_s_z, heart_rate_bpm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("legacy-athlete", "legacy-device", "2026-10-31T08:15:30Z",
                 0.1, 0.2, 9.8, 0.01, 0.02, 0.03, 120),
            )

        store = ReadingStore(database)
        user = store.create_user("new-owner@example.test", "migrated-password-123")
        self.assertEqual(store.list(owner_user_id=user["id"]), [])
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT participant_id, owner_user_id FROM readings"
            ).fetchone()
        self.assertEqual(row, ("legacy-athlete", None))

    def test_admin_endpoints_require_admin_role(self):
        user_cookie = self.register("regular@example.test", "regular-password-123")
        with self.assertRaises(HTTPError) as error:
            self.request_json("/v1/admin/overview", cookie=user_cookie)
        self.assertEqual(error.exception.code, 403)

        with self.assertRaises(HTTPError) as error:
            urlopen(f"{self.base_url}/v1/admin/users")
        self.assertEqual(error.exception.code, 401)

    def test_admin_manages_users_and_can_revoke_sessions(self):
        user_cookie = self.register("managed@example.test", "managed-password-123")
        users = json.load(self.request_json("/v1/admin/users"))["users"]
        managed = next(user for user in users if user["email"] == "managed@example.test")
        self.request_json(
            f"/v1/admin/users/{managed['id']}",
            method="PATCH",
            payload={"is_premium": True},
        )
        updated = next(
            user for user in json.load(self.request_json("/v1/admin/users"))["users"]
            if user["id"] == managed["id"]
        )
        self.assertTrue(updated["is_premium"])

        self.request_json(
            f"/v1/admin/users/{managed['id']}",
            method="PATCH",
            payload={"is_active": False},
        )
        with self.assertRaises(HTTPError) as error:
            self.request_json("/v1/readings", cookie=user_cookie)
        self.assertEqual(error.exception.code, 401)

    def test_admin_cannot_remove_the_last_admin(self):
        with self.assertRaises(HTTPError) as error:
            self.request_json(
                "/v1/admin/users/1",
                method="PATCH",
                payload={"is_active": False},
            )
        self.assertEqual(error.exception.code, 400)
        self.assertIn("own administrator access", json.load(error.exception)["error"])

    def test_admin_can_edit_delete_readings_and_download_backup(self):
        reading_id = json.load(self.post(sample_reading()))["id"]
        updated = sample_reading(participant_id="corrected-athlete")
        self.request_json(
            f"/v1/admin/readings/{reading_id}",
            method="PUT",
            payload=updated,
        )
        result = json.load(self.request_json("/v1/admin/readings"))["readings"]
        edited = next(reading for reading in result if reading["id"] == reading_id)
        self.assertEqual(edited["participant_id"], "corrected-athlete")
        self.assertEqual(edited["owner_email"], "owner@example.test")

        backup_request = Request(
            f"{self.base_url}/v1/admin/backup",
            headers={"Cookie": self.session_cookie},
        )
        with urlopen(backup_request) as response:
            backup = response.read()
        self.assertTrue(backup.startswith(b"SQLite format 3"))

        self.request_json(f"/v1/admin/readings/{reading_id}", method="DELETE")
        result = json.load(self.request_json("/v1/admin/readings"))["readings"]
        self.assertFalse(any(reading["id"] == reading_id for reading in result))

    def test_admin_can_create_account_and_reset_password_once(self):
        response = self.request_json(
            "/v1/admin/users",
            method="POST",
            payload={
                "email": "created@example.test",
                "password": "created-password-123",
                "role": "user",
                "is_premium": True,
            },
        )
        user_id = json.load(response)["id"]
        reset = json.load(
            self.request_json(
                f"/v1/admin/users/{user_id}/reset-password",
                method="POST",
                payload={},
            )
        )
        self.assertGreaterEqual(len(reset["temporary_password"]), 32)
        events = json.load(self.request_json("/v1/admin/audit"))["events"]
        self.assertIn("reset_account_password", [event["action"] for event in events])


if __name__ == "__main__":
    unittest.main()
