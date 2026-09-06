"""Report the pinned E002 fit while retaining unavailable calendar-block inference.

The original run's failure is preserved. No refit, quote relaxation, date-gap
compression or replacement confidence estimator is permitted here.
"""

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from research.experiments.e002_baselines import build_quotes, read_rows, score_validation
from research.experiments.e003_daily_baselines import summaries_and_intervals
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import LogisticCalibration
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    try:
        model_row = archive.db.execute(
            "SELECT * FROM records WHERE id=20410 AND kind='model_artifact'"
        ).fetchone()
        model = archive.json(model_row)
        amendment = {
            "experiment": "E002-report-recovery-v1",
            "model_record_id": model_row["id"],
            "reason": "Original inference correctly rejected nonconsecutive eligible validation dates",
            "action": "Publish frozen model scores and explicit confidence-unavailable fields; retain the original estimator and reject compressing gaps",
            "no_model_refit": True,
            "no_quote_or_date_relaxation": True,
            "multiplicity": "No Holm correction reported for an incomplete four-horizon confidence family",
            "implementation_source_records": {},
        }
        for path in (
            Path(__file__),
            Path("research/experiments/e003_daily_baselines.py"),
            Path("research/experiments/e002_baselines.py"),
            Path("weatherpred/calibration.py"),
        ):
            amendment["implementation_source_records"][str(path)] = archive.append(
                "research_source", str(path), utcnow(), {}, path.read_bytes()
            )
        protocol_id = archive.append(
            "experiment_protocol", amendment["experiment"], utcnow(), {}, canonical(amendment).encode()
        )
        markets = read_rows("reports/E002_development_markets.jsonl")
        acquisition = json.loads(Path("reports/E002_acquisition.json").read_text())
        protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
        details = json.loads(Path("config/e002_scoring.json").read_text())
        if acquisition["errors"] or {r["ticker"] for r in acquisition["rows"]} != {
            m["ticker"] for m in markets
        }:
            raise ValueError("Incomplete or changed acquisition")
        quotes, statuses = build_quotes(archive, markets, acquisition, protocol, details)
        if hashlib.sha256(canonical(quotes).encode()).hexdigest() != model["data_sha256"]:
            raise ValueError("Quote dataset differs from the already fitted model")
        models = {r["horizon_hours"]: LogisticCalibration(**r["model"]) for r in model["models"]}
        scored = score_validation([r for r in quotes if r["split"] == "validation"], models)
        summaries, comparisons, calibration = summaries_and_intervals(scored, ["logistic"])
        for comparison in comparisons:
            if "holm_adjusted_pvalue_weather_models_and_horizons" in comparison:
                comparison["holm_adjusted_pvalue_four_horizons"] = comparison.pop(
                    "holm_adjusted_pvalue_weather_models_and_horizons"
                )
        event_sizes = Counter(m["event"] for m in markets)
        grouped = defaultdict(list)
        for row in scored:
            grouped[(row["model"], row["horizon_hours"], row["event"])].append(row["probability"])
        coherence = [
            {
                "model": name,
                "horizon_hours": horizon,
                "event": event,
                "probability_sum": sum(p),
                "complete_event": len(p) == event_sizes[event],
            }
            for (name, horizon, event), p in grouped.items()
        ]
        result = {
            "experiment": "E002-v1",
            "generated_at": iso(utcnow()),
            "model_artifact_record_id": model_row["id"],
            "report_recovery_protocol_record_id": protocol_id,
            "training": model["models"],
            "validation_scores": summaries,
            "paired_day_block_comparisons": comparisons,
            "calibration": calibration,
            "development_contracts": len(markets),
            "quote_examples": len(quotes),
            "coverage": [
                {"split": split, "horizon_hours": h, "status": status, "count": n}
                for (split, h, status), n in Counter(
                    (r["split"], r["horizon_hours"], r["status"]) for r in statuses
                ).items()
            ],
            "confidence_unavailable_comparisons": sum("confidence_unavailable" in r for r in comparisons),
            "network_requests": 0,
            "holdout_accessed": False,
            "profitability_proven": False,
            "historical_pnl": None,
            "limitations": [
                "Frozen model recovered without refit after original calendar-gap failure",
                "Some horizons have missing eligible calendar days; their block inference is unavailable",
                "Incomplete confidence family receives no reduced-family Holm result",
                "Historical quote/metadata availability and revisions are not independently verified",
                "Scores condition on exact two-sided quotes; no depth, fills, fees or trading returns",
            ],
        }
        archive.append("experiment_report", "E002_baselines", utcnow(), {}, canonical(result).encode())
        Path("reports/E002_baselines.json").write_text(json.dumps(result, indent=2))
        for name, rows in (
            ("quotes", quotes),
            ("scored", scored),
            ("coverage", statuses),
            ("coherence", coherence),
        ):
            body = "\n".join(canonical(r) for r in rows) + "\n"
            archive.append("research_dataset", "E002_" + name, utcnow(), {}, body.encode())
            Path(f"reports/E002_{name}.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "model_artifact_record_id": model_row["id"],
                    "validation_scores": [{k: v for k, v in r.items() if k != "daily"} for r in summaries],
                    "confidence_unavailable_comparisons": result["confidence_unavailable_comparisons"],
                    "holdout_accessed": False,
                    "profitability_proven": False,
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
