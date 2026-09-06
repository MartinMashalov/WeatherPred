"""Rebuild E007 source inputs and independently score its frozen probability pools."""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from research.experiments.e002_baselines import read_rows
from research.experiments.e007_market_weather_pool import (
    by_id,
    complete_events,
    digest,
    inputs,
)
from weatherpred.archive import Archive, canonical
from weatherpred.daily_forecasts import predict_model
from weatherpred.forecast_pool import chronological_prefix
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    archive = Archive()
    try:
        summary = json.loads(Path("reports/E007_market_weather_pool.json").read_text())
        artifact = by_id(archive, summary["model_record_id"])
        protocol = artifact["protocol"]
        base, weather_rows, markets, quotes = inputs(archive, protocol)
        events, _ = complete_events(weather_rows, markets, quotes, protocol)
        train = [r for r in events if r["split"] == "train"]
        if digest(train) != artifact["training_events_sha256"]:
            raise ValueError("Rebuilt pool training event/source lineage differs")
        folds = {r["month"][:7]: r for r in artifact["folds"]}
        for fold in folds.values():
            cutoff = int(parse_time(fold["fit_cutoff"]).timestamp())
            prefix = chronological_prefix(
                [r for r in weather_rows if r["split"] == "train"], fold["month"], cutoff
            )
            if digest(prefix) != fold["training_data_sha256"]:
                raise ValueError("Cross-fitting prefix differs from frozen fold")
            if max(max(r["settled_ts"], r["nws_issue_ts"]) for r in prefix) >= cutoff:
                raise ValueError("Future outcome reached a chronological fold")
        reconstructed = {}
        for row in train:
            if row["event"] in reconstructed:
                continue
            fold = folds[row["day"][:7]]
            reconstructed[row["event"]] = {
                name: [
                    predict_model(fold["models"], name, row["weather_row"]).probability(m)
                    for m in row["contracts"]
                ]
                for name in protocol["weather_models"]
            }
        if digest(reconstructed) != artifact["out_of_fit_probabilities_sha256"]:
            raise ValueError("Replayed out-of-fit probabilities differ from frozen training input")
        eligible = [
            r for r in events if r["split"] == "validation" and str(r["horizon_hours"]) in artifact["fits"]
        ]
        model_names = {"raw_market", "normalized_market", "temperature_market", *protocol["weather_models"]}
        expected = {
            (row["event"], row["horizon_hours"], contract["ticker"], name)
            for row in eligible
            for contract in row["contracts"]
            for name in model_names
        }
        saved = read_rows("reports/E007_scored.jsonl")
        lookup = {(r["event"], r["horizon_hours"], r["ticker"], r["model"]): r for r in saved}
        if set(lookup) != expected or len(lookup) != len(saved):
            raise ValueError("Saved score membership is incomplete or duplicated")
        errors, daily_events = [], defaultdict(lambda: defaultdict(list))
        categorical, max_partition_error = defaultdict(list), 0.0
        for row in eligible:
            raw = np.asarray(row["raw_midpoints"])
            q = raw / raw.sum()
            probabilities = {"raw_market": raw, "normalized_market": q}
            fits = artifact["fits"][str(row["horizon_hours"])]
            for name, fit in fits.items():
                if name == "temperature_market":
                    weather = np.full(len(q), 1 / len(q))
                else:
                    distribution = predict_model(base["models"], name, row["weather_row"])
                    weather = np.asarray([distribution.probability(m) for m in row["contracts"]])
                # Independent implementation: no pool.predict or binary_metrics call.
                theta = fit["model"]
                log_p = theta["market_power"] * np.log(q) + theta["weather_power"] * np.log(
                    np.maximum(weather, 1e-6)
                )
                unnormalized = np.exp(log_p - log_p.max())
                probabilities[name] = unnormalized / unnormalized.sum()
            for name, p in probabilities.items():
                if name != "raw_market":
                    max_partition_error = max(max_partition_error, abs(float(sum(p)) - 1))
                    categorical[name, row["horizon_hours"], row["day"]].append(
                        float(-np.log(max(p[row["winner"]], 1e-6)))
                    )
                scores = {"brier": [], "log_loss": []}
                event_count = sum(
                    r["day"] == row["day"] and r["horizon_hours"] == row["horizon_hours"] for r in eligible
                )
                for i, contract in enumerate(row["contracts"]):
                    actual = lookup[row["event"], row["horizon_hours"], contract["ticker"], name]
                    outcome = int(i == row["winner"])
                    clipped = min(1 - 1e-6, max(1e-6, p[i]))
                    values = {
                        "probability": p[i],
                        "brier": (p[i] - outcome) ** 2,
                        "log_loss": -np.log(clipped if outcome else 1 - clipped),
                        "weight": 1 / len(q) / event_count,
                    }
                    if actual["outcome"] != outcome:
                        raise ValueError("Saved binary outcome differs from complete categorical target")
                    for field, value in values.items():
                        error = abs(actual[field] - value)
                        if error > 1e-12:
                            raise ValueError("Replayed score or day weight differs: " + field)
                        errors.append(error)
                    for metric, score_values in scores.items():
                        score_values.append(values[metric])
                for metric, values in scores.items():
                    daily_events[name, row["horizon_hours"], metric][row["day"]].append(
                        float(np.mean(values))
                    )
        for report in summary["validation_scores"]:
            for metric in ("brier", "log_loss"):
                day_means = {
                    day: float(np.mean(v))
                    for day, v in daily_events[report["model"], report["horizon_hours"], metric].items()
                }
                for day, mean in day_means.items():
                    errors.append(abs(mean - report["daily"][day][metric]))
                errors.append(abs(float(np.mean(list(day_means.values()))) - report[metric]))
        for report in summary["multiclass_log_loss"]:
            means = [
                float(np.mean(values))
                for (name, horizon, _), values in categorical.items()
                if (name, horizon) == (report["model"], report["horizon_hours"])
            ]
            errors.append(abs(float(np.mean(means)) - report["log_loss"]))
        if max(errors) > 1e-12:
            raise ValueError("Replayed event/day aggregates differ from saved report")
        result = {
            "generated_at": iso(utcnow()),
            "model_record_id": summary["model_record_id"],
            "chronological_folds_reproduced": len(folds),
            "training_event_horizons_reproduced": len(train),
            "out_of_fit_event_distributions_reproduced": len(reconstructed) * len(protocol["weather_models"]),
            "validation_score_rows_reproduced": len(saved),
            "complete_score_membership_verified": True,
            "event_then_day_aggregation_verified": True,
            "maximum_numeric_difference": float(max(errors)),
            "maximum_partition_error": max_partition_error,
            "model_refits": 0,
            "network_requests": 0,
            "profitability_proven": False,
        }
        archive.append("experiment_report", "E007_raw_replay", utcnow(), {}, canonical(result).encode())
        Path("reports/E007_replay.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
