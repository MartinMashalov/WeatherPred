"""Event-weighted calibration and day-level diagnostics, no strategy selection."""

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    report = json.loads(Path("reports/E004_baselines.json").read_text())
    rows = [json.loads(line) for line in Path("reports/E004_binary.jsonl").read_text().splitlines()]
    sizes = Counter((r["model"], r["horizon_minutes"], r["event"]) for r in rows)
    bins = defaultdict(list)
    for r in rows:
        weight = 1 / sizes[(r["model"], r["horizon_minutes"], r["event"])]
        b = min(9, int(r["probability"] * 10))
        bins[(r["model"], r["horizon_minutes"], b)].append((r["probability"], r["outcome"], weight))
    calibration = []
    for (model, horizon, b), values in sorted(bins.items()):
        p, y, weights = np.asarray(values).T
        calibration.append(
            {
                "model": model,
                "horizon_minutes": horizon,
                "bin": b,
                "weight": float(weights.sum()),
                "contracts_descriptive_only": len(values),
                "mean_probability": float(np.average(p, weights=weights)),
                "observed_frequency": float(np.average(y, weights=weights)),
            }
        )
    comparisons = []
    scores = report["binary_scores_paired_event_averaged"]
    for h in (30, 15, 5):
        market = next(r for r in scores if (r["model"], r["horizon_minutes"]) == ("market_midpoint", h))
        for model in ("persistence_empirical", "persistence_gaussian", "trend_empirical", "trend_gaussian"):
            row = next(r for r in scores if (r["model"], r["horizon_minutes"]) == (model, h))
            daily = {day: value - market["daily_brier"][day] for day, value in row["daily_brier"].items()}
            cb = [r for r in calibration if (r["model"], r["horizon_minutes"]) == (model, h)]
            ece = sum(r["weight"] * abs(r["mean_probability"] - r["observed_frequency"]) for r in cb)
            ece /= sum(r["weight"] for r in cb)
            comparisons.append(
                {
                    "model": model,
                    "horizon_minutes": h,
                    "paired_events": row["events"],
                    "brier_minus_market": row["brier"] - market["brier"],
                    "daily_brier_minus_market": daily,
                    "days_better_than_market": sum(v < 0 for v in daily.values()),
                    "days": len(daily),
                    "event_weighted_calibration_error": ece,
                }
            )
    # Explicitly compare deterministic results across actual archived reruns.
    records = list(
        archive.db.execute(
            "SELECT * FROM records WHERE kind='experiment_report' AND key='E004_baselines' ORDER BY id"
        )
    )
    check_keys = (
        "training_examples",
        "validation_examples",
        "audit",
        "quote_coverage",
        "binary_scores_paired_event_averaged",
        "continuous_scores_all_validation",
    )
    first, last = archive.json(records[0]), archive.json(records[-1])
    equal = all(first[k] == last[k] for k in check_keys)
    if not equal:
        raise ValueError("Baseline results changed on raw-data replay")
    result = {
        "experiment": "E004-v1-diagnostics",
        "generated_at": iso(utcnow()),
        "calibration": calibration,
        "paired_daily_comparisons": comparisons,
        "replay": {
            "original_record": records[0]["id"],
            "latest_record": records[-1]["id"],
            "matching_scores_and_coverage": equal,
            "latest_network_requests": last["network_requests"],
        },
        "confidence_statement": "Only five validation days; no profitability or robust confidence claim",
        "profitability_proven": False,
    }
    archive.append("experiment_report", "E004_diagnostics", utcnow(), {}, canonical(result).encode())
    Path("reports/E004_diagnostics.json").write_text(json.dumps(result, indent=2))
    print(json.dumps({"replay": result["replay"], "comparisons": comparisons}, indent=2))
    archive.close()


if __name__ == "__main__":
    main()
