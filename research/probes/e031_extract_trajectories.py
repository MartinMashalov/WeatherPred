"""Registered raw NBH path extraction only: no models, network, labels or scores.

Reuses frozen E026 lineage/header/card-target checks. Newly authorized TMP
cells are parsed only after all original source and conditional-time gates.
"""

import argparse
import fcntl
import gzip
import json
import re
import resource
import signal
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from research.experiments.e026_nbh_comparison import (
    original_response,
    original_sources,
    read_record,
    verify_files,
)
from research.experiments.e026_result_audit import assert_pin, sha
from research.experiments.e029_fixed_combinations import parents
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import parse_time, utcnow

CONFIG = Path("config/e031_extract_trajectories.json")
FILES = [
    Path(__file__),
    CONFIG,
    Path("tests/test_e031_extract_trajectories.py"),
    Path("config/c1_nbh_covariate_design.json"),
    Path("research/CHRONOS_COVARIATE_PREFLIGHT.md"),
    Path("research/experiments/e026_result_audit.py"),
    Path("research/experiments/e026_nbh_comparison.py"),
    Path("research/experiments/e029_fixed_combinations.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]
HOUR = 3600000
HEADER = re.compile(
    rb"(?m)^ ?([A-Z0-9]{4,6}) +NBM V([0-9.]+) NBH GUIDANCE +(\d{1,2})/(\d{1,2})/(\d{4}) +(\d{2})00 UTC *\r?$"
)


def fixed_config(config):
    expected = {
        "experiment": "E031-current-cycle-NBH-trajectory-extraction-v1",
        "schema_version": 1,
        "source_registration_id": 129863,
        "physical_comparison_registration_id": 117527,
        "acquisition_registration_id": 113137,
        "parent_registration_id": 105314,
        "expected_objects": 498,
        "expected_cases": 9870,
        "expected_calibration_cases": 3271,
        "expected_development_cases": 6599,
        "run_lag_hours": 2,
        "union_leads": list(range(2, 9)),
        "future_slots": 7,
        "field": "TMP",
        "version": "5.0",
        "execution_seconds": 600,
        "maximum_cpu_threads": 2,
        "maximum_peak_memory_bytes": 4294967296,
        "attempts": 1,
    }
    if any(config.get(key) != value for key, value in expected.items()) or any(
        config.get(key) != 0
        for key in (
            "network_requests",
            "model_fits",
            "model_inferences",
            "weather_scores_computed",
            "trading_actions",
        )
    ):
        raise ValueError("Extraction differs from fixed no-model trajectory scope")


def check_resources(config):
    if Path(config["stop_file"]).exists():
        raise InterruptedError("Extraction stop file present")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_bytes = int(peak if sys.platform == "darwin" else peak * 1024)
    if peak_bytes > config["maximum_peak_memory_bytes"]:
        raise MemoryError("Registered4GiB resident-memory budget exceeded")
    return peak_bytes


def cell_path(body, *, body_offset, card, case, source):
    """Extract only required current-cycle cells from one verified complete card.

    Source is already checked against response headers/receipt lineage by caller;
    repeat time, hash, identity and width gates here before reading numeric TMP.
    """
    station = case["station_id"]
    horizon = case["horizon_hours"]
    decision, target, run = case["decision_ms"], case["target_ms"], case["run_ms"]
    if (
        horizon not in (1, 3, 6)
        or any(type(v) is not int for v in (decision, target, run, horizon))
        or run != decision - 2 * HOUR
        or target != decision + horizon * HOUR
    ):
        raise ValueError("Trajectory decision/run/target/horizon binding changed")
    if (
        source["object_last_modified_ms"] > decision
        or source["conditional_eligible_at_ms"] != source["object_last_modified_ms"]
    ):
        raise ValueError("Source storage eligibility is after decision")
    if parse_time(source["actual_received_at"]) < parse_time(source["conditional_eligible_at"]):
        raise ValueError("Actual receipt precedes original source storage time")
    if (
        int(parse_time(source["conditional_eligible_at"]).timestamp() * 1000)
        != source["conditional_eligible_at_ms"]
        or sha(body) != source["response_sha256"]
    ):
        raise ValueError("Source timestamp or response hash changed")
    offset = card["byte_offset"] - body_offset
    end = offset + card["raw_card_bytes"]
    if not 0 <= offset < end <= len(body):
        raise ValueError("Response does not contain the entire pinned card")
    raw = body[offset:end]
    if (
        sha(raw) != card["raw_card_sha256"]
        or card["station_id"] != station
        or card["run_ms"] != run
        or card["version"] != "5.0"
    ):
        raise ValueError("Pinned card identity/hash changed")
    matching = [header for header in HEADER.finditer(body) if header[1].decode() == station]
    if len(matching) != 1 or matching[0].start() != offset:
        raise ValueError("Duplicate station or mismatched exact card offset")
    header = matching[0]
    if header[2] != b"5.0":
        raise ValueError("Wrong raw NBH version")
    runtime = datetime(int(header[5]), int(header[3]), int(header[4]), int(header[6]), tzinfo=UTC)
    if int(runtime.timestamp() * 1000) != run:
        raise ValueError("Raw header runtime changed")
    if not re.search(rb"\r?\n[ \t]*\r?\n$", raw):
        raise ValueError("Incomplete station card separator")
    fields = {}
    line_offset = 0
    for with_ending in raw.splitlines(keepends=True):
        line = with_ending.removesuffix(b"\n").removesuffix(b"\r")
        for name in (b"UTC", b"TMP"):
            if re.match(rb"^ ?" + name + rb" ", line):
                if name in fields:
                    raise ValueError("Duplicate raw trajectory field")
                match = re.fullmatch(rb" ?" + name + rb" (.{75})" + (rb" ?" if name == b"UTC" else b""), line)
                if match is None:
                    raise ValueError("Malformed raw trajectory field width")
                fields[name] = (
                    match[1],
                    card["byte_offset"] + line_offset,
                    card["byte_offset"] + line_offset + match.start(1),
                )
        line_offset += len(with_ending)
    if b"UTC" not in fields:
        raise ValueError("Missing raw UTC field")
    required = list(range(2, horizon + 3))
    cells, future = [], [None] * 7
    for lead in required:
        valid = run + lead * HOUR
        utc_lexeme = fields[b"UTC"][0][3 * (lead - 1) : 3 * lead]
        if (
            re.fullmatch(rb" *\d{1,2}", utc_lexeme) is None
            or int(utc_lexeme) != datetime.fromtimestamp(valid / 1000, UTC).hour
        ):
            raise ValueError("Extracted UTC cell disagrees with exact valid time")
        missing, numeric, lexeme, field_offset, cell_offset = None, None, None, None, None
        if b"TMP" not in fields:
            missing = "absent_tmp_row"
        else:
            values, field_offset, first_cell = fields[b"TMP"]
            cell_offset = first_cell + 3 * (lead - 1)
            lexeme = values[3 * (lead - 1) : 3 * lead]
            stripped = lexeme.strip(b" ")
            if not stripped:
                missing = "blank_tmp_cell"
            elif stripped == b"-99":
                missing = "missing_tmp_sentinel"
            elif re.fullmatch(rb"-?\d{1,3}", stripped) is None:
                raise ValueError("Malformed required TMP cell")
            else:
                numeric = int(stripped)
                if not -98 <= numeric <= 998:
                    raise ValueError("TMP cell outside registered fixed-width format")
        cells.append(
            {
                "forecast_hour": lead,
                "valid_ms": valid,
                "tmp_f": numeric,
                "missing_reason": missing,
                "raw_lexeme": lexeme.decode("ascii") if lexeme is not None else None,
                "utc_raw_lexeme": utc_lexeme.decode("ascii"),
                **source,
                "card_sha256": card["raw_card_sha256"],
                "card_global_offset": card["byte_offset"],
                "field_line_offset": field_offset,
                "cell_offset": cell_offset,
                "utc_cell_offset": fields[b"UTC"][2] + 3 * (lead - 1),
                "station_id": station,
                "run_ms": run,
                "historical_public_availability_verified": False,
            }
        )
        future[lead - 2] = numeric
    missing_cells = [cell for cell in cells if cell["missing_reason"] is not None]
    return {
        **{
            key: case[key]
            for key in (
                "case_id",
                "station_id",
                "split",
                "decision_ms",
                "target_ms",
                "horizon_hours",
                "run_ms",
            )
        },
        "available": not missing_cells,
        "reason": "missing_required_tmp_cells" if missing_cells else None,
        "future_tmp_f": future,
        "required_leads": required,
        "cells": cells,
        "historical_public_availability_verified": False,
    }


def extract_object(archive, value, entry, settings, object_row):
    # Existing lineage and exact old target-cell checks must complete before any
    # newly registered trajectory cell is decoded.
    bound_cases, source_ids = original_sources(archive, value, entry, settings, object_row)
    if value["status"] == "failed":
        return [
            {
                **{
                    k: case[k]
                    for k in ("case_id", "station_id", "split", "decision_ms", "target_ms", "run_ms")
                },
                "horizon_hours": (case["target_ms"] - case["decision_ms"]) // HOUR,
                "available": False,
                "reason": "original_object_failed",
                "future_tmp_f": [None] * 7,
                "required_leads": list(range(2, (case["target_ms"] - case["decision_ms"]) // HOUR + 3)),
                "cells": [],
                "historical_public_availability_verified": False,
            }
            for case in bound_cases
        ], source_ids
    # References sorted before values: deterministic source selection cannot
    # favor a particular forecast or fill in a partial card from another run.
    responses = []
    for reference in sorted(value["sources"], key=lambda source: source["record_id"]):
        header = reference["request_headers"].get("Range")
        span = tuple(map(int, header[6:].split("-"))) if header else None
        role = f"range:{span[0]}:{span[1]}" if span else "full"
        row, meta, body = original_response(
            archive, reference["record_id"], entry, role, settings, object_row, value["object"], span
        )
        responses.append((row, meta, body, span[0] if span else 0))
    rows = []
    for original_case in bound_cases:
        case = {
            **original_case,
            "horizon_hours": (original_case["target_ms"] - original_case["decision_ms"]) // HOUR,
        }
        card = value["cards"][case["station_id"]]
        candidates = [
            (row, meta, body, offset)
            for row, meta, body, offset in responses
            if 0
            <= card["byte_offset"] - offset
            < card["byte_offset"] - offset + card["raw_card_bytes"]
            <= len(body)
        ]
        if not candidates:
            raise ValueError("No original response contains the complete trajectory card")
        row, meta, body, offset = candidates[0]
        modified = value["object"]["last_modified_ms"]
        source = {
            "response_id": row["id"],
            "response_sha256": row["body_sha256"],
            "response_record_sha256": row["record_sha256"],
            "conditional_eligible_at_ms": modified,
            "conditional_eligible_at": datetime.fromtimestamp(modified / 1000, UTC).isoformat(),
            "object_last_modified": value["object"]["last_modified"],
            "object_last_modified_ms": modified,
            "actual_received_at": meta["actual_received_at"],
        }
        rows.append(cell_path(body, body_offset=offset, card=card, case=case, source=source))
    return rows, source_ids


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    fixed_config(config)
    if archive.latest("e031_trajectory_protocol", config["experiment"]):
        raise ValueError("Extraction already registered")
    parent_row, parent = read_record(archive, config["source_registration_id"], "e029_protocol")
    verify_files(parent)
    _comparison, _acquisition, cases, census = parents(archive, parent["config"])
    if (
        parent["object_sources"] != census
        or len(cases) != config["expected_cases"]
        or len(census) != config["expected_objects"]
    ):
        raise ValueError("Frozen parent source census changed")
    if Counter(case["split"] for case in cases) != {
        "calibration": config["expected_calibration_cases"],
        "development": config["expected_development_cases"],
    }:
        raise ValueError("Original split census changed")
    hashes = dict(parent["source_hashes"])
    for path in FILES:
        current = sha(path.read_bytes())
        if str(path) in hashes and hashes[str(path)] != current:
            raise ValueError("Conflicting inherited source hash")
        hashes[str(path)] = current
    source_ids = {
        str(path): archive.append("research_source", str(path), utcnow(), {}, path.read_bytes())
        for path in FILES
    }
    protocol = {
        "config": config,
        "parent_registration_id": parent_row["id"],
        "parent_registration_record_sha256": parent_row["record_sha256"],
        "source_hashes": hashes,
        "source_record_ids": source_ids,
        "object_sources": census,
        "manifest_sha256": sha(Path(config["manifest_path"]).read_bytes()),
        "design_sha256": sha(Path(config["design_path"]).read_bytes()),
        "case_ids_sha256": sha(canonical(sorted(case["case_id"] for case in cases)).encode()),
        "numpy_version": parent["numpy_version"],
        "python_version": sys.version,
        "cwd": str(Path.cwd().resolve()),
        "registered_before_new_tmp_cells": True,
        "shared_checks": "Frozen E029 parents and E026 original_sources/original_response validate original request/receipt hashes, listings, station cards and original target. The new parser extracts only each registered lead2..horizon+2 path.",
        "execution_limits": "Single Python worker, scalar parsing only; no BLAS/model operations. 600s POSIX wall alarm;4GiB peak RSS checked between objects and before publication; bounded original response sizes inherited; no OS sandbox.",
    }
    return archive.append(
        "e031_trajectory_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )


def execute(archive, identifier):
    _, protocol = read_record(archive, identifier, "e031_trajectory_protocol")
    config = protocol["config"]
    fixed_config(config)
    verify_files(protocol)
    if sys.version != protocol["python_version"] or json.loads(CONFIG.read_bytes()) != config:
        raise ValueError("Extraction configuration or Python runtime changed")
    for path, source_id in protocol["source_record_ids"].items():
        source, body = read_record(archive, source_id, "research_source", decode=False)
        if (
            source_id >= identifier
            or source["key"] != path
            or source["body_sha256"] != protocol["source_hashes"][path]
            or sha(body) != protocol["source_hashes"][path]
        ):
            raise ValueError("Registered extraction source archive changed")
    if archive.latest("e031_extraction_started", str(identifier)):
        raise ValueError("Extraction already attempted; no automatic retry")
    check_resources(config)
    binding_id = archive.append(
        "e031_extraction_started",
        str(identifier),
        utcnow(),
        {},
        canonical({"object_sources": protocol["object_sources"]}).encode(),
    )

    def timeout(_number, _frame):
        raise TimeoutError("Registered600-second extraction budget reached")

    old = signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, config["execution_seconds"])
    try:
        parent_row, parent = read_record(archive, config["source_registration_id"], "e029_protocol")
        if parent_row["record_sha256"] != protocol["parent_registration_record_sha256"]:
            raise ValueError("Pinned source registration changed")
        comparison, acquisition, cases, census = parents(archive, parent["config"])
        if census != protocol["object_sources"]:
            raise ValueError("Registered object census changed")
        trajectories, source_ids = {}, set()
        for number, (pinned, entry) in enumerate(
            zip(census, acquisition["manifest"]["objects"], strict=True), 1
        ):
            check_resources(config)
            verify_files(protocol)
            row, value = read_record(archive, pinned["id"], comparison["config"]["acquisition_object_kind"])
            assert_pin(row, pinned)
            if row["id"] >= identifier:
                raise ValueError("Source object is after extraction registration")
            extracted, raw_ids = extract_object(archive, value, entry, comparison["config"], row)
            source_ids.update(raw_ids)
            for case in extracted:
                if case["case_id"] in trajectories:
                    raise ValueError("Duplicate extracted case")
                trajectories[case["case_id"]] = case
            if number % 40 == 0:
                print(
                    json.dumps({"objects_extracted": number, "cases_retained": len(trajectories)}), flush=True
                )
        if (
            set(trajectories) != {case["case_id"] for case in cases}
            or len(trajectories) != config["expected_cases"]
        ):
            raise ValueError("Trajectory extraction did not retain the original case census")
        ordered = [trajectories[case["case_id"]] for case in cases]
        result = {
            "schema_version": 1,
            "experiment": config["experiment"],
            "registration_id": identifier,
            "extraction_registration_id": identifier,
            "source_binding_record_id": binding_id,
            "manifest_sha256": protocol["manifest_sha256"],
            "design_sha256": protocol["design_sha256"],
            "case_ids_sha256": protocol["case_ids_sha256"],
            "cases": ordered,
            "summary": {
                "objects": len(census),
                "cases": len(ordered),
                "available": sum(case["available"] for case in ordered),
                "unavailable": sum(not case["available"] for case in ordered),
                "reasons": dict(Counter(case["reason"] for case in ordered if case["reason"])),
                "required_cells": sum(len(case["cells"]) for case in ordered),
                "split_counts": dict(Counter(case["split"] for case in ordered)),
                "peak_memory_bytes_before_serialization": check_resources(config),
            },
            "raw_source_ids": sorted(source_ids),
            "model_fits": 0,
            "model_inferences": 0,
            "network_requests": 0,
            "weather_scores_computed": 0,
            "trading_actions": 0,
            "historical_public_availability_verified": False,
        }
        canonical_body = canonical(result).encode()
        compressed = gzip.compress(canonical_body, mtime=0)
        check_resources(config)
        verify_files(protocol)
        record_id = archive.append("e031_trajectories_gzip", str(identifier), utcnow(), {}, compressed)
        path = Path("reports/E031_trajectories.json")
        with path.open("x") as handle:
            handle.write(canonical({**result, "record_id": record_id}))
        return {
            "record_id": record_id,
            "path": str(path),
            "body_sha256": sha(compressed),
            "canonical_payload_sha256": sha(canonical_body),
            "summary": result["summary"],
        }
    except BaseException as error:
        archive.append(
            "e031_extraction_failed",
            str(identifier),
            utcnow(),
            {},
            canonical({"error": f"{type(error).__name__}: {error}", "automatic_retry": False}).encode(),
        )
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--register", action="store_true")
    modes.add_argument("--run-record-id", type=int)
    args = parser.parse_args()
    archive = Archive("data")
    try:
        with (archive.root / json.loads(CONFIG.read_bytes())["lock_file"]).open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = (
                {"extraction_registration_id": register(archive), "new_tmp_cells_read": 0}
                if args.register
                else execute(archive, args.run_record_id)
            )
            print(json.dumps(result, sort_keys=True))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
