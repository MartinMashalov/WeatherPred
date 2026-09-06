"""Finite registered NBH acquisition. Preparation/registration make no HTTP calls."""

import argparse
import fcntl
import hashlib
import json
import shutil
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.nbh import merged_ranges, parse_cards
from weatherpred.timeutil import iso, utcnow

CONFIG = Path("config/e025_physical_baseline.json")
HOUR = 3_600_000
NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
SOURCE_PATHS = [
    "research/experiments/e025_acquire_nbh.py",
    "weatherpred/nbh.py",
    "weatherpred/archive.py",
    "weatherpred/timeutil.py",
    str(CONFIG),
    "tests/test_nbh.py",
    "tests/test_e025_acquire_nbh.py",
    "pyproject.toml",
    "uv.lock",
]


class AcquisitionPause(RuntimeError):
    """Registered resource bound or operator stop; resume without a new protocol."""


def sha(body):
    return hashlib.sha256(body).hexdigest()


def checked_body(archive, record):
    body = archive.body(record)
    if sha(body) != record["body_sha256"]:
        raise ValueError("Archived source bytes changed")
    return body


def make_manifest(config):
    raw = Path(config["manifest_path"]).read_bytes()
    if sha(raw) != config["manifest_sha256"]:
        raise ValueError("E022 manifest hash mismatch")
    if config["run_lag_hours"] != 2 or config["station_lag_minutes"] != 15:
        raise ValueError("Unregistered run/station lag")
    bindings = []
    for case in json.loads(raw)["cases"]:
        run_ms = case["decision_ms"] - 2 * HOUR
        run = datetime.fromtimestamp(run_ms / 1000, UTC)
        bindings.append(
            {
                "case_id": case["case_id"],
                "station_id": case["station_id"],
                "split": case["split"],
                "target_ms": case["target_ms"],
                "decision_ms": case["decision_ms"],
                "run_ms": run_ms,
                "forecast_hour": (case["target_ms"] - run_ms) // HOUR,
                "key": f"blend.{run:%Y%m%d}/{run:%H}/text/blend_nbhtx.t{run:%H}z",
            }
        )
    bindings.sort(key=lambda row: row["case_id"])
    if sha(canonical(bindings).encode()) != config["bindings_sha256"]:
        raise ValueError("Case-to-object bindings differ from declared proposal")
    if len(bindings) != config["expected_cases"] or len({b["case_id"] for b in bindings}) != len(bindings):
        raise ValueError("Wrong or duplicate case count")
    if dict(Counter(b["split"] for b in bindings)) != config["expected_split_cases"]:
        raise ValueError("Wrong split counts")
    if sorted({b["station_id"] for b in bindings}) != config["stations"]:
        raise ValueError("Station universe changed")
    objects = {}
    for binding in bindings:
        obj = objects.setdefault(
            binding["key"],
            {
                "key": binding["key"],
                "run_ms": binding["run_ms"],
                "cases": [],
            },
        )
        obj["cases"].append(binding)
    if len(objects) != config["expected_objects"]:
        raise ValueError("Wrong object count")
    return {"bindings": bindings, "objects": [objects[k] for k in sorted(objects)]}


def register(archive, config):
    manifest = make_manifest(config)
    source_records, hashes = {}, {}
    for name in SOURCE_PATHS + [config["manifest_path"]]:
        body = Path(name).read_bytes()
        hashes[name] = sha(body)
        source_records[name] = archive.append("e025_source", name, utcnow(), {}, body)
    protocol = {
        "config": config,
        "source_sha256": hashes,
        "source_record_ids": source_records,
        "working_directory": str(Path.cwd().resolve()),
        "archive_root": str(archive.root.resolve()),
        "httpx_version": httpx.__version__,
        "manifest": manifest,
        "registered_at": iso(utcnow()),
        "forecast_downloads_before_registration": 0,
        "scoring_authorized": False,
    }
    return archive.append("e025_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode())


def load_protocol(archive, registration_id):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (registration_id,)).fetchone()
    if row is None or row["kind"] != "e025_protocol":
        raise ValueError("Expected an E025 registration record")
    protocol = json.loads(checked_body(archive, row))
    if protocol["working_directory"] != str(Path.cwd().resolve()):
        raise ValueError("Registered working directory changed")
    if (
        protocol["archive_root"] != str(archive.root.resolve())
        or protocol["httpx_version"] != httpx.__version__
    ):
        raise ValueError("Registered archive/environment changed")
    verify_source_hashes(protocol)
    if make_manifest(protocol["config"]) != protocol["manifest"]:
        raise ValueError("Registered manifest cannot be reproduced")
    return protocol


def verify_source_hashes(protocol):
    """Recheck bytes without rebuilding the full case manifest at each object."""
    for name, expected in protocol["source_sha256"].items():
        if sha(Path(name).read_bytes()) != expected:
            raise ValueError("Registered source/input changed: " + name)


class BoundedClient:
    """GET-only payload-bounded transport with durable interrupted-request costs."""

    def __init__(self, archive, registration_id, config, *, transport=None):
        self.archive, self.registration_id, self.config = archive, registration_id, config
        self.prefix = f"{registration_id}:"
        self.deadline = time.monotonic() + config["maximum_invocation_seconds"]
        self.next_request = 0.0
        self.client = httpx.Client(
            timeout=config["request_timeout_seconds"],
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "WeatherPred-E025/1.0 public research", "Accept-Encoding": "identity"},
        )
        self.used = self.restore_budget()

    def close(self):
        self.client.close()

    def restore_budget(self):
        starts, finished = {}, {}
        rows = self.archive.db.execute(
            "SELECT * FROM records WHERE kind IN ('e025_request_started','e025_response') AND key LIKE ?",
            (self.prefix + "%",),
        )
        for row in rows:
            meta = json.loads(row["metadata"])
            if row["kind"] == "e025_request_started":
                starts[row["id"]] = meta["reserved_payload_bytes"]
            else:
                start = meta["request_started_record_id"]
                if start in finished:
                    raise ValueError("Duplicate request completion in budget ledger")
                finished[start] = len(checked_body(self.archive, row))
        if not set(finished) <= set(starts):
            raise ValueError("Completion without request reservation")
        return sum(finished.get(identity, reserved) for identity, reserved in starts.items())

    def check_pause(self, reservation=0):
        if (self.archive.root / self.config["stop_file"]).exists():
            raise AcquisitionPause("stop_file")
        if time.monotonic() >= self.deadline:
            raise AcquisitionPause("invocation_time_budget")
        if shutil.disk_usage(self.archive.root).free < self.config["minimum_free_disk_bytes"]:
            raise AcquisitionPause("minimum_free_disk")
        if self.used + reservation > self.config["maximum_total_payload_bytes"]:
            raise AcquisitionPause("total_payload_budget")

    def get(self, key, role, *, params=None, obj=None, byte_range=None):
        url = self.config["bucket"] + (key if params is None else "")
        headers = {}
        if obj is not None:
            headers["If-Match"] = obj["etag"]
        if byte_range is not None:
            headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
        cap = (
            65536
            if obj is None
            else (byte_range[1] - byte_range[0] + 1 if byte_range is not None else obj["size"])
        )
        if cap <= 0 or cap > self.config["maximum_object_bytes"]:
            raise ValueError("Request exceeds object payload ceiling")
        # Reserve one bounded chunk for detecting a server that exceeds its
        # advertised body length. Even rejected bytes count toward the budget.
        reservation = cap + 16384
        signature = sha(canonical({"url": url, "params": params, "headers": headers, "cap": cap}).encode())
        identity = self.prefix + key + ":" + role
        cached = self.archive.latest("e025_response", identity)
        if cached is not None:
            meta = json.loads(cached["metadata"])
            if meta["request_signature"] != signature:
                raise ValueError("Cached request signature changed")
            if meta["complete"]:
                return checked_body(self.archive, cached), meta, cached["id"]
        prior_attempts = self.archive.db.execute(
            "SELECT count(*) FROM records WHERE kind='e025_request_started' AND key=?",
            (identity,),
        ).fetchone()[0]
        for attempt in range(prior_attempts, self.config["maximum_attempts_per_request"]):
            self.check_pause(reservation)
            delay = max(0, self.next_request - time.monotonic())
            if delay:
                time.sleep(delay)
            self.check_pause(reservation)
            self.next_request = time.monotonic() + self.config["request_interval_seconds"]
            started = utcnow()
            intent = self.archive.append(
                "e025_request_started",
                identity,
                started,
                {
                    "request_signature": signature,
                    "reserved_payload_bytes": reservation,
                    "url": url,
                    "params": params,
                    "request_headers": headers,
                    "attempt_in_invocation": attempt,
                    "request_started_at": iso(started),
                },
                b"",
            )
            self.used += reservation
            body, status, response_headers, error = bytearray(), None, {}, None
            try:
                with self.client.stream("GET", url, params=params, headers=headers) as response:
                    status, response_headers = response.status_code, dict(response.headers)
                    validate_headers(status, response_headers, obj, byte_range, cap)
                    for chunk in response.iter_raw(chunk_size=16384):
                        body.extend(chunk)
                        if len(body) > cap:
                            raise ValueError("Response exceeds reserved payload")
                        self.check_pause()
                    length = response_headers.get("content-length")
                    if length is not None and len(body) != int(length):
                        raise ValueError("Truncated response payload")
            except (httpx.HTTPError, ValueError, AcquisitionPause) as exc:
                error = str(exc)
                failure = exc
            received = utcnow()
            metadata = {
                "url": url,
                "params": params,
                "request_headers": headers,
                "request_signature": signature,
                "request_started_record_id": intent,
                "request_started_at": iso(started),
                "actual_received_at": iso(received),
                "status": status,
                "headers": response_headers,
                "complete": error is None,
                "error": error,
                "payload_bytes": len(body),
                "raw_sha256": sha(bytes(body)),
            }
            rec = self.archive.append("e025_response", identity, received, metadata, bytes(body))
            self.used -= reservation - len(body)
            if error is None:
                return bytes(body), metadata, rec
            if isinstance(failure, AcquisitionPause):
                raise failure
            retryable = isinstance(failure, httpx.TransportError) or status == 429 or (status or 0) >= 500
            if not retryable or attempt + 1 == self.config["maximum_attempts_per_request"]:
                raise ValueError(f"Acquisition request failed: {error}; source_record={rec}") from failure
            self.check_pause()
            time.sleep(2**attempt)
        raise ValueError("Registered request attempt limit already exhausted")


def validate_headers(status, headers, obj, byte_range, cap):
    expected_status = 206 if byte_range is not None else 200
    if status != expected_status:
        raise ValueError(f"Unexpected HTTP status {status}, expected {expected_status}")
    raw_length = headers.get("content-length")
    length = int(raw_length) if raw_length is not None else None
    if length is None and obj is not None:
        raise ValueError("Missing object Content-Length")
    if length is not None and not 0 <= length <= cap:
        raise ValueError("Missing/excess Content-Length")
    if headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("Unexpected encoded response")
    if obj is None:
        return
    if headers.get("etag") != obj["etag"]:
        raise ValueError("Object ETag changed")
    modified = parsedate_to_datetime(headers.get("last-modified", ""))
    if int(modified.timestamp() * 1000) != obj["last_modified_ms"]:
        raise ValueError("Object Last-Modified changed")
    if byte_range is not None:
        expected = f"bytes {byte_range[0]}-{byte_range[1]}/{obj['size']}"
        if headers.get("content-range") != expected or length != cap:
            raise ValueError("Content-Range differs from registered object/range")
    elif length != obj["size"]:
        raise ValueError("Full object size changed")


def listing(client, entry):
    body, meta, rec = client.get(
        entry["key"],
        "listing",
        params={
            "list-type": "2",
            "prefix": entry["key"],
            "max-keys": 2,
        },
    )
    root = ET.fromstring(body)
    matches = [
        n for n in root.findall("s:Contents", NS) if n.findtext("s:Key", namespaces=NS) == entry["key"]
    ]
    if root.findtext("s:IsTruncated", namespaces=NS) != "false" or len(matches) != 1:
        raise ValueError("Missing, duplicate or truncated exact-object listing")
    node = matches[0]
    modified = node.findtext("s:LastModified", namespaces=NS)
    if not modified:
        raise ValueError("Missing Last-Modified in original listing")
    modified_time = datetime.fromisoformat(modified)
    if modified_time.tzinfo is None:
        raise ValueError("Missing Last-Modified timezone")
    modified_ms = int(modified_time.timestamp() * 1000)
    obj = {
        "key": entry["key"],
        "run_ms": entry["run_ms"],
        "last_modified": modified,
        "last_modified_ms": modified_ms,
        "etag": node.findtext("s:ETag", namespaces=NS),
        "size": int(node.findtext("s:Size", default="", namespaces=NS)),
        "listing_record_id": rec,
        "listing_actual_received_at": meta["actual_received_at"],
    }
    if not obj["etag"] or not 0 < obj["size"] <= client.config["maximum_object_bytes"]:
        raise ValueError("Invalid ETag or object size")
    if any(modified_ms > c["decision_ms"] for c in entry["cases"]):
        raise ValueError("Object stored after registered decision")
    return obj


def authorize_full(client, key, seed):
    identity = client.prefix + key
    existing = client.archive.latest("e025_full_authorization", identity)
    if existing:
        return json.loads(checked_body(client.archive, existing))["reason"]
    rows = client.archive.db.execute(
        "SELECT * FROM records WHERE kind='e025_full_authorization' AND key LIKE ?",
        (client.prefix + "%",),
    ).fetchall()
    reasons = [json.loads(checked_body(client.archive, row))["reason"] for row in rows]
    reason = "seed" if seed and "seed" not in reasons else "recovery"
    if reason == "recovery" and reasons.count("recovery") >= client.config["full_recovery_cap"]:
        raise AcquisitionPause("full_object_recovery_cap")
    client.archive.append(
        "e025_full_authorization", identity, utcnow(), {}, canonical({"reason": reason}).encode()
    )
    return reason


def acquire_object(client, entry, offsets):
    obj = listing(client, entry)
    stations = set(client.config["stations"])
    cards, sources, range_failure, full_reason = {}, [], None, None
    if stations <= set(offsets):
        try:
            ranges = merged_ranges(
                [offsets[s] for s in stations], obj["size"], client.config["range_radius_bytes"]
            )
            for low, high in ranges:
                body, meta, rec = client.get(
                    entry["key"], f"range:{low}:{high}", obj=obj, byte_range=(low, high)
                )
                # Wanted headers close to an edge can belong to another planned range.
                wanted = {s for s in stations if low <= offsets[s] <= high}
                found = parse_cards(
                    body,
                    wanted,
                    expected_run_ms=entry["run_ms"],
                    body_offset=low,
                    versions=tuple(client.config["versions"]),
                )
                for station in found:
                    if station in cards:
                        raise ValueError("Duplicate card across disjoint ranges")
                cards.update(found)
                sources.append(source_summary(rec, meta))
            if set(cards) != stations:
                raise ValueError("Missing station cards in requested ranges")
        except (ValueError, ET.ParseError) as exc:
            range_failure = str(exc)
    if set(cards) != stations:
        full_reason = authorize_full(client, entry["key"], seed=not offsets)
        body, meta, rec = client.get(entry["key"], "full", obj=obj)
        sources.append(source_summary(rec, meta))
        cards = parse_cards(
            body, stations, expected_run_ms=entry["run_ms"], versions=tuple(client.config["versions"])
        )
        if set(cards) != stations:
            raise ValueError("Missing exact station cards after full-object acquisition")
    cases = []
    for case in entry["cases"]:
        card = cards[case["station_id"]]
        target = next((row for row in card["rows"] if row["valid_ms"] == case["target_ms"]), None)
        if target is None or target["forecast_hour"] != case["forecast_hour"]:
            raise ValueError("Exact target hour absent or inconsistent")
        good = target["tmp_f"] is not None
        cases.append(
            {
                **case,
                "available": good,
                "point_f": target["tmp_f"],
                "reason": None if good else "missing_tmp",
                "card_sha256": card["raw_card_sha256"],
                "last_modified_ms": obj["last_modified_ms"],
                "historical_public_availability_verified": False,
            }
        )
    return {
        "key": entry["key"],
        "status": "acquired",
        "object": obj,
        "cases": cases,
        "cards": {s: {k: v for k, v in card.items() if k != "rows"} for s, card in cards.items()},
        "sources": sources,
        "range_failure": range_failure,
        "full_object_reason": full_reason,
        "historical_public_availability_verified": False,
        "scores_computed": False,
    }


def source_summary(rec, meta):
    return {
        "record_id": rec,
        "raw_sha256": meta["raw_sha256"],
        "headers": meta["headers"],
        "request_headers": meta["request_headers"],
        "actual_received_at": meta["actual_received_at"],
    }


def run(archive, registration_id, report_dir):
    protocol = load_protocol(archive, registration_id)
    config, entries = protocol["config"], protocol["manifest"]["objects"]
    results, offsets, newly_attempted, stop = [], {}, 0, "all_objects_attempted"
    client = BoundedClient(archive, registration_id, config)
    try:
        for entry in entries:
            identity = client.prefix + entry["key"]
            existing = archive.latest("e025_object", identity)
            if existing:
                result = json.loads(checked_body(archive, existing))
                record_id = existing["id"]
            else:
                verify_source_hashes(protocol)
                try:
                    client.check_pause()
                    if newly_attempted >= config["maximum_objects_per_invocation"]:
                        raise AcquisitionPause("invocation_object_batch")
                    newly_attempted += 1
                    result = acquire_object(client, entry, offsets)
                except AcquisitionPause as exc:
                    stop = str(exc)
                    break
                except (ValueError, ET.ParseError, KeyError) as exc:
                    result = {
                        "key": entry["key"],
                        "status": "failed",
                        "reason": str(exc),
                        "cases": [
                            {**c, "available": False, "point_f": None, "reason": str(exc)}
                            for c in entry["cases"]
                        ],
                        "cards": {},
                        "scores_computed": False,
                    }
                record_id = archive.append("e025_object", identity, utcnow(), {}, canonical(result).encode())
            results.append({"record_id": record_id, **result})
            offsets.update({s: card["byte_offset"] for s, card in result["cards"].items()})
    finally:
        client.close()
    known = {c["case_id"]: c for r in results for c in r["cases"]}
    cases = [
        known.get(c["case_id"], {**c, "available": False, "point_f": None, "reason": "not_yet_attempted"})
        for c in protocol["manifest"]["bindings"]
    ]
    report = {
        "registration_id": registration_id,
        "stop_reason": stop,
        "reported_at": iso(utcnow()),
        "objects_attempted": len(results),
        "objects_total": len(entries),
        "newly_attempted": newly_attempted,
        "object_record_ids": [r["record_id"] for r in results],
        "cases": cases,
        "case_count": len(cases),
        "available_cases": sum(c["available"] for c in cases),
        "full_panel_complete": all(c["available"] for c in cases),
        "full_panel_incomplete": not all(c["available"] for c in cases),
        "failure_reasons": dict(Counter(c["reason"] for c in cases if not c["available"])),
        "charged_payload_bytes_including_interrupted_reservations": client.used,
        "scoring_authorized": False,
        "scores_computed": False,
        "historical_public_availability_verified": False,
    }
    verify_source_hashes(protocol)
    rec = archive.append("e025_report", str(registration_id), utcnow(), {}, canonical(report).encode())
    report["report_record_id"] = rec
    report_dir.mkdir(parents=True, exist_ok=True)
    destination = report_dir / f"E025_acquisition_{registration_id}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.replace(destination)
    return {k: v for k, v in report.items() if k not in {"cases", "object_record_ids"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--run-record-id", type=int)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.prepare:
        manifest = make_manifest(config)
        directory = Path(args.report_dir)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "E025_acquisition_manifest.json"
        path.write_text(canonical(manifest) + "\n")
        print(
            canonical(
                {
                    "cases": len(manifest["bindings"]),
                    "objects": len(manifest["objects"]),
                    "manifest_sha256": sha(path.read_bytes()),
                    "network_requests": 0,
                }
            )
        )
        return
    archive = Archive(args.archive_root)
    try:
        with (archive.root / "e025_nbh.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another E025 acquisition holds the lock") from None
            if args.register:
                print(canonical({"registration_id": register(archive, config), "network_requests": 0}))
            else:
                print(canonical(run(archive, args.run_record_id, Path(args.report_dir))))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
