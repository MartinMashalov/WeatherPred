"""Three registered IEM measurement canaries; acquisition only, no scoring.

This tests the separately documented request shape and raw CSV availability.
It does not authorize the proposed 242-day acquisition or model fitting.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

PROPOSAL = Path("reports/minute_observation_proposal.json")
PROPOSAL_SHA = "33155e9a0c91cf3d4c267e0ad206013ec7d9f32a81dc1498f4d1497a021ee535"
NAME = "E028-three-IEM-minute-measurement-canaries-v1"
URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py"
FILES = [
    Path(__file__),
    PROPOSAL,
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
    Path("pyproject.toml"),
    Path("uv.lock"),
]


def sha(body):
    return hashlib.sha256(body).hexdigest()


def pins():
    return {str(p): sha(p.read_bytes()) for p in FILES}


def register(archive):
    if archive.latest("e028_protocol", NAME):
        raise ValueError("Canary already registered; no silent new attempt")
    raw = PROPOSAL.read_bytes()
    if sha(raw) != PROPOSAL_SHA:
        raise ValueError("Original three-canary proposal changed")
    requests = json.loads(raw)["canary_requests"]
    if len(requests) != 3 or any(
        r["method"] != "GET" or not r["url"].startswith(URL + "?") for r in requests
    ):
        raise ValueError("Expected exactly the three declared canaries")
    hashes = pins()
    sources = {str(p): archive.append("e028_source", str(p), utcnow(), {}, p.read_bytes()) for p in FILES}
    protocol = {
        "experiment": NAME,
        "requests": requests,
        "source_hashes": hashes,
        "source_record_ids": sources,
        "httpx_version": httpx.__version__,
        "cwd": str(Path.cwd()),
        "per_response_payload_limit": 1048576,
        "overflow_detection_bytes": 16384,
        "maximum_physical_attempts": 9,
        "minimum_spacing_seconds": 1,
        "wall_budget_seconds": 300,
        "request_timeout_seconds": 30,
        "stop_file": "data/STOP_E028",
        "measurement_canaries_authorized": True,
        "bulk_acquisition_authorized": False,
        "model_fitting_authorized": False,
        "historical_public_availability_verified": False,
        "scores_computed": 0,
    }
    return archive.append("e028_protocol", NAME, utcnow(), {}, canonical(protocol).encode())


def run(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None or row["kind"] != "e028_protocol":
        raise ValueError("Missing canary registration")
    body = archive.body(row)
    if sha(body) != row["body_sha256"]:
        raise ValueError("Canary registration changed")
    protocol = json.loads(body)
    if (
        pins() != protocol["source_hashes"]
        or httpx.__version__ != protocol["httpx_version"]
        or str(Path.cwd()) != protocol["cwd"]
    ):
        raise ValueError("Canary source/environment changed")
    if archive.latest("e028_started", str(identifier)):
        raise ValueError("Canary attempt already exists; preserve its success or failure")
    archive.append("e028_started", str(identifier), utcnow(), {}, b"{}")
    deadline, next_at = time.monotonic() + protocol["wall_budget_seconds"], 0.0
    results, physical_attempts = [], 0
    try:
        with httpx.Client(
            timeout=protocol["request_timeout_seconds"],
            follow_redirects=False,
            headers={
                "User-Agent": "WeatherPred-E028/1.0 finite public canary",
                "Accept-Encoding": "identity",
            },
        ) as client:
            for request in protocol["requests"]:
                for attempt in range(3):
                    if time.monotonic() >= deadline or Path(protocol["stop_file"]).exists():
                        raise TimeoutError("Canary deadline/stop reached")
                    time.sleep(max(0, next_at - time.monotonic()))
                    next_at = time.monotonic() + protocol["minimum_spacing_seconds"]
                    physical_attempts += 1
                    if physical_attempts > protocol["maximum_physical_attempts"]:
                        raise ValueError("Canary request budget exhausted")
                    started = utcnow()
                    intent = archive.append(
                        "e028_request_started",
                        f"{identifier}:{request['key']}",
                        started,
                        {
                            "url": URL,
                            "params": request["params"],
                            "attempt": attempt,
                            "reserved_bytes": protocol["per_response_payload_limit"]
                            + protocol["overflow_detection_bytes"],
                        },
                        b"",
                    )
                    content, headers, status, error = bytearray(), {}, None, None
                    try:
                        with client.stream("GET", URL, params=request["params"]) as response:
                            headers, status = dict(response.headers), response.status_code
                            if headers.get("content-encoding", "identity") != "identity":
                                raise ValueError("Unexpected encoded response")
                            length = int(headers["content-length"]) if "content-length" in headers else None
                            if (
                                length is not None
                                and not 0 <= length <= protocol["per_response_payload_limit"]
                            ):
                                raise ValueError("Canary response exceeds byte budget")
                            for chunk in response.iter_raw(chunk_size=16384):
                                content.extend(chunk)
                                if len(content) > protocol["per_response_payload_limit"]:
                                    raise ValueError("Canary streaming payload limit")
                                if time.monotonic() >= deadline or Path(protocol["stop_file"]).exists():
                                    raise TimeoutError("Canary deadline/stop reached in response")
                            if length is not None and len(content) != length:
                                raise ValueError("Truncated canary response")
                    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
                        error = f"{type(exc).__name__}: {exc}"
                    received = utcnow()
                    meta = {
                        "request_started_record_id": intent,
                        "url": URL,
                        "params": request["params"],
                        "request_started_at": started.isoformat(),
                        "actual_received_at": received.isoformat(),
                        "headers": headers,
                        "status": status,
                        "error": error,
                        "bytes": len(content),
                        "attempt": attempt,
                    }
                    rec = archive.append(
                        "e028_http", f"{identifier}:{request['key']}", received, meta, bytes(content)
                    )
                    results.append(
                        {
                            "key": request["key"],
                            "record_id": rec,
                            "status": status,
                            "error": error,
                            "bytes": len(content),
                            "body_sha256": sha(content),
                        }
                    )
                    retryable = (
                        status == 429
                        or (status is not None and status >= 500)
                        or (
                            status is None
                            and error is not None
                            and error.startswith(
                                ("Connect", "Read", "Write", "RemoteProtocol", "PoolTimeout")
                            )
                        )
                    )
                    if not retryable or attempt == 2:
                        break
                    pause = 2**attempt
                    if status == 429:
                        try:
                            pause = max(pause, float(headers.get("retry-after", pause)))
                        except ValueError:
                            raise ValueError("Cannot honor unknown Retry-After within this canary") from None
                    if time.monotonic() + pause >= deadline:
                        raise TimeoutError("Retry-After exceeds the registered canary deadline")
                    time.sleep(pause)
                if status in (401, 403):
                    break
        if pins() != protocol["source_hashes"]:
            raise ValueError("Canary sources changed during acquisition")
        report = {
            "protocol_record_id": identifier,
            "physical_attempts": physical_attempts,
            "responses": results,
            "measurement_values_normalized": 0,
            "model_fits": 0,
            "scores_computed": 0,
            "bulk_acquisition_authorized": False,
            "historical_public_availability_verified": False,
        }
        report_id = archive.append("e028_report", str(identifier), utcnow(), {}, canonical(report).encode())
        report["report_record_id"] = report_id
        Path(f"reports/E028_canary_{identifier}.json").write_text(json.dumps(report, indent=2) + "\n")
        return report
    except BaseException as exc:
        archive.append(
            "e028_failed",
            str(identifier),
            utcnow(),
            {},
            canonical(
                {"error": str(exc), "responses": results, "physical_attempts": physical_attempts}
            ).encode(),
        )
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        print(
            canonical(
                {"protocol_record_id": register(archive), "network_requests": 0}
                if args.register
                else run(archive, args.run_record_id)
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
