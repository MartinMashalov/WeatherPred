"""Offline August-only model replay and descriptive earlier-fold comparators."""

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import norm

from research.experiments.e018_conditional_hourly import record, verify_sources
from weatherpred.archive import Archive, canonical
from weatherpred.conditional_forecasts import DAY, MINUTE, select_and_fit
from weatherpred.freshness import train_fresh_model
from weatherpred.timeutil import iso, utcnow


def main(model_id):
    archive = Archive()
    try:
        artifact = archive.json(record(archive, model_id))
        run = archive.json(record(archive, artifact["run_record_id"]))
        verify_sources(run)
        cfg = run["config"]
        old = archive.json(record(archive, artifact["baseline_model_record_id"]))
        points = [
            p
            for identifier in old["history_record_ids"]
            for p in archive.json(record(archive, identifier))["timeseries"]
        ]
        _, rebuilt, _ = train_fresh_model(points, old["protocol"])
        if hashlib.sha256(canonical(rebuilt).encode()).hexdigest() != artifact["training_sha256"]:
            raise ValueError("Raw August source replay does not reproduce training rows")
        fit = select_and_fit(rebuilt, cfg)
        predictions = fit.pop("predictions")
        raw_predictions = record(archive, artifact["selection_predictions_record_id"])
        if hashlib.sha256(canonical(predictions).encode()).hexdigest() != raw_predictions["body_sha256"]:
            raise ValueError("Earlier-fold predictions differ from registered artifact")
        if any(fit[k] != artifact[k] for k in ("chosen_candidate", "models", "candidate_scores")):
            raise ValueError("Conditional coefficients, scale or selection differ on replay")
        selected = [p for p in predictions if p["candidate"] == artifact["chosen_candidate"]["id"]]
        comparison = [{"model": "conditional_selected", **p} for p in selected]
        for offset in cfg["fold_start_day_offsets"]:
            cutoff = cfg["training_start_ms"] + offset * DAY
            stop = min(cutoff + cfg["fold_days"] * DAY, cfg["training_end_ms"])
            for horizon in cfg["horizons_minutes"]:
                training = [
                    r
                    for r in rebuilt
                    if r["horizon_minutes"] == horizon and r["settlement_ms"] + 5 * MINUTE < cutoff
                ]
                validation = [
                    r
                    for r in rebuilt
                    if r["horizon_minutes"] == horizon
                    and cutoff <= r["decision_ms"]
                    and r["settlement_ms"] < stop
                ]
                for base in ("persistence", "trend"):
                    errors = np.asarray([r["observed"] - r["features"][base] for r in training])
                    bias, sigma = float(errors.mean()), max(0.05, float(errors.std(ddof=1)))
                    for row in validation:
                        mean = row["features"][base] + bias
                        comparison.append(
                            {
                                "model": base + "_gaussian",
                                "settlement_ms": row["settlement_ms"],
                                "horizon_minutes": horizon,
                                "residual": row["observed"] - mean,
                                "negative_log_density": float(
                                    -norm.logpdf(row["observed"], loc=mean, scale=sigma)
                                ),
                            }
                        )
        summaries = []
        for model in ("conditional_selected", "persistence_gaussian", "trend_gaussian"):
            for horizon in [None, *cfg["horizons_minutes"]]:
                rows = [
                    r
                    for r in comparison
                    if r["model"] == model and (horizon is None or r["horizon_minutes"] == horizon)
                ]
                daily = defaultdict(list)
                for r in rows:
                    daily[r["settlement_ms"] // DAY].append(r["negative_log_density"])
                summaries.append(
                    {
                        "model": model,
                        "horizon_minutes": horizon,
                        "rows": len(rows),
                        "days": len(daily),
                        "mean_day_negative_log_density": float(np.mean([np.mean(v) for v in daily.values()])),
                        "rmse_f": float(np.sqrt(np.mean([r["residual"] ** 2 for r in rows]))),
                        "mean_error_f": float(np.mean([r["residual"] for r in rows])),
                    }
                )
        result = {
            "generated_at": iso(utcnow()),
            "run_record_id": artifact["run_record_id"],
            "model_record_id": model_id,
            "training_rows_rebuilt_from_raw": len(rebuilt),
            "earlier_fold_predictions_replayed": len(predictions),
            "candidate_count": len(cfg["candidates"]),
            "chosen_candidate": artifact["chosen_candidate"],
            "coefficients_scales_and_selection_reproduced": True,
            "comparisons": summaries,
            "network_requests": 0,
            "september_validation_accessed": False,
            "holdout_accessed": False,
            "profitability_proven": False,
            "limits": "All comparators use the eight August model-selection days. The selected candidate benefited from that selection; these are development diagnostics, not unbiased generalization estimates. Historical publication is unverified and no historical fills/P&L are inferred.",
        }
        archive.append("experiment_report", "E018_august_replay", utcnow(), {}, canonical(result).encode())
        Path("reports/E018_diagnostics.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-record-id", type=int, default=59490)
    main(parser.parse_args().model_record_id)
