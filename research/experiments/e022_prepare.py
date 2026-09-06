"""Build E022 calendar and hourly training artifacts; never fit or score a model."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from research.probes.station_transformer import (
    DATA_START,
    FIXED_FIT,
    HOUR,
    MINUTE,
    MODEL_ID,
    REVISION,
    canonical,
    digest,
    index_observations,
    prepare_manifest,
    timestamp_ms,
    validate_config,
    validate_training_series,
)
from weatherpred.station_forecasts import describe_case

DAY = 24 * HOUR
CONFIG_PATH = Path("config/e022_station_forecasts.json")


def validate_protocol(protocol):
    """Reject drift from the fixed adapter and chronological model availability."""
    config = validate_config(protocol)
    if protocol["fixed_fit"] != FIXED_FIT:
        raise ValueError("Training settings differ from adapter.FIXED_FIT")
    if config["model_id"] != MODEL_ID or config["model_revision"] != REVISION:
        raise ValueError("Checkpoint model identity changed")
    start, fit, calibration, development, end = (
        timestamp_ms(protocol[key])
        for key in (
            "training_target_start",
            "fit_cutoff",
            "calibration_fit_available",
            "development_target_start",
            "target_end_exclusive",
        )
    )
    if not (
        start == FIXED_FIT["training_start_ms"]
        and fit == FIXED_FIT["fit_cutoff_ms"]
        and fit == timestamp_ms(protocol["calibration_target_start"])
        and start < fit < calibration == development < end
        and end == config["target_end_exclusive_ms"]
        and all(value % DAY == 0 for value in (start, fit, calibration, development, end))
    ):
        raise ValueError("Training/calibration/development cutoffs disagree")
    if protocol["candidate_count"] != 8 or len(protocol["candidates"]) != 8:
        raise ValueError("Exactly eight candidates must be declared before execution")
    ids = [candidate["id"] for candidate in protocol["candidates"]]
    if len(set(ids)) != len(ids):
        raise ValueError("Candidate IDs must be unique")
    for key in (
        "minimum_station_training_cases",
        "minimum_station_training_days",
        "minimum_calibration_cases_per_horizon",
        "minimum_calibration_days_per_horizon",
    ):
        if isinstance(protocol[key], bool) or not isinstance(protocol[key], int) or protocol[key] <= 0:
            raise ValueError("Support counts must be positive integers")
    return config


def station_directory(registry):
    stations = {}
    for item in registry["stations"]:
        identifier, timezone = item["icao"], item["timezone"]
        if not isinstance(identifier, str) or not identifier or identifier in stations:
            raise ValueError("Directory station IDs must be unique nonempty strings")
        if not isinstance(timezone, str) or not timezone:
            raise ValueError("Directory station requires a timezone")
        stations[identifier] = timezone
    if not stations:
        raise ValueError("Empty station directory")
    return dict(sorted(stations.items()))


def calendar_cases(stations, protocol):
    config = validate_protocol(protocol)
    start, fit, development, end = (
        timestamp_ms(protocol[key])
        for key in ("training_target_start", "fit_cutoff", "development_target_start", "target_end_exclusive")
    )
    result = []
    for day in range(start, end, DAY):
        for hour in config["target_utc_hours"]:
            target = day + hour * HOUR
            split = "training" if target < fit else "calibration" if target < development else "development"
            for station in stations:
                for horizon in config["horizons_hours"]:
                    result.append(
                        {
                            "case_id": f"{station}:{target}:{horizon}",
                            "station_id": station,
                            "station_timezone": stations[station],
                            "target_ms": target,
                            "decision_ms": target - horizon * HOUR,
                            "horizon_hours": horizon,
                            "split": split,
                        }
                    )
    return result


def inspect_case(case, observations, protocol):
    """Cut off boundary decisions before any context or label lookup.

    Label presence can restrict a shared scoring panel, never change a forecast
    or select a model by its error. No target temperatures enter these records.
    """
    config = protocol["station_transformer"]
    result = {**case, "eligible": False, "exclusion_reasons": []}
    decision, target, split = case["decision_ms"], case["target_ms"], case["split"]
    required_fit = (
        timestamp_ms(protocol["fit_cutoff"])
        if split == "calibration"
        else timestamp_ms(protocol["calibration_fit_available"])
        if split == "development"
        else None
    )
    if required_fit is not None and decision < required_fit:
        result["exclusion_reasons"] = [
            "decision_precedes_model_fit_cutoff"
            if split == "calibration"
            else "decision_precedes_calibration_fit_cutoff"
        ]
        return result
    label_cutoff = (
        timestamp_ms(protocol["fit_cutoff"])
        if split == "training"
        else timestamp_ms(protocol["calibration_fit_available"])
        if split == "calibration"
        else timestamp_ms(protocol["target_end_exclusive"])
    )
    if target + config["lag_minutes"] * MINUTE > label_cutoff:
        result["exclusion_reasons"] = ["assumed_label_availability_after_split_cutoff"]
        return result
    end = ((decision - config["lag_minutes"] * MINUTE) // HOUR) * HOUR
    grid = range(end - (config["context_hours"] - 1) * HOUR, end + HOUR, HOUR)
    valid = [
        observed
        for observed in grid
        if (point := observations.get((case["station_id"], observed))) is not None
        and point["status"] == "settled"
    ]
    result.update(
        context_end_ms=end,
        finite_context_points=len(valid),
        last_input_ms=valid[-1] if valid else None,
    )
    if len(valid) < config["minimum_finite_context"]:
        result["exclusion_reasons"].append("insufficient_finite_history")
    if valid and decision - valid[-1] > config["maximum_last_input_age_minutes"] * MINUTE:
        result["exclusion_reasons"].append("last_input_too_old")
    label = observations.get((case["station_id"], target))
    if label is None or label["status"] != "settled":
        result["exclusion_reasons"].append("missing_settled_target")
    else:
        result["target_source_record_ids"] = label["source_record_ids"]
        result["target_actual_received_ms"] = label["actual_received_ms"]
        result["target_assumed_available_ms"] = target + config["lag_minutes"] * MINUTE
    result["eligible"] = not result["exclusion_reasons"]
    return result


def make_training_series(stations, observations, supported, protocol):
    """Retain the entire requested hourly calendar, including all empty May hours."""
    start, end = timestamp_ms(protocol["training_target_start"]), timestamp_ms(protocol["fit_cutoff"])
    times = list(range(start, end, HOUR))
    included, excluded = [], []
    for station in stations:
        points = [observations.get((station, when)) for when in times]
        points = [point if point and point["status"] == "settled" else None for point in points]
        series = {
            "station_id": station,
            "station_timezone": stations[station],
            "timestamps_ms": times,
            "values_f": [point["temperature_f"] if point else None for point in points],
            "missing_mask": [point is None for point in points],
            "source_record_ids": [point["source_record_ids"][0] if point else None for point in points],
            "all_source_record_ids": [point["source_record_ids"] if point else [] for point in points],
            "actual_received_ms": [point["actual_received_ms"] if point else [] for point in points],
            "actual_received_at_original": [
                point["actual_received_at_original"] if point else [] for point in points
            ],
            "historical_availability_verified": False,
        }
        if supported[station]["eligible"]:
            included.append(series)
        else:
            excluded.append({**series, "exclusion_reason": "insufficient_station_training_support"})
    content_hash = validate_training_series(included) if included else None
    return {
        "schema_version": 1,
        "training_start_ms": start,
        "fit_cutoff_ms": end,
        "series": included,
        "excluded_series": excluded,
        "training_series_sha256": content_hash,
        "null_interpretation": "A missing hourly observation; convert null to NaN without compressing the time axis.",
        "fitting_performed": False,
    }


def prepare(protocol, registry, rows):
    validate_protocol(protocol)
    stations = station_directory(registry)
    # Validate identity only inside the registered source window. Values after
    # the cutoff are not inspected by the adapter's observation indexer.
    end = timestamp_ms(protocol["target_end_exclusive"])
    source_rows = []
    for row in rows:
        if DATA_START <= timestamp_ms(row["observed_at"]) < end:
            station = row["station_id"]
            if station not in stations or row["station_timezone"] != stations[station]:
                raise ValueError("Observation station/timezone does not match the pinned directory")
        source_rows.append(row)
    observations, audit = index_observations(source_rows)
    calendar = [inspect_case(case, observations, protocol) for case in calendar_cases(stations, protocol)]
    training_support = {}
    for station in stations:
        cases = [
            c for c in calendar if c["station_id"] == station and c["split"] == "training" and c["eligible"]
        ]
        target_days = len({case["target_ms"] // DAY for case in cases})
        training_support[station] = {
            "eligible_cases": len(cases),
            "target_days": target_days,
            "eligible": len(cases) >= protocol["minimum_station_training_cases"]
            and target_days >= protocol["minimum_station_training_days"],
        }
    for case in calendar:
        if not training_support[case["station_id"]]["eligible"]:
            case["exclusion_reasons"].append("insufficient_station_training_support")
            case["eligible"] = False
    calibration_support = {}
    for horizon in protocol["station_transformer"]["horizons_hours"]:
        cases = [
            c
            for c in calendar
            if c["split"] == "calibration" and c["horizon_hours"] == horizon and c["eligible"]
        ]
        target_days = len({case["target_ms"] // DAY for case in cases})
        calibration_support[str(horizon)] = {
            "eligible_cases": len(cases),
            "target_days": target_days,
            "eligible": len(cases) >= protocol["minimum_calibration_cases_per_horizon"]
            and target_days >= protocol["minimum_calibration_days_per_horizon"],
        }
    for case in calendar:
        if case["split"] != "training" and not calibration_support[str(case["horizon_hours"])]["eligible"]:
            case["exclusion_reasons"].append("insufficient_horizon_calibration_support")
            case["eligible"] = False
    inference = [c for c in calendar if c["split"] != "training" and c["eligible"]]
    manifest = {
        "schema_version": 1,
        "experiment": protocol["experiment"],
        "protocol_canonical_sha256": digest(protocol),
        "cases": inference,
        "calendar_cases": calendar,
        "training_support": training_support,
        "calibration_support": calibration_support,
        "labels_supplied_to_inference": False,
        "model_execution_performed": False,
        "historical_availability_verified": False,
        "development_is_untouched_validation": False,
        "adapter_case_policy": "cases contains the common eligible calibration/development panel only; every requested case, including boundary and no-data abstentions, remains in calendar_cases.",
    }
    series = make_training_series(stations, observations, training_support, protocol)
    histories = defaultdict(dict)
    for (station, when), point in observations.items():
        if point["status"] == "settled":
            histories[station][when] = point["temperature_f"]
    series["ridge_cases"] = []
    for case in calendar:
        if case["split"] != "training" or not case["eligible"]:
            continue
        label = observations[(case["station_id"], case["target_ms"])]
        features = describe_case(histories[case["station_id"]], case, stations[case["station_id"]])
        series["ridge_cases"].append({**case, **features, "observed_f": label["temperature_f"]})
    counts = defaultdict(lambda: {"calendar_cases": 0, "eligible_cases": 0, "exclusion_reasons": Counter()})
    station_counts = defaultdict(lambda: Counter())
    for case in calendar:
        item = counts[case["split"]]
        item["calendar_cases"] += 1
        item["eligible_cases"] += int(case["eligible"])
        item["exclusion_reasons"].update(case["exclusion_reasons"])
        station_counts[case["station_id"]][case["split"] + "_calendar"] += 1
        station_counts[case["station_id"]][case["split"] + "_eligible"] += int(case["eligible"])
    finite_training = [
        when
        for item in series["series"]
        for when, value in zip(item["timestamps_ms"], item["values_f"], strict=True)
        if value is not None
    ]
    coverage = {
        "experiment": protocol["experiment"],
        "station_directory_count": len(stations),
        "stations_with_in_window_observations": len({key[0] for key in observations}),
        "training_supported_stations": len(series["series"]),
        "training_excluded_stations": len(series["excluded_series"]),
        "splits": dict(counts),
        "per_station": dict(station_counts),
        "training_support": training_support,
        "calibration_support": calibration_support,
        "first_finite_training_hour_ms": min(finite_training) if finite_training else None,
        "last_finite_training_hour_ms": max(finite_training) if finite_training else None,
        "training_series_sha256": series["training_series_sha256"],
        "training_artifact_sha256": digest(series),
        "eligible_ridge_training_cases": len(series["ridge_cases"]),
        "first_ridge_training_target_ms": min(
            (case["target_ms"] for case in series["ridge_cases"]), default=None
        ),
        "manifest_sha256": digest(manifest),
        "data_audit": audit,
        "model_fits": 0,
        "model_inferences": 0,
        "scores_computed": 0,
        "historical_availability_verified": False,
        "profitability_proven": False,
    }
    # Exercise the exact downstream adapter without importing neural libraries.
    # Bound context/provenance memory by checking only 64 cases at a time.
    context_hash = hashlib.sha256()
    checked = 0
    for offset in range(0, len(inference), 64):
        chunk = inference[offset : offset + 64]
        checked_cases = prepare_manifest({"schema_version": 1, "cases": chunk}, observations, protocol)
        for original, prepared in zip(chunk, checked_cases, strict=True):
            if not prepared["eligible"] or any(
                prepared[key] != original[key]
                for key in ("context_end_ms", "finite_context_points", "last_input_ms")
            ):
                raise ValueError("Shared preparation eligibility disagrees with the fixed adapter")
            context_hash.update(
                canonical({"case_id": prepared["case_id"], "input_sha256": prepared["input_sha256"]})
            )
            checked += 1
    coverage["adapter_prepared_cases_verified"] = checked
    coverage["adapter_case_input_sequence_sha256"] = context_hash.hexdigest()
    return manifest, series, coverage


def main(args):
    protocol_bytes = Path(args.config).read_bytes()
    protocol = json.loads(protocol_bytes)
    validate_protocol(protocol)
    inputs = {}
    for name in ("observations", "registry"):
        body = Path(protocol[name + "_path"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != protocol[name + "_sha256"]:
            raise ValueError(f"Pinned {name} bytes changed")
        inputs[name] = body
    manifest, series, coverage = prepare(
        protocol,
        json.loads(inputs["registry"]),
        (json.loads(line) for line in inputs["observations"].splitlines() if line.strip()),
    )
    coverage["input_sha256"] = {
        "protocol": hashlib.sha256(protocol_bytes).hexdigest(),
        "observations": protocol["observations_sha256"],
        "registry": protocol["registry_sha256"],
        "preparer": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "adapter": hashlib.sha256(Path("research/probes/station_transformer.py").read_bytes()).hexdigest(),
    }
    outputs = {
        "E022_manifest.json": manifest,
        "E022_training_series.json": series,
        "E022_preparation_coverage.json": coverage,
    }
    directory = Path(args.output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    if any((directory / name).exists() for name in outputs):
        raise ValueError("Preparation refuses to overwrite an existing artifact")
    for name, content in outputs.items():
        with (directory / name).open("xb") as handle:
            handle.write(canonical(content))
    print(
        json.dumps(
            {k: v for k, v in coverage.items() if k not in ("per_station", "training_support", "data_audit")},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(CONFIG_PATH))
    parser.add_argument("--output-dir", default="reports")
    main(parser.parse_args())
