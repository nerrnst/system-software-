# StrideSignal Research Prototype

A local web application for collecting, validating, and reviewing raw wearable
readings. It accepts accelerometer, gyroscope, and optional heart-rate
measurements and stores them in SQLite. It does not assess injury risk,
diagnose conditions, or provide medical advice.

## Run

Requires Python 3.9 or later; no third-party packages are needed.

```sh
python3 app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) for the home page.
Navigate to `/dashboard` to submit or review readings, `/how-it-works` for the
data flow, and `/about` for project scope and limitations. The frontend uses
plain HTML, CSS, and JavaScript in `static/`. Use these pages through the local
server for API features rather than opening the files directly. By default,
the server listens only on `127.0.0.1:8000` and writes to `readings.sqlite3`.
Set `PORT` or `ACQUISITION_DB` to change those values.
The homepage hero rotates through three feature promotions (movement capture,
batch import, and data validation) every 6.5 seconds, with
manual controls and automatic pausing on hover, keyboard focus, hidden tabs, and
reduced-motion settings. Its animated sensor graphic is illustrative, not live
wearable data.

## Accounts and private readings

Create an account or sign in from the dashboard. Accounts are open for this
prototype; each new account can view only readings saved while signed in to that
account. Passwords are stored as salted PBKDF2-HMAC-SHA256 hashes, and the
12-hour session cookie is HTTP-only and same-site. Sign out from the dashboard
to end the current session.

The reading API requires the session cookie. The server assigns ownership from
the authenticated session; client-supplied participant IDs do not grant access
to another account's records. Existing database rows from before accounts were
added have no known owner and remain unassigned and hidden rather than being
silently assigned to the first person who registers.

## Administrator access

The admin page is available at `/admin` to active administrator accounts only.
Administrators can create accounts, grant/revoke the Premium status, promote or
demote admins, suspend/reactivate accounts, issue a one-time password reset,
and delete accounts. Deleting an account also permanently deletes its readings.
Admins can view and edit/delete readings for any account, see database totals,
download a complete SQLite backup, and review the recent admin activity log.
The Premium value is currently only an account status flag; no premium-only
product feature or payment processing is implemented.

Create the first administrator from the app directory. This command uses the
same database configured by `ACQUISITION_DB` and prints a randomly generated
password exactly once:

```sh
python3 app.py --create-admin admin@stridesignal.local
```

Save that password securely and sign in at `/dashboard`; the Admin link appears
for administrator accounts. Further admins can be promoted from the admin page.
The bootstrap command refuses to create another first admin once an active
administrator exists. Keep downloaded database backups private because they
contain account password hashes, sessions, and sensor data. The admin activity
log records changes and backup downloads, but does not store reset passwords.

## Authentication API

- `POST /v1/auth/register` with `{"email":"person@example.com","password":"at-least-12-characters"}`
  creates an account and signs it in.
- `POST /v1/auth/login` with the same fields signs in and issues a new session.
- `GET /v1/auth/me` returns the signed-in account.
- `POST /v1/auth/logout` revokes the current session.

Registration requires a valid email address and a password of 12-1024
characters. Authentication and readings are intended for local prototype use
only; the server does not provide HTTPS, production rate limiting, email-based
account recovery, or production health-data safeguards.

## Send a reading

`POST /v1/readings` accepts one JSON object:

```json
{
  "participant_id": "athlete-01",
  "device_id": "wearable-01",
  "captured_at": "2026-10-31T08:15:30Z",
  "acceleration_m_s2": {"x": 0.1, "y": 0.2, "z": 9.8},
  "angular_velocity_rad_s": {"x": 0.01, "y": 0.02, "z": 0.03},
  "heart_rate_bpm": 120
}
```

The timestamp must be ISO 8601 with a timezone. The API validates the record
and responds with `201` and its stored ID, or `400` with a validation error.
Heart rate is optional.

## Import a batch

`POST /v1/readings/batch` accepts a JSON object with a `readings` array. The
same per-reading fields and validation rules apply as for a single reading:

```json
{
  "readings": [
    {
      "participant_id": "athlete-01",
      "device_id": "wearable-01",
      "captured_at": "2026-10-31T08:15:30Z",
      "acceleration_m_s2": {"x": 0.1, "y": 0.2, "z": 9.8},
      "angular_velocity_rad_s": {"x": 0.01, "y": 0.02, "z": 0.03},
      "heart_rate_bpm": 120
    }
  ]
}
```

Batch imports are validated before storage, so a validation error rejects the
entire batch and includes the invalid item index. A batch can contain at most
500 readings; request bodies are limited to 64 KiB.

All reading endpoints require the session cookie issued by registration or
login. `GET /v1/readings` lists the latest 100 readings belonging to the signed
in account. Use
`?participant_id=athlete-01` to filter or `?limit=200` to change the result
count (maximum 500).

## Test

```sh
python3 -m unittest discover -s tests -v
```

## Evaluate acquisition performance

Run a repeatable local integration check with generated sensor readings. It
reports records per second and batch-request latency, verifies records can be
retrieved, and checks that invalid batches are rejected:

```sh
python3 evaluate.py
```

Use `--samples` (1-500) and `--batch-size` (1-500) to adjust the workload. This
measurement uses synthetic data on localhost; it is not a production capacity
guarantee or an evaluation of injury-risk detection accuracy.

## Prototype limitations

This prototype has no wearable pairing/Bluetooth driver, HTTPS, account
recovery, retention policy, or production deployment safeguards. It is bound to
localhost intentionally. Do not expose it to a network or use it with
identifiable or real-world health data without adding appropriate security,
consent, privacy, and data-governance controls. A device-specific adapter can
be added once the wearable hardware and its data format are known.
