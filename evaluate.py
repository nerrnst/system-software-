import argparse
import json
import statistics
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from http.cookiejar import CookieJar

from app import ReadingStore, make_handler


def build_reading(index):
    captured_at = datetime(2026, 10, 31, tzinfo=timezone.utc) + timedelta(
        milliseconds=index
    )
    return {
        "participant_id": f"evaluation-{index % 10:02d}",
        "device_id": "evaluation-device",
        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
        "acceleration_m_s2": {"x": 0.1, "y": 0.2, "z": 9.8},
        "angular_velocity_rad_s": {"x": 0.01, "y": 0.02, "z": 0.03},
        "heart_rate_bpm": 120,
    }


def post_json(url, payload, opener=urlopen):
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    open_request = opener.open if hasattr(opener, "open") else opener
    with open_request(request, timeout=10) as response:
        return response.status, json.load(response)


def evaluate(sample_count, batch_size):
    with tempfile.TemporaryDirectory() as temp_dir:
        store = ReadingStore(Path(temp_dir) / "evaluation.sqlite3")
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        base_url = f"http://127.0.0.1:{server.server_port}"
        opener = build_opener(HTTPCookieProcessor(CookieJar()))

        latencies_ms = []
        stored_count = 0
        try:
            status, _ = post_json(
                f"{base_url}/v1/auth/register",
                {"email": "evaluation@example.test", "password": "evaluation-password-123"},
                opener,
            )
            if status != 201:
                raise RuntimeError("Could not create evaluation account")

            for start in range(0, sample_count, batch_size):
                readings = [
                    build_reading(index)
                    for index in range(start, min(start + batch_size, sample_count))
                ]
                started = time.perf_counter()
                status, result = post_json(
                    f"{base_url}/v1/readings/batch", {"readings": readings}, opener
                )
                latencies_ms.append((time.perf_counter() - started) * 1000)
                if status != 201 or result["count"] != len(readings):
                    raise RuntimeError(f"Batch import failed: {result}")
                stored_count += result["count"]

            invalid_reading = build_reading(sample_count)
            invalid_reading["captured_at"] = "not-a-timestamp"
            try:
                post_json(
                    f"{base_url}/v1/readings/batch",
                    {"readings": [build_reading(sample_count + 1), invalid_reading]},
                    opener,
                )
            except HTTPError as error:
                if error.code != 400:
                    raise RuntimeError(f"Invalid record returned HTTP {error.code}") from error
                rejection = json.load(error)
                if rejection.get("index") != 1:
                    raise RuntimeError("Invalid batch did not identify the bad record")
            else:
                raise RuntimeError("Invalid record was unexpectedly accepted")

            with opener.open(
                f"{base_url}/v1/readings?limit={sample_count}", timeout=10
            ) as response:
                retrieved_count = len(json.load(response)["readings"])
            if retrieved_count != sample_count:
                raise RuntimeError(
                    f"Expected {sample_count} stored records, found {retrieved_count}"
                )
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    elapsed_seconds = sum(latencies_ms) / 1000
    return {
        "status": "passed",
        "records_submitted": sample_count,
        "records_stored_and_retrieved": retrieved_count,
        "invalid_batch_rejected": True,
        "batch_requests": len(latencies_ms),
        "elapsed_seconds": round(elapsed_seconds, 4),
        "throughput_records_per_second": round(sample_count / elapsed_seconds, 2),
        "batch_latency_ms": {
            "median": round(statistics.median(latencies_ms), 2),
            "maximum": round(max(latencies_ms), 2),
        },
        "note": "Local synthetic-data measurement; not a production capacity guarantee.",
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate local wearable data ingestion.")
    parser.add_argument("--samples", type=int, default=200, help="Synthetic records (1-500)")
    parser.add_argument(
        "--batch-size", type=int, default=50, help="Records per API request (1-500)"
    )
    args = parser.parse_args()
    if not 1 <= args.samples <= 500:
        parser.error("--samples must be between 1 and 500")
    if not 1 <= args.batch_size <= 500:
        parser.error("--batch-size must be between 1 and 500")
    print(json.dumps(evaluate(args.samples, args.batch_size), indent=2))


if __name__ == "__main__":
    main()
