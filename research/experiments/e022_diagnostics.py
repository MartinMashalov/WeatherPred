"""Separately declared post-result E022 subgroup and shared-block diagnosis."""

import argparse
import gzip
import hashlib
import importlib.metadata
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.experiments.e022_audit import observations_from_rows, timestamp
from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import utcnow

CONFIG = Path("config/e022_station_diagnostics.json")
FILES = [
    Path(__file__),
    CONFIG,
    Path("research/experiments/e022_audit.py"),
    Path("weatherpred/archive.py"),
    Path("weatherpred/timeutil.py"),
]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def object_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read(archive, identifier, compressed=False, decode=True):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("Missing declared source")
    data = archive.body(row)
    if hashlib.sha256(data).hexdigest() != row["body_sha256"]:
        raise ValueError("Archived body hash mismatch")
    if compressed:
        data = gzip.decompress(data)
    return row, json.loads(data) if decode else data


def register(archive):
    config = json.loads(CONFIG.read_bytes())
    if archive.latest("experiment_protocol", config["experiment"]):
        raise ValueError("Diagnostic already declared; reuse its immutable registration")
    parent_row, parent = read(archive, config["parent_registration_record_id"])
    report_row, _ = read(archive, config["parent_report_record_id"], decode=False)
    paths = {str(p): sha(p) for p in FILES}
    paths.update(parent["source_hashes"])
    paths[config["parent_report_path"]] = sha(config["parent_report_path"])
    paths[config["prior_audit_path"]] = sha(config["prior_audit_path"])
    for model in config["model_ids"]:
        path = f"reports/E022-run-{config['parent_registration_record_id']}/{model}/predictions.json"
        paths[path] = sha(path)  # Pin bytes without reading subgroup errors.
    for path, expected in paths.items():
        if sha(path) != expected:
            raise ValueError("Declared parent source changed: " + path)
    source_ids = {
        str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes()) for p in FILES
    }
    declaration = {
        "config": config,
        "source_hashes": paths,
        "source_record_ids": source_ids,
        "parent_registration_record_sha256": parent_row["record_sha256"],
        "parent_report_body_sha256": report_row["body_sha256"],
        "numpy_version": importlib.metadata.version("numpy"),
        "subgroup_errors_examined_by_this_diagnostic": False,
        "known_aggregate_results_acknowledged": True,
        "prior_audit_raw_forecast_and_daily_score_access_acknowledged": True,
        "profitability_proven": False,
    }
    identifier = archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(declaration).encode()
    )
    print(
        json.dumps(
            {
                "diagnostic_registration_record_id": identifier,
                "subgroup_errors_computed": 0,
                "source_sha256": paths[str(Path(__file__))],
                "configuration_sha256": paths[str(CONFIG)],
            }
        )
    )
    return identifier


def shared_bootstrap(daily, settings):
    if daily.shape != (28, 4):
        raise ValueError("Expected fixed28day/fourcomparison panel")
    generator = np.random.default_rng(settings["seed"])
    starts = generator.integers(0, 28, size=(settings["resamples"], 4))
    indexes = ((starts[:, :, None] + np.arange(7)[None, None, :]) % 28).reshape(-1, 28)
    means = daily[indexes].mean(axis=1)
    observed = daily.mean(axis=0)
    errors = means.std(axis=0, ddof=1)
    active = errors > 0
    maxima = (
        np.max(np.abs(means[:, active] - observed[active]) / errors[active], axis=1)
        if active.any()
        else np.zeros(settings["resamples"])
    )
    critical = float(np.quantile(maxima, settings["confidence"], method="higher"))
    return {
        "observed_means": observed.tolist(),
        "standard_errors": errors.tolist(),
        "active_comparisons": active.tolist(),
        "simultaneous_critical_value": critical,
        "intervals": [
            [float(m - critical * s), float(m + critical * s)] for m, s in zip(observed, errors, strict=True)
        ],
        "sampled_day_indexes_sha256": object_hash(indexes.tolist()),
        "resampled_means_sha256": object_hash(means.tolist()),
    }, {
        "sampled_day_indexes": indexes.tolist(),
        "resampled_means": means.tolist(),
        "maximum_statistics": maxima.tolist(),
    }


def calculate(archive, run_id):
    registered, declaration = read(archive, run_id)
    if (
        registered["kind"] != "experiment_protocol"
        or declaration["config"]["experiment"] != "E022-station-subgroup-diagnostic-v1"
    ):
        raise ValueError("Wrong diagnostic declaration")
    for path, expected in declaration["source_hashes"].items():
        if sha(path) != expected:
            raise ValueError("Diagnostic source/config/input changed: " + path)
    if importlib.metadata.version("numpy") != declaration["numpy_version"]:
        raise ValueError("Declared numerical environment changed")
    config = declaration["config"]
    _, parent = read(archive, config["parent_registration_record_id"])
    _, parent_report = read(archive, config["parent_report_record_id"], compressed=True)
    parent_config = parent["config"]
    manifest = json.loads(Path("reports/E022_manifest.json").read_bytes())
    cases = [case for case in manifest["cases"] if case["split"] == "development"]
    ids = [case["case_id"] for case in cases]
    stations = sorted({case["station_id"] for case in cases})
    days = sorted({case["target_ms"] // (24 * 3_600_000) for case in cases})
    if (
        len(cases) != 6599
        or len(set(ids)) != 6599
        or len(stations) != 20
        or len(days) != 28
        or days != list(range(days[0], days[0] + 28))
    ):
        raise ValueError("Declared common development panel changed")
    with Path(parent_config["observations_path"]).open() as handle:
        observations = observations_from_rows(
            (json.loads(line) for line in handle if line.strip()), parent_config
        )
    for case in cases:
        if (
            not timestamp(parent_config["calibration_fit_available"])
            <= case["decision_ms"]
            < case["target_ms"]
            < timestamp(parent_config["target_end_exclusive"])
        ):
            raise ValueError("Development case crosses original model/calibration boundary")
    observed = np.asarray(
        [observations[(case["station_id"], case["target_ms"])]["temperature_f"] for case in cases]
    )
    day_ids = np.asarray([case["target_ms"] // (24 * 3_600_000) for case in cases])
    station_ids = np.asarray([case["station_id"] for case in cases])
    horizons = np.asarray([case["horizon_hours"] for case in cases])
    errors = []
    for model in config["model_ids"]:
        card = next(card for card in parent_report["candidates"] if card["candidate"]["id"] == model)
        if card["status"] != "completed":
            raise ValueError("A previously known completed model is unavailable")
        if sha(card["predictions_file"]) != card["predictions_sha256"]:
            raise ValueError("Parent model prediction bytes changed")
        predictions = json.loads(Path(card["predictions_file"]).read_bytes())["predictions"]
        mapped = {p["case_id"]: p["point_f"] for p in predictions}
        if len(mapped) != len(predictions) or set(mapped) != {c["case_id"] for c in manifest["cases"]}:
            raise ValueError("Candidate prediction case panel changed")
        errors.append(np.asarray([mapped[identifier] for identifier in ids]) - observed)
    errors = np.asarray(errors)
    absolute = np.abs(errors)

    def weighted(values, mask):
        return np.mean(
            [values[mask & (day_ids == day)].mean() for day in days if (mask & (day_ids == day)).any()]
        )

    def summary(model_index, mask):
        return {
            "cases": int(mask.sum()),
            "utc_days": len(set(day_ids[mask].tolist())),
            "mae_f": float(weighted(absolute[model_index], mask)),
            "rmse_f": float(np.sqrt(weighted(errors[model_index] ** 2, mask))),
            "mean_signed_error_f": float(weighted(errors[model_index], mask)),
        }

    station_horizon_rows, station_rows, horizon_rows, model_rows = [], [], [], []
    all_cases = np.ones(len(cases), dtype=bool)
    for i, model in enumerate(config["model_ids"]):
        overall = summary(i, all_cases)
        if abs(overall["mae_f"] - config["known_day_weighted_mae_f"][model]) > 1e-12:
            raise ValueError("Previously known aggregate did not reproduce")
        model_rows.append({"model": model, **overall})
        for station in stations:
            station_rows.append({"model": model, "station": station, **summary(i, station_ids == station)})
            for horizon in (1, 3, 6):
                station_horizon_rows.append(
                    {
                        "model": model,
                        "station": station,
                        "horizon_hours": horizon,
                        **summary(i, (station_ids == station) & (horizons == horizon)),
                    }
                )
        for horizon in (1, 3, 6):
            horizon_rows.append({"model": model, "horizon_hours": horizon, **summary(i, horizons == horizon)})
    focal = config["model_ids"].index(config["focal_model"])
    daily_matrix = []
    comparisons, daily_rows = [], []
    for reference in config["comparison_models"]:
        j = config["model_ids"].index(reference)
        difference = absolute[focal] - absolute[j]
        daily = [float(difference[day_ids == day].mean()) for day in days]
        daily_matrix.append(daily)
        for day, value in zip(days, daily, strict=True):
            daily_rows.append(
                {
                    "reference": reference,
                    "day": datetime.fromtimestamp(day * 86400, UTC).date().isoformat(),
                    "cases": int((day_ids == day).sum()),
                    "fixed_fit_minus_reference_mae_f": value,
                }
            )
        slices = []
        for station in stations:
            for horizon in (1, 3, 6):
                mask = (station_ids == station) & (horizons == horizon)
                slices.append(
                    {
                        "station": station,
                        "horizon_hours": horizon,
                        "cases": int(mask.sum()),
                        "utc_days": len(set(day_ids[mask].tolist())),
                        "fixed_fit_minus_reference_mae_f": float(weighted(difference, mask)),
                    }
                )
        station_diffs = [float(weighted(difference, station_ids == station)) for station in stations]
        horizon_diffs = [float(weighted(difference, horizons == horizon)) for horizon in (1, 3, 6)]

        def counts(values):
            return {
                "better": sum(v < 0 for v in values),
                "tied": sum(v == 0 for v in values),
                "worse": sum(v > 0 for v in values),
            }

        contributions = []
        for station in stations:
            contribution = float(
                np.mean(
                    [
                        -difference[(station_ids == station) & (day_ids == day)].sum()
                        / (day_ids == day).sum()
                        for day in days
                    ]
                )
            )
            contributions.append({"station": station, "mae_reduction_contribution_f": contribution})
        horizon_contributions = []
        for horizon in (1, 3, 6):
            contribution = float(
                np.mean(
                    [
                        -difference[(horizons == horizon) & (day_ids == day)].sum() / (day_ids == day).sum()
                        for day in days
                    ]
                )
            )
            horizon_contributions.append(
                {"horizon_hours": horizon, "mae_reduction_contribution_f": contribution}
            )
        aggregate = float(np.mean(daily))
        if (
            abs(sum(c["mae_reduction_contribution_f"] for c in contributions) + aggregate) > 1e-12
            or abs(sum(c["mae_reduction_contribution_f"] for c in horizon_contributions) + aggregate) > 1e-12
        ):
            raise ValueError("Group contributions do not add to the aggregate gain")
        comparisons.append(
            {
                "reference": reference,
                "fixed_fit_minus_reference_mae_f": aggregate,
                "relative_mae_reduction": -aggregate / model_rows[j]["mae_f"],
                "stations": [
                    {"station": s, "fixed_fit_minus_reference_mae_f": v}
                    for s, v in zip(stations, station_diffs, strict=True)
                ],
                "horizons": [
                    {"horizon_hours": h, "fixed_fit_minus_reference_mae_f": v}
                    for h, v in zip((1, 3, 6), horizon_diffs, strict=True)
                ],
                "station_horizon_slices": slices,
                "station_counts": counts(station_diffs),
                "horizon_counts": counts(horizon_diffs),
                "station_horizon_counts": counts([s["fixed_fit_minus_reference_mae_f"] for s in slices]),
                "daily_counts": counts(daily),
                "station_gain_contributions": contributions,
                "horizon_gain_contributions": horizon_contributions,
            }
        )
    bootstrap, retained = shared_bootstrap(np.asarray(daily_matrix).T, config["bootstrap"])
    for i, comparison in enumerate(comparisons):
        comparison["bootstrap_standard_error_f"] = bootstrap["standard_errors"][i]
        comparison["simultaneous_descriptive_interval_f"] = bootstrap["intervals"][i]
        comparison["bootstrap_variance_positive"] = bootstrap["active_comparisons"][i]
    result = {
        "diagnostic_registration_record_id": run_id,
        "parent_registration_record_id": config["parent_registration_record_id"],
        "parent_report_record_id": config["parent_report_record_id"],
        "prior_access": config["prior_access"],
        "development_cases": len(cases),
        "utc_days": len(days),
        "stations": stations,
        "model_summaries": model_rows,
        "all_model_station_horizon_rows": station_horizon_rows,
        "all_model_station_rows": station_rows,
        "all_model_horizon_rows": horizon_rows,
        "comparisons": comparisons,
        "all_paired_daily_differences": daily_rows,
        "bootstrap": {**config["bootstrap"], **bootstrap},
        "model_fits": 0,
        "model_inferences": 0,
        "network_requests": 0,
        "sealed_2025_data_accessed": False,
        "promotion_eligible": False,
        "profitability_proven": False,
        "limits": config["limits"],
    }
    return result, retained


def self_check():
    settings = {"seed": 6202202, "resamples": 10000, "confidence": 0.95}
    x = np.arange(28, dtype=float)
    panel = np.stack([x, 2 * x, -x, np.zeros(28)], axis=1)
    summary, raw = shared_bootstrap(panel, settings)
    means = np.asarray(raw["resampled_means"])
    if (
        not np.array_equal(means[:, 1], 2 * means[:, 0])
        or not np.array_equal(means[:, 2], -means[:, 0])
        or summary["active_comparisons"] != [True, True, True, False]
    ):
        raise ValueError("Shared block arithmetic fixture failed")
    if summary["intervals"][3] != [0.0, 0.0]:
        raise ValueError("Zero-variance comparison fixture failed")
    indexes = np.asarray(raw["sampled_day_indexes"]).reshape(10000, 4, 7)
    if not np.all(np.diff(indexes, axis=2) % 28 == 1):
        raise ValueError("Circular blocks lost their consecutive days")
    print(json.dumps({"shared_block_fixtures": 3, "actual_model_or_subgroup_data_read": False}))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--verify-report-id", type=int)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    archive = Archive()
    try:
        if args.register_only:
            register(archive)
            return
        if args.verify_report_id:
            _, saved = read(archive, args.verify_report_id, compressed=True)
            result, bootstrap = calculate(archive, saved["result"]["diagnostic_registration_record_id"])
            _, saved_bootstrap = read(archive, saved["bootstrap_record_id"], compressed=True)
            if result != saved["result"] or bootstrap != saved_bootstrap:
                raise ValueError("Diagnostic report or shared bootstrap failed exact replay")
            print(
                json.dumps(
                    {
                        "report_record_id": args.verify_report_id,
                        "exact_report_replay": True,
                        "exact_bootstrap_replay": True,
                        "model_fits": 0,
                        "model_inferences": 0,
                    }
                )
            )
            return
        if not args.run_record_id:
            raise ValueError("Register diagnostic before reading subgroup errors")
        result, bootstrap = calculate(archive, args.run_record_id)
        bootstrap_id = archive.append(
            "research_dataset_gzip",
            f"E022_station_diagnostic_bootstrap:{args.run_record_id}",
            utcnow(),
            {},
            gzip.compress(canonical(bootstrap).encode(), mtime=0),
        )
        payload = {
            "generated_at": utcnow().isoformat(),
            "result": result,
            "bootstrap_record_id": bootstrap_id,
        }
        report_id = archive.append(
            "experiment_report_gzip",
            f"E022_station_diagnostic:{args.run_record_id}",
            utcnow(),
            {},
            gzip.compress(canonical(payload).encode(), mtime=0),
        )
        Path("reports/E022_station_diagnostics.json").write_text(
            json.dumps({**payload, "report_record_id": report_id}, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "diagnostic_registration_record_id": args.run_record_id,
                    "report_record_id": report_id,
                    "bootstrap_record_id": bootstrap_id,
                    "station_horizon_rows": len(result["all_model_station_horizon_rows"]),
                    "simultaneous_critical_value": result["bootstrap"]["simultaneous_critical_value"],
                    "comparisons": [
                        {
                            k: c[k]
                            for k in (
                                "reference",
                                "fixed_fit_minus_reference_mae_f",
                                "relative_mae_reduction",
                                "simultaneous_descriptive_interval_f",
                                "station_counts",
                                "horizon_counts",
                                "station_horizon_counts",
                                "daily_counts",
                            )
                        }
                        for c in result["comparisons"]
                    ],
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
