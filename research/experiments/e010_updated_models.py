"""Fixed-method updated NBM benchmarks; original datasets and models remain pinned."""

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import numpy as np

from research.experiments.e002_baselines import event_weights
from research.experiments.e003_daily_baselines import summaries_and_intervals
from research.experiments.e007_market_weather_pool import by_id, inputs
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import LogisticCalibration, binary_metrics, holm_adjust
from weatherpred.daily_forecasts import fit_daily_models, predict_model
from weatherpred.timeutil import iso, parse_time, utcnow
from weatherpred.updated_forecasts import updated_features


def wait_for_acquisition(seconds):
    deadline = time.monotonic() + seconds
    while True:
        path = Path("reports/E010_acquisition.json")
        try:
            report = json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            report = {}
        if report.get("stop_reason") == "all_development_cycles_attempted":
            identities = [(r["day"], r["cycle_utc_hour"]) for r in [*report["cycles"], *report["errors"]]]
            if len(identities) != 546 or len(set(identities)) != 546:
                raise ValueError("Updated acquisition does not cover the registered date/cycle census")
            return report
        if time.monotonic() >= deadline or Path("data/STOP_E010_ANALYSIS").exists():
            raise ValueError("Updated acquisition incomplete; no fit or score")
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def build_updates(archive, rows, stations, windows):
    cache, result, coverage = {}, [], []
    for row in rows:
        day = row["day"]
        if day not in cache:
            early = by_id(archive, row["nbm_record_id"])
            sources = [dict(early, record_id=row["nbm_record_id"])]
            for hour in (7, 13):
                rec = archive.latest("e010_nbm_cycle", f"{day}:{hour:02}")
                if rec:
                    sources.append(dict(archive.json(rec), record_id=rec["id"]))
            cache[day] = sources
        sources = cache[day]
        offset = windows[row["series"]]["standard_utc_offset_hours"]
        station = stations[row["series"]]
        # Control intentionally contains only the original01UTC object. A later
        # cycle could legitimately precede a western station's day start.
        start = parse_time(row["features"]["source_period_start"])
        control = updated_features(day, offset, station, [sources[0]], start)
        if any(control[k] != row["features"][k] for k in ("grid_max", "txn_18h", "xnd_18h")):
            raise ValueError("Explicit early-only control differs from frozen E003 features")
        decision = parse_time(row["features"]["source_period_end"]) - timedelta(hours=12)
        features = updated_features(day, offset, station, sources, decision)
        result.append({**row, "features": features})
        coverage.append(
            {
                "event": row["event"],
                "day": day,
                "source_records_present": [s["record_id"] for s in sources],
                "source_records_eligible": features["eligible_source_record_ids"],
                "selected_proxy_runtime": features["runtime"],
                "delta_grid_max": features["grid_max"] - row["features"]["grid_max"],
                "delta_txn": features["txn_18h"] - row["features"]["txn_18h"],
            }
        )
    return result, coverage


def compare(scored, names):
    summaries, market_intervals, reliability = summaries_and_intervals(scored, names)
    comparisons = []
    for r in market_intervals:
        r.pop("holm_adjusted_pvalue_weather_models_and_horizons", None)
        comparisons.append(dict(r, reference="raw_market"))
    for name in names:
        old = "old_" + name
        pair = [
            dict(r, model="raw_market" if r["model"] == old else r["model"])
            for r in scored
            if r["model"] in (name, old)
        ]
        _, intervals, _ = summaries_and_intervals(pair, [name])
        for r in intervals:
            r.pop("holm_adjusted_pvalue_weather_models_and_horizons", None)
            comparisons.append(dict(r, reference=old))
    for block in (1, 7, 14):
        for metric in ("brier", "log_loss"):
            group = [r for r in comparisons if r["block_days"] == block and r["metric"] == metric]
            if len(group) == 12 and all("one_sided_improvement_pvalue_approximate" in r for r in group):
                adjusted = holm_adjust([r["one_sided_improvement_pvalue_approximate"] for r in group])
                for r, p in zip(group, adjusted, strict=True):
                    r["holm_adjusted_12_development_comparisons"] = p
    return summaries, comparisons, reliability


def main(wait_seconds):
    archive = Archive()
    try:
        config = json.loads(Path("config/e010_updated_models.json").read_text())
        paths = [
            Path(__file__),
            Path("config/e010_updated_models.json"),
            Path("weatherpred/updated_forecasts.py"),
            Path("weatherpred/daily_forecasts.py"),
            Path("weatherpred/calibration.py"),
            Path("research/experiments/e003_daily_baselines.py"),
            Path("research/experiments/e007_market_weather_pool.py"),
        ]
        sources = {
            str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes()) for p in paths
        }
        protocol_id = archive.append(
            "experiment_protocol",
            config["experiment"],
            utcnow(),
            {},
            canonical({"config": config, "source_record_ids": sources}).encode(),
        )
        print(
            json.dumps(
                {"registered_scorer_before_updated_results": protocol_id, "waiting_for_acquisition": True}
            ),
            flush=True,
        )
        acquisition = wait_for_acquisition(wait_seconds)
        base, original_rows, markets, quotes = inputs(
            archive, {"weather_model_record_id": config["base_model_record_id"]}
        )
        stations = json.loads(Path("config/e010_nbm_updates.json").read_text())["stations"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        updated, coverage = build_updates(archive, original_rows, stations, windows)
        train = [r for r in updated if r["split"] == "train"]
        models, fit_audit = fit_daily_models(train, config)
        artifact = {
            "published_at": iso(utcnow()),
            "config": config,
            "protocol_record_id": protocol_id,
            "source_record_ids": sources,
            "models": models,
            "fit_audit": fit_audit,
            "training_events": len(train),
            "training_days": len({r["day"] for r in train}),
            "training_sha256": hashlib.sha256(canonical(train).encode()).hexdigest(),
            "promotion_eligible": False,
        }
        model_id = archive.append(
            "model_artifact", config["experiment"], utcnow(), {}, canonical(artifact).encode()
        )
        Path("reports/E010_model.json").write_text(json.dumps({**artifact, "record_id": model_id}, indent=2))
        print(json.dumps({"updated_model_frozen_before_validation": model_id}), flush=True)
        weather = {r["event"]: r for r in updated if r["split"] == "validation"}
        old_weather = {r["event"]: r for r in original_rows if r["split"] == "validation"}
        events = defaultdict(list)
        for m in markets:
            if m["event"] in weather:
                events[m["event"]].append(m)
        probabilities, continuous, partitions = {}, [], []
        for event, row in weather.items():
            for name in config["models"]:
                for prefix, fitted, features in (
                    ("", models, row),
                    ("old_", base["models"], old_weather[event]),
                ):
                    distribution = predict_model(fitted, name, features)
                    p = {m["ticker"]: distribution.probability(m) for m in events[event]}
                    if abs(sum(p.values()) - 1) > 1e-10:
                        raise ValueError("Updated full-event distribution is not coherent")
                    probabilities[event, prefix + name] = p
                    partitions.append({"event": event, "model": prefix + name, "sum": sum(p.values())})
                    if not prefix:
                        intervals = {str(level): distribution.interval(level) for level in (0.8, 0.95)}
                        continuous.append(
                            {
                                "event": event,
                                "day": row["day"],
                                "model": name,
                                "error": distribution.mean - row["observed_f"],
                                "coverage": {
                                    k: lo <= row["observed_f"] <= hi for k, (lo, hi) in intervals.items()
                                },
                            }
                        )
        paired = [
            r
            for r in quotes
            if r["split"] == "validation" and r["horizon_hours"] == 12 and r["event"] in weather
        ]
        old_market = by_id(archive, 20410)
        logistic = LogisticCalibration(
            **next(r["model"] for r in old_market["models"] if r["horizon_hours"] == 12)
        )
        scored = []
        for quote, weight in zip(paired, event_weights(paired), strict=True):
            p = {
                name: probabilities[quote["event"], name][quote["ticker"]]
                for name in [*config["models"], *["old_" + n for n in config["models"]]]
            }
            p.update(
                raw_market=quote["midpoint"], logistic_market=float(logistic.predict([quote["midpoint"]])[0])
            )
            for name, probability in p.items():
                metrics = binary_metrics([probability], [quote["outcome"]])
                scored.append(
                    {
                        **{
                            k: quote[k]
                            for k in ("ticker", "event", "series", "day", "horizon_hours", "outcome")
                        },
                        "model": name,
                        "probability": probability,
                        "weight": weight,
                        **{k: float(v[0]) for k, v in metrics.items()},
                    }
                )
        summaries, comparisons, reliability = compare(scored, config["models"])
        continuous_summary = []
        for name in config["models"]:
            group = [r for r in continuous if r["model"] == name]
            counts = Counter(r["day"] for r in group)
            w = [1 / counts[r["day"]] for r in group]
            continuous_summary.append(
                {
                    "model": name,
                    "events": len(group),
                    "days": len(counts),
                    "mae": float(np.average([abs(r["error"]) for r in group], weights=w)),
                    "rmse": float(np.sqrt(np.average([r["error"] ** 2 for r in group], weights=w))),
                    "coverage": {
                        level: float(np.average([r["coverage"][level] for r in group], weights=w))
                        for level in ("0.8", "0.95")
                    },
                }
            )
        result = {
            "generated_at": iso(utcnow()),
            "model_record_id": model_id,
            "validation_scores": summaries,
            "paired_day_block_comparisons": comparisons,
            "calibration": reliability,
            "continuous_scores": continuous_summary,
            "acquisition_errors_retained": acquisition["errors"],
            "maximum_partition_error": max(abs(r["sum"] - 1) for r in partitions),
            "proxy_cycles": dict(Counter(r["selected_proxy_runtime"][11:13] for r in coverage)),
            "historical_pnl": None,
            "holdout_accessed": False,
            "profitability_proven": False,
            "network_requests": 0,
            "limitations": [config[k] for k in ("status", "missing", "target_limits")],
        }
        archive.append("experiment_report", "E010_updated_models", utcnow(), {}, canonical(result).encode())
        Path("reports/E010_updated_models.json").write_text(json.dumps(result, indent=2))
        for name, rows in (
            ("features", updated),
            ("coverage", coverage),
            ("scored", scored),
            ("partitions", partitions),
            ("continuous", continuous),
        ):
            body = "\n".join(canonical(r) for r in rows) + "\n"
            archive.append("research_dataset", "E010_" + name, utcnow(), {}, body.encode())
            Path(f"reports/E010_{name}.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "validation_scores": [{k: v for k, v in r.items() if k != "daily"} for r in summaries],
                    "continuous_scores": continuous_summary,
                    "profitability_proven": False,
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait-seconds", type=int, default=0)
    args = parser.parse_args()
    if not 0 <= args.wait_seconds <= 3600:
        parser.error("Wait must be between0and3600seconds")
    main(args.wait_seconds)
