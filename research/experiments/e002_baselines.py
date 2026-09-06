"""E002 daily market/calibration study with a sealed final holdout.

Requires complete acquisition. Optional bounded --wait-seconds allows unattended
continuation once that independent public download finishes. No trading returns.
"""

import argparse
import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.calibration import (
    binary_metrics,
    block_mean_interval,
    fit_logistic,
    holm_adjust,
    require_training_rows,
)
from weatherpred.forecasts import candle_quote
from weatherpred.historical_candles import normalize_historical_candles
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line]


def wait_for_acquisition(expected_tickers, seconds):
    deadline = time.monotonic() + seconds
    last_log = 0.0
    while True:
        path = Path("reports/E002_acquisition.json")
        try:
            report = json.loads(path.read_text()) if path.exists() else {}
        except json.JSONDecodeError:
            # The independent writer may be partway through its final report.
            report = {}
        received = {r["ticker"] for r in report.get("rows", [])}
        if report.get("stop_reason") == "all_development_markets_attempted":
            if report.get("errors") or received != expected_tickers:
                raise ValueError("Completed acquisition has missing/error requests; reconcile before scoring")
            return report
        if time.monotonic() >= deadline or Path("data/STOP_E002_ANALYSIS").exists():
            raise ValueError("Daily acquisition is incomplete; no fit or scores computed")
        if time.monotonic() - last_log >= 60:
            progress_path = Path("reports/E002_acquisition_progress.json")
            try:
                progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
            except json.JSONDecodeError:
                progress = {}
            LOG.info(
                "Awaiting complete acquisition: %s/%s reported; no probability scores computed",
                len(progress.get("rows", [])),
                len(expected_tickers),
            )
            last_log = time.monotonic()
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def event_weights(rows):
    event_sizes = Counter((r["day"], r["event"]) for r in rows)
    events_per_day = Counter(day for day, _ in event_sizes)
    return [1 / event_sizes[(r["day"], r["event"])] / events_per_day[r["day"]] for r in rows]


def build_quotes(archive, markets, acquisition, protocol, details):
    fit_ts = int(parse_time(details["fit_cutoff"]).timestamp())
    records = {r["ticker"]: r["record_id"] for r in acquisition["rows"]}
    page_cache, quote_rows, statuses = {}, [], []
    for m in markets:
        # Reject contamination before reading outcomes or quote bodies.
        if not protocol["development_start"] <= m["day"] < protocol["validation_end_exclusive"]:
            raise ValueError("Sealed/out-of-window date reached daily scoring")
        rec = m["source_record_id"]
        if rec not in page_cache:
            row = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            page_cache[rec] = {raw["ticker"]: raw for raw in archive.json(row)["markets"]}
        raw = page_cache[rec][m["ticker"]]
        if (
            raw["event_ticker"] != m["event"]
            or raw["result"] not in ("yes", "no")
            or int(raw["result"] == "yes") != m["outcome"]
        ):
            raise ValueError("Derived development metadata differs from original contract")
        settled_ts = int(parse_time(raw["settlement_ts"]).timestamp()) if raw.get("settlement_ts") else None
        candle_record = archive.db.execute(
            "SELECT * FROM records WHERE id=?", (records[m["ticker"]],)
        ).fetchone()
        data = archive.json(candle_record)
        if data.get("ticker") != m["ticker"]:
            raise ValueError("Daily candle/market lineage mismatch")
        normalized_candles = normalize_historical_candles(data["candlesticks"])
        for horizon in protocol["decision_hours_before_period_end"]:
            decision = m["decision_ts"][str(horizon)]
            status = {
                "ticker": m["ticker"],
                "event": m["event"],
                "series": m["series"],
                "day": m["day"],
                "split": m["split"],
                "horizon_hours": horizon,
            }
            reason = None
            if m["split"] == "train" and (settled_ts is None or settled_ts >= fit_ts):
                reason = "training_label_not_settled_before_fit"
            elif m["split"] == "validation" and decision < fit_ts:
                reason = "decision_precedes_model_fit"
            elif not m["open_ts"] <= decision < m["close_ts"]:
                reason = "market_not_open_at_decision"
            quote = candle_quote(normalized_candles, decision, max_age_seconds=0) if not reason else None
            if not reason and quote is None:
                reason = "missing_exact_two_sided_quote"
            statuses.append(dict(status, status=reason or "eligible"))
            if reason:
                continue
            quote_rows.append(
                {
                    **status,
                    **quote,
                    "decision_ts": decision,
                    "settled_ts": settled_ts,
                    "outcome": m["outcome"],
                    "candle_record_id": candle_record["id"],
                    "source_record_id": rec,
                    "predicate_provenance": m["predicate_provenance"],
                    "strike_type": m["strike_type"],
                }
            )
    return quote_rows, statuses


def score_validation(rows, models):
    scored = []
    for horizon in sorted(models):
        group = [r for r in rows if r["horizon_hours"] == horizon]
        if not group:
            raise ValueError("No validation quotes at a predefined horizon")
        probabilities = {
            "raw_market": np.asarray([r["midpoint"] for r in group]),
            "market_bid": np.asarray([r["bid"] for r in group]),
            "market_ask": np.asarray([r["ask"] for r in group]),
        }
        probabilities["logistic"] = models[horizon].predict(probabilities["raw_market"])
        outcomes = np.asarray([r["outcome"] for r in group])
        weights = event_weights(group)
        for name, p in probabilities.items():
            metrics = binary_metrics(p, outcomes)
            for i, r in enumerate(group):
                scored.append(
                    {
                        **r,
                        "model": name,
                        "probability": float(p[i]),
                        "weight": weights[i],
                        **{key: float(value[i]) for key, value in metrics.items()},
                    }
                )
    return scored


def summarize(scored, resamples):
    grouped, calibration = defaultdict(list), defaultdict(list)
    for r in scored:
        grouped[(r["model"], r["horizon_hours"])].append(r)
        calibration[(r["model"], r["horizon_hours"], min(9, int(10 * r["probability"])))].append(r)
    summaries = []
    for (model, horizon), rows in sorted(grouped.items()):
        days = sorted({r["day"] for r in rows})
        weights = np.asarray([r["weight"] for r in rows])
        daily = {
            day: {
                metric: float(
                    np.average(
                        [r[metric] for r in rows if r["day"] == day],
                        weights=[r["weight"] for r in rows if r["day"] == day],
                    )
                )
                for metric in ("brier", "log_loss")
            }
            for day in days
        }
        summaries.append(
            {
                "model": model,
                "horizon_hours": horizon,
                "days": len(days),
                "events": len({r["event"] for r in rows}),
                "contracts_descriptive": len(rows),
                **{
                    metric: float(np.average([r[metric] for r in rows], weights=weights))
                    for metric in ("brier", "log_loss")
                },
                "daily": daily,
            }
        )
    bins = []
    for (model, horizon, b), rows in sorted(calibration.items()):
        weights = [r["weight"] for r in rows]
        bins.append(
            {
                "model": model,
                "horizon_hours": horizon,
                "bin": b,
                "weight": sum(weights),
                "contracts_descriptive": len(rows),
                "mean_probability": float(np.average([r["probability"] for r in rows], weights=weights)),
                "observed_frequency": float(np.average([r["outcome"] for r in rows], weights=weights)),
            }
        )
    comparisons = []
    for horizon in sorted({r["horizon_hours"] for r in summaries}):
        raw = next(r for r in summaries if (r["model"], r["horizon_hours"]) == ("raw_market", horizon))
        fitted = next(r for r in summaries if (r["model"], r["horizon_hours"]) == ("logistic", horizon))
        days = sorted(raw["daily"])
        for metric in ("brier", "log_loss"):
            values = [fitted["daily"][d][metric] - raw["daily"][d][metric] for d in days]
            for block in (1, 7, 14):
                interval = block_mean_interval(days, values, block, resamples=resamples)
                comparisons.append({"horizon_hours": horizon, "metric": metric, **interval})
    for block in (1, 7, 14):
        for metric in ("brier", "log_loss"):
            entries = [r for r in comparisons if (r["block_days"], r["metric"]) == (block, metric)]
            adjusted = holm_adjust([r["one_sided_improvement_pvalue_approximate"] for r in entries])
            for row, value in zip(entries, adjusted, strict=True):
                row["holm_adjusted_pvalue_four_horizons"] = value
    return summaries, bins, comparisons


def main(wait_seconds=0):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    archive = Archive()
    try:
        protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
        details = json.loads(Path("config/e002_scoring.json").read_text())
        markets = read_rows("reports/E002_development_markets.jsonl")
        if any(
            not protocol["development_start"] <= m["day"] < protocol["validation_end_exclusive"]
            for m in markets
        ):
            raise ValueError("Development metadata contains a sealed/out-of-range date")
        # Preserve exact implementation bytes before the wait and before scores.
        source_ids = {}
        for path in (
            Path(__file__),
            Path("weatherpred/calibration.py"),
            Path("weatherpred/forecasts.py"),
            Path("weatherpred/historical_candles.py"),
            Path("config/e002_market_baseline.json"),
            Path("config/e002_scoring.json"),
        ):
            source_ids[str(path)] = archive.append(
                "research_source", str(path), utcnow(), {}, path.read_bytes()
            )
        archive.append(
            "experiment_protocol",
            "E002-scoring-v1",
            utcnow(),
            {"source_record_ids": source_ids},
            canonical(details).encode(),
        )
        acquisition = wait_for_acquisition({m["ticker"] for m in markets}, wait_seconds)
        quotes, statuses = build_quotes(archive, markets, acquisition, protocol, details)
        fit_ts = int(parse_time(details["fit_cutoff"]).timestamp())
        models, fit_audit = {}, []
        for horizon in protocol["decision_hours_before_period_end"]:
            train = [r for r in quotes if r["split"] == "train" and r["horizon_hours"] == horizon]
            require_training_rows(
                train, protocol["development_start"], protocol["train_end_exclusive"], fit_ts
            )
            models[horizon] = fit_logistic(
                [r["midpoint"] for r in train],
                [r["outcome"] for r in train],
                event_weights(train),
                penalty=protocol["logistic_l2_penalty"],
            )
            fit_audit.append(
                {
                    "horizon_hours": horizon,
                    "contracts_descriptive": len(train),
                    "events": len({r["event"] for r in train}),
                    "days": len({r["day"] for r in train}),
                    "latest_training_settlement_ts": max(r["settled_ts"] for r in train),
                    "model": asdict(models[horizon]),
                }
            )
        artifact = {
            "experiment": "E002-v1",
            "published_at": iso(utcnow()),
            "fit_cutoff": details["fit_cutoff"],
            "models": fit_audit,
            "source_record_ids": source_ids,
            "data_sha256": hashlib.sha256(canonical(quotes).encode()).hexdigest(),
            "promotion_eligible": False,
            "real_money_recommended_size": 0,
        }
        artifact_id = archive.append("model_artifact", "E002-v1", utcnow(), {}, canonical(artifact).encode())
        validation = [r for r in quotes if r["split"] == "validation"]
        scored = score_validation(validation, models)
        summaries, bins, comparisons = summarize(scored, 10_000)
        counts = Counter((r["split"], r["series"], r["horizon_hours"], r["status"]) for r in statuses)
        coverage = [
            {"split": s, "series": city, "horizon_hours": h, "status": status, "count": count}
            for (s, city, h, status), count in sorted(counts.items())
        ]
        # Sum diagnostics only when the entire original event membership is quoted.
        event_sizes = Counter(m["event"] for m in markets)
        sums = defaultdict(list)
        for r in scored:
            sums[(r["model"], r["horizon_hours"], r["event"])].append(r["probability"])
        coherence = [
            {
                "model": model,
                "horizon_hours": h,
                "event": event,
                "probability_sum": sum(p),
                "complete_event": len(p) == event_sizes[event],
            }
            for (model, h, event), p in sums.items()
        ]
        result = {
            "experiment": "E002-v1",
            "generated_at": iso(utcnow()),
            "model_artifact_record_id": artifact_id,
            "training": fit_audit,
            "coverage": coverage,
            "validation_scores": summaries,
            "calibration": bins,
            "paired_day_block_comparisons": comparisons,
            "development_contracts": len(markets),
            "quote_examples": len(quotes),
            "network_requests": 0,
            "holdout_accessed": False,
            "profitability_proven": False,
            "historical_pnl": None,
            "limitations": [
                "Retrospective metadata revisions/publication history not proven",
                "Conditional on observed exact two-sided quote coverage",
                "One seasonal validation quarter, circular-block stationarity assumption",
                "Four-horizon multiplicity adjustment is development-only, not global promotion",
                "Per-contract probability calibration does not enforce bracket coherence",
                "No depth, fill, contemporaneous fee, capital or execution inference",
            ],
        }
        archive.append("experiment_report", "E002_baselines", utcnow(), {}, canonical(result).encode())
        Path("reports/E002_baselines.json").write_text(json.dumps(result, indent=2))
        for name, rows in (
            ("quotes", quotes),
            ("scored", scored),
            ("coherence", coherence),
            ("coverage", statuses),
        ):
            body = "\n".join(canonical(r) for r in rows) + "\n"
            archive.append("experiment_dataset", "E002_" + name, utcnow(), {}, body.encode())
            Path(f"reports/E002_{name}.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "model_artifact_record_id": artifact_id,
                    "validation_scores": [{k: v for k, v in r.items() if k != "daily"} for r in summaries],
                    "holdout_accessed": False,
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
    if not 0 <= args.wait_seconds <= 7200:
        parser.error("wait-seconds must be in [0, 7200]")
    main(args.wait_seconds)
