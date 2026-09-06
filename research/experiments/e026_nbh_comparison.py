"""One registered arithmetic comparison of original NBH and eight saved forecasts.

No network clients, model loading or inference. E022's scorer is independently
checked with its separate pure arithmetic auditor. Raw NBH temperatures are
also checked against retained card bytes without using the acquisition parser.
"""

import argparse
import fcntl
import gzip
import hashlib
import json
import re
import signal
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import (
    check_case,
    compare_scores,
    observations_from_rows,
    reconstruct_scores,
)
from research.experiments.e022_run import score_predictions
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import parse_time, utcnow

CONFIG = Path("config/e026_nbh_comparison.json")
FILES = [
    Path(__file__),
    CONFIG,
    Path("research/experiments/e022_audit.py"),
    Path("tests/test_e026_nbh_comparison.py"),
]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def read_record(archive, identifier, kind=None, decode=True):
    record = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if record is None or (kind is not None and record["kind"] != kind):
        raise ValueError("Missing or wrong source record")
    fields = [
        record[k] for k in ("kind", "key", "available_at", "metadata", "body_sha256", "previous_sha256")
    ]
    previous = archive.db.execute(
        "SELECT record_sha256 FROM records WHERE id<? ORDER BY id DESC LIMIT 1", (identifier,)
    ).fetchone()
    body = archive.body(record)
    if (
        digest(canonical(fields).encode()) != record["record_sha256"]
        or digest(body) != record["body_sha256"]
        or record["previous_sha256"] != (previous[0] if previous else "0" * 64)
    ):
        raise ValueError("Source record/body/chain integrity failure")
    if decode and record["kind"].endswith("_gzip"):
        body = gzip.decompress(body)
    return dict(record), json.loads(body) if decode else body


def verify_files(protocol):
    for name, expected in protocol["source_hashes"].items():
        if digest(Path(name).read_bytes()) != expected:
            raise ValueError("Registered source/input changed: " + name)
    if protocol["numpy_version"] != np.__version__ or protocol["cwd"] != str(Path.cwd().resolve()):
        raise ValueError("Registered runtime changed")


def fixed_bootstrap(settings):
    expected = {
        "block_days": 7,
        "resamples": 10000,
        "seed": 6202601,
        "confidence": 0.95,
        "comparison_count": 8,
    }
    if settings != expected:
        raise ValueError("Bootstrap controls differ from the fixed eight-comparison protocol")


def merge_pins(*groups):
    merged = {}
    for group in groups:
        for name, value in group.items():
            if name in merged and merged[name] != value:
                raise ValueError("Conflicting parent/acquisition source pins: " + name)
            merged[name] = value
    return merged


def case_binding(case):
    horizon, decision, target = (case[key] for key in ("horizon_hours", "decision_ms", "target_ms"))
    if (
        any(type(value) is not int for value in (horizon, decision, target))
        or horizon not in (1, 3, 6)
        or target - decision != horizon * 3_600_000
    ):
        raise ValueError("Original case has an invalid target/decision/horizon binding")
    run_ms = decision - 2 * 3_600_000
    run = datetime.fromtimestamp(run_ms / 1000, UTC)
    return {
        "case_id": case["case_id"],
        "station_id": case["station_id"],
        "split": case["split"],
        "target_ms": target,
        "decision_ms": decision,
        "run_ms": run_ms,
        "forecast_hour": horizon + 2,
        "key": f"blend.{run:%Y%m%d}/{run:%H}/text/blend_nbhtx.t{run:%H}z",
    }


def validate_bindings(cases, bindings):
    expected = {case["case_id"]: case_binding(case) for case in cases}
    if (
        len(expected) != len(cases)
        or len(bindings) != len(cases)
        or {row["case_id"] for row in bindings} != set(expected)
    ):
        raise ValueError("Physical bindings do not retain every unique original E022 case")
    for binding in bindings:
        if any(binding.get(key) != value for key, value in expected[binding["case_id"]].items()):
            raise ValueError("Physical station/time/split/run binding differs from original E022 case")
    return expected


def validate_acquisition(acquisition, config, cases):
    settings = acquisition["config"]
    expected_kinds = {
        "acquisition_protocol_kind": "e025v2_protocol",
        "acquisition_object_kind": "e025v2_object",
        "acquisition_response_kind": "e025v2_response",
        "reused_response_kind": "e025_response",
    }
    if any(config.get(key) != value for key, value in expected_kinds.items()):
        raise ValueError("Only the declared v2 acquisition and explicit v1 response reuse are supported")
    if (
        settings["experiment"] != "E025-original-NBH-station-acquisition-v2"
        or settings["bucket"] != config["bucket"]
        or settings["prior_registration_id"] != config["prior_acquisition_registration_id"]
        or settings["reuse_sources"] != config["reuse_sources"]
        or settings["run_lag_hours"] != 2
        or settings["station_lag_minutes"] != 15
        or settings["expected_objects"] != config["expected_objects"]
        or settings["expected_cases"] != config["expected_cases"]
    ):
        raise ValueError("Wrong acquisition family, lineage, source reuse, or fixed lag")
    manifest = acquisition["manifest"]
    expected = validate_bindings(cases, manifest["bindings"])
    grouped = {}
    for binding in sorted(expected.values(), key=lambda row: row["case_id"]):
        grouped.setdefault(binding["key"], {"key": binding["key"], "run_ms": binding["run_ms"], "cases": []})[
            "cases"
        ].append(binding)
    if manifest["objects"] != [grouped[key] for key in sorted(grouped)]:
        raise ValueError("Acquisition object bindings differ from original E022 cases")
    if len(cases) != config["expected_cases"] or len(grouped) != config["expected_objects"]:
        raise ValueError("Acquisition case/object census differs")


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    fixed_bootstrap(config["bootstrap"])
    if archive.latest("e026_protocol", config["experiment"]):
        raise ValueError("Comparison already registered; reuse its immutable record")
    parent_record, parent = read_record(archive, config["parent_registration_id"], "experiment_protocol")
    report_record, report = read_record(archive, config["parent_report_record_id"], "experiment_report_gzip")
    acquisition_record, acquisition = read_record(
        archive, config["acquisition_registration_id"], config["acquisition_protocol_kind"]
    )
    cases = json.loads(Path(config["manifest_path"]).read_bytes())["cases"]
    validate_acquisition(acquisition, config, cases)
    if [c["candidate"]["id"] for c in report["candidates"]] != config["model_ids"]:
        raise ValueError("Must retain all eight existing forecasts")
    if report["quantile_levels"] != config["quantile_levels"]:
        raise ValueError("Quantile levels differ from the original study")
    local_report = json.loads(Path(config["parent_report_path"]).read_bytes())
    # The local copy adds its immutable report ID after archival.
    if {k: v for k, v in local_report.items() if k != "report_record_id"} != report:
        raise ValueError("Local parent report differs from its archived record")
    pins = merge_pins(parent["source_hashes"], acquisition["source_sha256"])
    for path in FILES + [Path(config["parent_report_path"]), Path("pyproject.toml"), Path("uv.lock")]:
        pins = merge_pins(pins, {str(path): digest(path.read_bytes())})
    for item in report["prediction_artifacts"]:
        pins = merge_pins(pins, {item["path"]: item["sha256"]})
    source_records = {
        str(p): archive.append("e026_source", str(p), utcnow(), {}, p.read_bytes()) for p in FILES
    }
    protocol = {
        "config": config,
        "source_hashes": pins,
        "source_record_ids": source_records,
        "parent_record_sha256": parent_record["record_sha256"],
        "report_body_sha256": report_record["body_sha256"],
        "acquisition_record_sha256": acquisition_record["record_sha256"],
        "numpy_version": np.__version__,
        "cwd": str(Path.cwd().resolve()),
        "known_E022_results_acknowledged": True,
        "NBH_scores_examined": False,
        "registered_at": utcnow().isoformat(),
    }
    verify_files(protocol)
    return archive.append("e026_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode())


def check_original_card(body, card, case, run_ms, body_offset=0):
    """Independent fixed-column target extraction, separate from NBH parser."""
    start = card["byte_offset"] - body_offset
    end = start + card["raw_card_bytes"]
    if not 0 <= start < end <= len(body):
        return False
    raw = body[start:end]
    if digest(raw) != card["raw_card_sha256"] or digest(raw) != case["card_sha256"]:
        raise ValueError("Raw NBH card hash mismatch")
    lines = raw.splitlines()
    header = re.fullmatch(
        rb" ?([A-Z0-9]{4,6}) +NBM V5\.0 NBH GUIDANCE +(\d{1,2})/(\d{1,2})/(\d{4}) +(\d{2})00 UTC *",
        lines[0],
    )
    if header is None or header[1].decode() != case["station_id"]:
        raise ValueError("Raw NBH station/version/header mismatch")
    runtime = datetime(int(header[4]), int(header[2]), int(header[3]), int(header[5]), tzinfo=UTC)
    if int(runtime.timestamp() * 1000) != run_ms:
        raise ValueError("Raw NBH runtime mismatch")
    hour = case["forecast_hour"]
    if not 1 <= hour <= 25 or run_ms + hour * 3_600_000 != case["target_ms"]:
        raise ValueError("Raw NBH valid-time mismatch")
    values = {}
    for field in (b"UTC", b"TMP"):
        matching = [
            line[1:] if line.startswith(b" ") else line
            for line in lines[1:]
            if line.lstrip(b" ").startswith(field + b" ")
        ]
        if len(matching) > 1 or (field == b"UTC" and not matching):
            raise ValueError("Duplicate/missing raw NBH field")
        if not matching:
            values[field] = None
            continue
        line = matching[0]
        if field == b"UTC" and len(line) == 80 and line.endswith(b" "):
            line = line[:-1]
        if len(line) != 79 or line[:4] != field + b" ":
            raise ValueError("Raw NBH field width differs")
        cell = line[4 + 3 * (hour - 1) : 4 + 3 * hour].strip()
        if cell in (b"", b"-99"):
            values[field] = None
        elif re.fullmatch(rb"-?\d{1,3}", cell) is None:
            raise ValueError("Malformed raw NBH number")
        else:
            values[field] = int(cell)
    expected_utc = datetime.fromtimestamp(case["target_ms"] / 1000, UTC).hour
    if values[b"UTC"] != expected_utc or values[b"TMP"] != case["point_f"]:
        raise ValueError("Stored forecast differs from original NBH bytes")
    if case["available"] != (values[b"TMP"] is not None):
        raise ValueError("Forecast missingness differs from original NBH bytes")
    return True


def original_response(archive, identifier, entry, role, config, object_record, obj=None, byte_range=None):
    """Rebuild request identity and lineage before trusting original response bytes."""
    record, body = read_record(archive, identifier, decode=False)
    meta = json.loads(record["metadata"])
    listing = role == "listing"
    url = config["bucket"] + ("" if listing else entry["key"])
    params = {"list-type": "2", "prefix": entry["key"], "max-keys": 2} if listing else None
    headers = {} if listing else {"If-Match": obj["etag"]}
    if byte_range is not None:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    cap = 65536 if listing else obj["size"] if byte_range is None else byte_range[1] - byte_range[0] + 1
    signature = digest(canonical({"url": url, "params": params, "headers": headers, "cap": cap}).encode())
    if record["kind"] == config["acquisition_response_kind"]:
        owner = config["acquisition_registration_id"]
        request_kind = "e025v2_request_started"
        if not owner < identifier < object_record["id"]:
            raise ValueError("New response lies outside registered acquisition chronology")
    elif record["kind"] == config["reused_response_kind"]:
        owner = config["prior_acquisition_registration_id"]
        request_kind = "e025_request_started"
        pinned = [source for source in config["reuse_sources"] if source["record_id"] == identifier]
        if len(pinned) != 1 or not owner < identifier < config["acquisition_registration_id"]:
            raise ValueError("Old raw response is not an explicitly pinned reusable source")
        pin = pinned[0]
        if (
            record["key"] != pin["record_key"]
            or record["body_sha256"] != pin["body_sha256"]
            or record["record_sha256"] != pin["record_sha256"]
            or signature != pin["request_signature"]
            or meta["headers"].get("etag") != pin["etag"]
            or meta["headers"].get("last-modified") != pin["last_modified"]
            or meta["actual_received_at"] != pin["actual_received_at"]
        ):
            raise ValueError("Explicit reused-response hash/request/receipt pin changed")
    else:
        raise ValueError("Unexpected raw response lineage")
    expected_key = f"{owner}:{entry['key']}:{role}"
    if (
        record["key"] != expected_key
        or meta["url"] != url
        or meta["params"] != params
        or meta["request_headers"] != headers
        or meta["request_signature"] != signature
        or meta["complete"] is not True
        or meta["error"] is not None
        or meta["payload_bytes"] != len(body)
        or meta["raw_sha256"] != record["body_sha256"]
        or parse_time(meta["actual_received_at"]) != parse_time(record["available_at"])
        or parse_time(meta["actual_received_at"]) > parse_time(object_record["available_at"])
        or not 0 <= len(body) <= cap
    ):
        raise ValueError("Raw response request/header/receipt provenance changed")
    owner_record, _ = read_record(
        archive,
        owner,
        "e025v2_protocol" if request_kind == "e025v2_request_started" else "e025_protocol",
        False,
    )
    started_id = meta["request_started_record_id"]
    started, _ = read_record(archive, started_id, request_kind, False)
    request = json.loads(started["metadata"])
    if (
        not owner < started_id < identifier
        or parse_time(owner_record["available_at"]) > parse_time(started["available_at"])
        or started["key"] != expected_key
        or request["request_signature"] != signature
        or request["url"] != url
        or request["params"] != params
        or request["request_headers"] != headers
        or request["request_started_at"] != meta["request_started_at"]
        or parse_time(started["available_at"]) != parse_time(meta["request_started_at"])
        or parse_time(meta["request_started_at"]) > parse_time(meta["actual_received_at"])
    ):
        raise ValueError("Raw response no longer follows its original request-start record")
    response_headers = meta["headers"]
    content_length = response_headers.get("content-length")
    if (
        response_headers.get("content-encoding", "identity").lower() != "identity"
        or (content_length is not None and int(content_length) != len(body))
        or meta["status"] != (206 if byte_range is not None else 200)
    ):
        raise ValueError("Original response encoding, payload length or status changed")
    if not listing:
        last_modified = parsedate_to_datetime(response_headers["last-modified"])
        if (
            last_modified.tzinfo is None
            or int(last_modified.timestamp() * 1000) != obj["last_modified_ms"]
            or response_headers.get("etag") != obj["etag"]
            or content_length is None
        ):
            raise ValueError("Original object storage time, ETag or payload length changed")
        if byte_range is not None:
            low, high = byte_range
            if (
                not 0 <= low <= high < obj["size"]
                or response_headers.get("content-range") != f"bytes {low}-{high}/{obj['size']}"
                or len(body) != cap
            ):
                raise ValueError("Original source byte range differs")
        elif len(body) != obj["size"]:
            raise ValueError("Original full object differs")
    return record, meta, body


def original_listing(archive, result, entry, config, object_record):
    obj = result["object"]
    row, meta, body = original_response(
        archive, obj["listing_record_id"], entry, "listing", config, object_record
    )
    root = ET.fromstring(body)
    namespace = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    matches = [
        node
        for node in root.findall("s:Contents", namespace)
        if node.findtext("s:Key", namespaces=namespace) == entry["key"]
    ]
    if root.findtext("s:IsTruncated", namespaces=namespace) != "false" or len(matches) != 1:
        raise ValueError("Original exact-object listing is missing, duplicate or truncated")
    node = matches[0]
    modified = node.findtext("s:LastModified", namespaces=namespace)
    at = datetime.fromisoformat(modified)
    if (
        at.tzinfo is None
        or obj["key"] != entry["key"]
        or obj["run_ms"] != entry["run_ms"]
        or obj["last_modified"] != modified
        or obj["last_modified_ms"] != int(at.timestamp() * 1000)
        or obj["etag"] != node.findtext("s:ETag", namespaces=namespace)
        or obj["size"] != int(node.findtext("s:Size", namespaces=namespace))
        or obj["size"] <= 0
        or obj["listing_actual_received_at"] != meta["actual_received_at"]
    ):
        raise ValueError("Original listing no longer matches retained object metadata")
    return row["id"]


def original_sources(archive, result, entry, config, object_record):
    """Verify declared response identity, times, hashes and selected TMP cells."""
    if result["key"] != entry["key"]:
        raise ValueError("Acquisition object key differs")
    if (
        object_record["kind"] != config["acquisition_object_kind"]
        or object_record["key"] != f"{config['acquisition_registration_id']}:{entry['key']}"
        or object_record["id"] <= config["acquisition_registration_id"]
    ):
        raise ValueError("Object is outside the registered v2 acquisition lineage")
    cases = result["cases"]
    if len(cases) != len(entry["cases"]):
        raise ValueError("Acquisition lost cases")
    expected = {case["case_id"]: case for case in entry["cases"]}
    if {case["case_id"] for case in cases} != set(expected):
        raise ValueError("Acquisition changed/duplicated case identities")
    for case in cases:
        if any(case.get(key) != value for key, value in expected[case["case_id"]].items()):
            raise ValueError("Acquisition changed a frozen case binding")
    if result["status"] == "failed":
        if any(case["available"] or case["point_f"] is not None for case in cases):
            raise ValueError("Failed object contains usable forecasts")
        return cases, []
    if result["status"] != "acquired" or result.get("scores_computed") is not False:
        raise ValueError("Unexpected acquisition status")
    obj = result["object"]
    if obj["run_ms"] != entry["run_ms"] or any(
        obj["last_modified_ms"] > case["decision_ms"] for case in cases
    ):
        raise ValueError("Acquisition violated its historical storage gate")
    checked_ids = [original_listing(archive, result, entry, config, object_record)]
    sources = []
    for source in result["sources"]:
        header = source["request_headers"].get("Range")
        if header is not None and re.fullmatch(r"bytes=\d+-\d+", header) is None:
            raise ValueError("Malformed retained byte range")
        span = tuple(int(value) for value in header[6:].split("-")) if header else None
        role = f"range:{span[0]}:{span[1]}" if span else "full"
        record, meta, body = original_response(
            archive, source["record_id"], entry, role, config, object_record, obj, span
        )
        if (
            record["body_sha256"] != source["raw_sha256"]
            or meta["headers"] != source["headers"]
            or meta["request_headers"] != source["request_headers"]
            or meta["actual_received_at"] != source["actual_received_at"]
        ):
            raise ValueError("Raw NBH source provenance changed")
        sources.append((body, span[0] if span else 0))
        checked_ids.append(record["id"])
    if len(set(checked_ids)) != len(checked_ids):
        raise ValueError("Repeated original listing or payload source")
    for case in cases:
        card = result["cards"][case["station_id"]]
        if (
            card["station_id"] != case["station_id"]
            or card["version"] != "5.0"
            or card["run_ms"] != entry["run_ms"]
            or case["last_modified_ms"] != obj["last_modified_ms"]
            or case["historical_public_availability_verified"] is not False
        ):
            raise ValueError("Card identity or conditional storage provenance changed")
        if not any(
            check_original_card(body, card, case, entry["run_ms"], offset) for body, offset in sources
        ):
            raise ValueError("No retained response contains the entire selected card")
    return cases, checked_ids


def shared_intervals(differences, days, settings):
    fixed_bootstrap(settings)
    expected = [(date(2026, 7, 20) + timedelta(days=i)).isoformat() for i in range(28)]
    if days != expected:
        return {"status": "unsupported_missing_calendar_days", "days": days}
    x = np.asarray(differences, dtype=float)
    if x.shape != (28, settings["comparison_count"]) or not np.isfinite(x).all():
        raise ValueError("Wrong paired day/comparison matrix")
    rng = np.random.default_rng(settings["seed"])
    starts = rng.integers(0, 28, size=(settings["resamples"], 4))
    indices = ((starts[:, :, None] + np.arange(7)) % 28).reshape(-1, 28)
    samples = x[indices].mean(axis=1)
    means, errors = x.mean(axis=0), samples.std(axis=0, ddof=1)
    active = np.any(x != x[0], axis=0) & (errors > 1e-14)
    maxima = (
        np.max(np.abs(samples[:, active] - means[active]) / errors[active], axis=1)
        if active.any()
        else np.zeros(len(samples))
    )
    critical = float(np.quantile(maxima, settings["confidence"], method="higher"))
    return {
        "status": "descriptive_development_intervals",
        "means": means.tolist(),
        "standard_errors": errors.tolist(),
        "active": active.tolist(),
        "critical_value": critical,
        "intervals": [
            [float(m - critical * s), float(m + critical * s)] if a else None
            for m, s, a in zip(means, errors, active, strict=True)
        ],
        "indices_sha256": digest(canonical(indices.tolist()).encode()),
        "resampled_means_sha256": digest(canonical(samples.tolist()).encode()),
        "settings": settings,
        "independent_significance_proven": False,
    }


def compare_panel(cases, physical, predictions, observations, config, parent_config):
    """Choose the common IDs only from forecast presence, before reading labels."""
    fixed_bootstrap(config["bootstrap"])
    validate_bindings(cases, physical)
    ids = {c["case_id"] for c in cases}
    physical_map = {c["case_id"]: c for c in physical}
    if len(physical_map) != len(physical) or set(physical_map) != ids:
        raise ValueError("Physical acquisition is not the complete original case grid")
    available = {key for key, c in physical_map.items() if c["available"]}
    panel = [case for case in cases if case["case_id"] in available]
    result = {
        "full_panel_complete": available == ids,
        "classification": "full_original_panel" if available == ids else "common_available_subset_only",
        "original_case_count": len(cases),
        "common_case_count": len(panel),
        "missing": [{"case_id": c["case_id"], "reason": c["reason"]} for c in physical if not c["available"]],
        "case_ids_sha256": digest(canonical(sorted(available)).encode()),
        "comparisons": [],
    }
    levels = config["quantile_levels"]
    for horizon in (1, 3, 6):
        calibration = [c for c in panel if c["split"] == "calibration" and c["horizon_hours"] == horizon]
        if (
            len(calibration) < parent_config["minimum_calibration_cases_per_horizon"]
            or len({c["target_ms"] // 86400000 for c in calibration})
            < parent_config["minimum_calibration_days_per_horizon"]
        ):
            return {**result, "status": "insufficient_common_calibration", "candidates": []}
    if not any(c["split"] == "development" for c in panel):
        return {**result, "status": "no_common_development_cases", "candidates": []}
    observed = {c["case_id"]: check_case(c, parent_config, observations) for c in panel}
    candidates, maximum_error = [], 0.0
    for model in [*config["model_ids"], config["new_model_id"]]:
        if model == config["new_model_id"]:
            current = [
                {
                    "case_id": c["case_id"],
                    "point_f": physical_map[c["case_id"]]["point_f"],
                    "quantiles_f": [physical_map[c["case_id"]]["point_f"]] * len(levels),
                }
                for c in panel
            ]
        else:
            all_predictions = predictions[model]
            if (
                len({p["case_id"] for p in all_predictions}) != len(all_predictions)
                or {p["case_id"] for p in all_predictions} != ids
            ):
                raise ValueError("Saved candidate does not match all original cases")
            current = [p for p in all_predictions if p["case_id"] in available]
        evaluation, _ = score_predictions(current, {"cases": panel}, observations, parent_config, levels)
        audited, _ = reconstruct_scores(current, panel, observed, levels, parent_config)
        maximum_error = max(maximum_error, compare_scores(evaluation, audited))
        candidates.append({"id": model, "evaluation": evaluation})
    nbh = candidates[-1]["evaluation"]
    days = [d["day"] for d in nbh["daily"]]
    differences = []
    for i, day in enumerate(days):
        row = []
        for candidate in candidates[:-1]:
            other = candidate["evaluation"]["daily"][i]
            if (other["day"], other["cases"]) != (day, nbh["daily"][i]["cases"]):
                raise ValueError("Candidates do not share the identical daily cases")
            row.append(nbh["daily"][i]["absolute_error_f"] - other["absolute_error_f"])
        differences.append(row)
    for candidate in candidates[:-1]:
        result["comparisons"].append(
            {
                "candidate": candidate["id"],
                "nbh_minus_candidate_mae_f": nbh["metrics"]["mae_f"]
                - candidate["evaluation"]["metrics"]["mae_f"],
            }
        )
    return {
        **result,
        "status": "scored",
        "candidates": candidates,
        "daily_paired_mae": differences,
        "bootstrap": shared_intervals(differences, days, config["bootstrap"]),
        "independent_score_max_error": maximum_error,
    }


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e026_protocol")
    verify_files(protocol)
    config = protocol["config"]
    acquisition_record, acquisition = read_record(
        archive, config["acquisition_registration_id"], config["acquisition_protocol_kind"]
    )
    if acquisition_record["record_sha256"] != protocol["acquisition_record_sha256"]:
        raise ValueError("Pinned acquisition registration changed")
    cases = json.loads(Path(config["manifest_path"]).read_bytes())["cases"]
    validate_acquisition(acquisition, config, cases)
    entries = acquisition["manifest"]["objects"]
    records = []
    # Check the complete attempt census by record identity before forecast bodies.
    for entry in entries:
        row = archive.latest(
            config["acquisition_object_kind"], f"{config['acquisition_registration_id']}:{entry['key']}"
        )
        if row is None:
            raise ValueError("Acquisition incomplete; do not score partial acquisition batches")
        records.append({k: row[k] for k in ("id", "kind", "key", "body_sha256", "record_sha256")})
    if len(records) != config["expected_objects"] or len({r["id"] for r in records}) != len(records):
        raise ValueError("Wrong acquisition attempt census")
    if archive.latest("e026_started", str(identifier)):
        raise ValueError("Comparison attempt already exists; no silent retry")
    binding_id = archive.append(
        "e026_started", str(identifier), utcnow(), {}, canonical({"object_sources": records}).encode()
    )

    def deadline(_number, _frame):
        raise TimeoutError("Registered 600-second scoring budget reached")

    old_handler = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, config["execution_budget_seconds"])
    try:
        physical, source_ids = [], set()
        for item, entry in zip(records, entries, strict=True):
            if Path(config["stop_file"]).exists():
                raise InterruptedError("Registered stop file present")
            object_record, value = read_record(archive, item["id"], config["acquisition_object_kind"])
            if any(object_record[key] != expected for key, expected in item.items()):
                raise ValueError("Bound acquisition object changed")
            rows, raw_ids = original_sources(archive, value, entry, config, object_record)
            physical.extend(rows)
            source_ids.update(raw_ids)
        if len(physical) != config["expected_cases"]:
            raise ValueError("Acquisition case count changed")
        parent_config = json.loads(Path(config["parent_config_path"]).read_bytes())
        cases = json.loads(Path(config["manifest_path"]).read_bytes())["cases"]
        with Path(parent_config["observations_path"]).open() as handle:
            observations = observations_from_rows(
                (json.loads(line) for line in handle if line.strip()), parent_config
            )
        predictions = {
            model: json.loads(Path(f"reports/E022-run-105314/{model}/predictions.json").read_bytes())[
                "predictions"
            ]
            for model in config["model_ids"]
        }
        result = compare_panel(cases, physical, predictions, observations, config, parent_config)
        if result["full_panel_complete"] and result["status"] == "scored":
            parent_report = json.loads(Path(config["parent_report_path"]).read_bytes())
            for current, old in zip(result["candidates"][:-1], parent_report["candidates"], strict=True):
                compare_scores(current["evaluation"], old["evaluation"])
        verify_files(protocol)
        report = {
            "experiment": config["experiment"],
            "registration_id": identifier,
            "source_binding_record_id": binding_id,
            "raw_source_ids": sorted(source_ids),
            "result": result,
            "model_fits": 0,
            "model_inferences": 0,
            "network_requests": 0,
            "trading_actions": 0,
            "profitability_proven": False,
            "historical_public_availability_verified": False,
            "untouched_validation": False,
            "interpretation": config["interpretation"],
        }
        record_id = archive.append(
            "e026_report_gzip",
            str(identifier),
            utcnow(),
            {},
            gzip.compress(canonical(report).encode(), mtime=0),
        )
        report["report_record_id"] = record_id
        path = Path("reports") / f"E026_comparison_{identifier}.json"
        with path.open("x") as handle:
            json.dump(report, handle, indent=2, allow_nan=False)
        return {
            "report_record_id": record_id,
            "report_path": str(path),
            "status": result["status"],
            "full_panel_complete": result["full_panel_complete"],
        }
    except BaseException as exc:
        archive.append(
            "e026_failed",
            str(identifier),
            utcnow(),
            {},
            canonical({"error": f"{type(exc).__name__}: {exc}", "automatic_retry": False}).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / "e026.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            print(
                canonical(
                    {"registration_id": register(archive), "scores_computed": 0}
                    if args.register
                    else execute(archive, args.run_record_id)
                )
            )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
