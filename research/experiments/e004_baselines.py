"""Frozen E004 forecast diagnostics. Historical execution/P&L is prohibited.

Run once online to acquire validation candles, then reproduce using --offline.
All scores are exploratory: five dependent days and unverified publication lag.
"""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import numpy as np

from weatherpred.archive import Archive, canonical
from weatherpred.forecasts import IndexSeries, ResidualDistribution, candle_quote, fit_residuals
from weatherpred.http import PublicClient
from weatherpred.timeutil import iso, parse_time, utcnow


def timestamp_ms(value):
    return int(parse_time(value).timestamp() * 1000)


def read_id(archive, rec):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
    return archive.json(row)


def build_dataset(archive, acquisition, protocol):
    points = []
    for day in acquisition["days"]:
        points.extend(read_id(archive, day["record_id"])["timeseries"])
    series = IndexSeries(points)
    start, fit, end = (
        timestamp_ms(protocol[k])
        for k in ("training_start", "training_end_exclusive", "validation_end_exclusive")
    )
    markets = {}
    for rec in acquisition["market_record_ids"]:
        for m in read_id(archive, rec)["markets"]:
            if start <= timestamp_ms(m["close_time"]) < end:
                markets[m["ticker"]] = m
    events = defaultdict(list)
    audit, exclusions, mismatches = Counter(), Counter(), []
    for m in markets.values():
        s = timestamp_ms(m["close_time"])
        point = series.label(s)
        if not point:
            audit["missing_index_label"] += 1
            continue
        if Decimal(str(point["v"])) != Decimal(m["expiration_value"]):
            mismatches.append(
                {"ticker": m["ticker"], "index": point["v"], "expiration_value": m["expiration_value"]}
            )
            continue
        if m["strike_type"] != "greater" or m["result"] not in ("yes", "no"):
            raise ValueError("Unexpected Miami strike or unresolved label")
        yes = Decimal(str(point["v"])) > Decimal(str(m["floor_strike"]))
        if yes != (m["result"] == "yes"):
            raise ValueError("Index label disagrees with contract result")
        audit["matched_contract_value_and_result"] += 1
        events[s].append(m)
    if mismatches:
        raise ValueError("Settlement reconciliation requires investigation: " + canonical(mismatches[:10]))
    examples = []
    for s in range(start, end, 3_600_000):
        label = series.label(s)
        for horizon in protocol["decision_minutes_before_hour"]:
            decision = s - horizon * 60_000
            if not label:
                exclusions["missing_label"] += 1
                continue
            split = "train" if s < fit else "validation"
            if split == "validation" and decision < fit:
                exclusions["decision_precedes_fixed_fit_cutoff"] += 1
                continue
            features = series.features(
                decision,
                s,
                protocol["historical_publication_lag_minutes"] * 60_000,
                protocol["max_last_point_age_minutes"] * 60_000,
                protocol["trend_lookback_minutes"] * 60_000,
            )
            if not features:
                exclusions["missing_or_stale_features"] += 1
                continue
            examples.append(
                {
                    "settlement_ms": s,
                    "decision_ms": decision,
                    "horizon_minutes": horizon,
                    "split": split,
                    "features": features,
                    "observed": label["v"],
                    "label_point_ms": label["t"],
                    "day": datetime.fromtimestamp(s / 1000, UTC).date().isoformat(),
                }
            )
    return (
        examples,
        events,
        {
            "points": len(series.points),
            "listed_events": len(events),
            "contract_reconciliation": dict(audit),
            "exclusions": dict(exclusions),
        },
    )


def get_candles(archive, events, fit_ms, offline):
    cache, records, errors = {}, [], []
    client = PublicClient(archive)
    requests = 0
    try:
        for i, (s, markets) in enumerate(sorted(events.items())):
            if s < fit_ms:
                continue
            key = markets[0]["event_ticker"]
            record = archive.latest("e004_candles", key)
            if record is None:
                if offline:
                    raise ValueError("Missing archived validation candles for " + key)
                try:
                    data, rec = client.json(
                        "/markets/candlesticks",
                        {
                            "market_tickers": ",".join(sorted(m["ticker"] for m in markets)),
                            "start_ts": s // 1000 - 3600,
                            "end_ts": s // 1000,
                            "period_interval": 1,
                            "include_latest_before_start": "false",
                        },
                        kind="e004_candles",
                        key=key,
                    )
                    requests += 1
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    errors.append({"event": key, "error": str(exc)})
                    continue
            else:
                data, rec = archive.json(record), record["id"]
            received = {m["market_ticker"] for m in data["markets"]}
            if received != {m["ticker"] for m in markets}:
                raise ValueError("Candle response membership mismatch for " + key)
            for m in data["markets"]:
                cache[m["market_ticker"]] = m["candlesticks"]
            records.append(rec)
            if i % 20 == 0:
                print(f"Candle acquisition: {len(records)} events archived", flush=True)
    finally:
        client.close()
    return cache, records, errors, requests


def binary_scores(probability, outcome):
    p = np.clip(probability, 1e-6, 1 - 1e-6)
    return {
        "brier": float((probability - outcome) ** 2),
        "log_loss": float(-outcome * np.log(p) - (1 - outcome) * np.log(1 - p)),
    }


def summarize_scores(rows):
    # Every model uses identical quotes, then averages within the event first.
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["horizon_minutes"], r["event"], r["day"])].append(r)
    event_rows = []
    for (model, horizon, event, day), group in groups.items():
        event_rows.append(
            {
                "model": model,
                "horizon_minutes": horizon,
                "event": event,
                "day": day,
                "quoted_contracts": len(group),
                **{k: float(np.mean([r[k] for r in group])) for k in ("brier", "log_loss")},
            }
        )
    summaries = []
    for model, horizon in sorted({(r["model"], r["horizon_minutes"]) for r in event_rows}):
        group = [r for r in event_rows if (r["model"], r["horizon_minutes"]) == (model, horizon)]
        days = sorted({r["day"] for r in group})
        summaries.append(
            {
                "model": model,
                "horizon_minutes": horizon,
                "events": len(group),
                "independent_days_at_most": len(days),
                **{k: float(np.mean([r[k] for r in group])) for k in ("brier", "log_loss")},
                "daily_brier": {
                    d: float(np.mean([r["brier"] for r in group if r["day"] == d])) for d in days
                },
            }
        )
    return summaries, event_rows


def main(offline=False):
    archive = Archive()
    protocol = json.loads(Path("config/e004_index_baselines.json").read_text())
    acquisition = json.loads(Path("reports/E004_acquisition.json").read_text())
    examples, events, audit = build_dataset(archive, acquisition, protocol)
    fit_ms = timestamp_ms(protocol["training_end_exclusive"])
    train = [r for r in examples if r["split"] == "train"]
    validation = [r for r in examples if r["split"] == "validation"]
    models, fitted = {}, {}
    for horizon in protocol["decision_minutes_before_hour"]:
        for name in protocol["models"]:
            base, kind = name.split("_")
            residuals = fit_residuals(train, base, horizon, fit_ms)
            dist = ResidualDistribution(residuals, kind)
            models[(name, horizon)] = dist
            fitted[f"{name}:{horizon}"] = {
                "base": base,
                "kind": kind,
                "residuals": residuals,
                "n": len(residuals),
                "bias": dist.bias,
                "sigma": dist.sigma,
            }
    # Publish fitted artifacts BEFORE examining validation forecast scores.
    code = {
        str(p): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (Path(__file__), Path("weatherpred/forecasts.py"), Path("weatherpred/index.py"))
    }
    artifact = {
        "experiment": "E004-v1",
        "published_at": iso(utcnow()),
        "fit_cutoff_ms": fit_ms,
        "acquisition": acquisition,
        "protocol": protocol,
        "models": fitted,
        "code_sha256": code,
        "real_money_size": 0,
        "promotion_eligible": False,
        "limitations": [
            "Historical feature availability/revision provenance is unverified",
            "Only 12 training days; exploratory distributional baselines",
        ],
    }
    artifact_id = archive.append("model_artifact", "E004-v1", utcnow(), {}, canonical(artifact).encode())
    Path("reports/E004_model.json").write_text(json.dumps(artifact, indent=2))
    candles, records, errors, requests = get_candles(archive, events, fit_ms, offline)
    binary, continuous, coverage = [], [], Counter()
    for row in validation:
        horizon, s = row["horizon_minutes"], row["settlement_ms"]
        common = {k: row[k] for k in ("horizon_minutes", "day", "settlement_ms", "decision_ms")}
        for name in protocol["models"]:
            dist = models[(name, horizon)]
            base = row["features"][name.split("_")[0]]
            mean = base + dist.bias
            continuous.append(
                {
                    **common,
                    "model": name,
                    "crps": dist.crps(base, row["observed"]),
                    "absolute_error": abs(mean - row["observed"]),
                    "squared_error": (mean - row["observed"]) ** 2,
                    **{
                        f"coverage_{int(c * 100)}": float(
                            dist.interval(base, c)[0] <= row["observed"] <= dist.interval(base, c)[1]
                        )
                        for c in (0.8, 0.95)
                    },
                }
            )
        markets = events.get(s, [])
        if not markets:
            coverage["validation_forecasts_without_listed_event"] += 1
        for market in markets:
            coverage["candidate_contract_horizons"] += 1
            if timestamp_ms(market["open_time"]) > row["decision_ms"]:
                coverage["market_not_open"] += 1
                continue
            quote = candle_quote(candles.get(market["ticker"], []), row["decision_ms"] // 1000)
            if quote is None:
                coverage["absent_stale_or_not_two_sided_quote"] += 1
                continue
            coverage["paired_contract_horizons"] += 1
            outcome = int(market["result"] == "yes")
            forecasts = {"market_" + k: quote[k] for k in ("bid", "ask", "midpoint")}
            for name in protocol["models"]:
                forecasts[name] = models[(name, horizon)].probability(
                    row["features"][name.split("_")[0]], market
                )
            for name, probability in forecasts.items():
                binary.append(
                    {
                        **common,
                        "model": name,
                        "event": market["event_ticker"],
                        "ticker": market["ticker"],
                        "probability": probability,
                        "outcome": outcome,
                        **binary_scores(probability, outcome),
                    }
                )
    summary, event_rows = summarize_scores(binary)
    continuous_summary = []
    for name, horizon in sorted(models):
        group = [r for r in continuous if (r["model"], r["horizon_minutes"]) == (name, horizon)]
        continuous_summary.append(
            {
                "model": name,
                "horizon_minutes": horizon,
                "events": len(group),
                **{
                    k: float(np.mean([r[k] for r in group]))
                    for k in ("crps", "absolute_error", "squared_error", "coverage_80", "coverage_95")
                },
            }
        )
    report = {
        "experiment": "E004-v1",
        "generated_at": iso(utcnow()),
        "model_artifact_record_id": artifact_id,
        "acquisition": acquisition,
        "candle_record_ids": records,
        "network_requests": requests,
        "training_examples": len(train),
        "validation_examples": len(validation),
        "audit": audit,
        "quote_coverage": dict(coverage),
        "errors": errors,
        "binary_scores_paired_event_averaged": summary,
        "continuous_scores_all_validation": continuous_summary,
        "profitability_proven": False,
        "historical_pnl": None,
        "real_money_recommended_size": 0,
        "limitations": [
            "Five validation days only; no independent profitability inference",
            "10-minute historical publication lag is an unverified sensitivity assumption",
            "First-publication/revision history remains unproven despite matching settlements",
            "Quote candles provide no depth, fill, or queue evidence",
            "Binary scores condition on available two-sided quotes; missing quotes explicitly counted",
            "Actual model publication is today, not the retrospective fit cutoff",
        ],
    }
    archive.append("experiment_report", "E004_baselines", utcnow(), {}, canonical(report).encode())
    Path("reports/E004_baselines.json").write_text(json.dumps(report, indent=2))
    for name, rows in (
        ("binary", binary),
        ("continuous", continuous),
        ("events", event_rows),
        ("examples", examples),
    ):
        body = "\n".join(canonical(r) for r in rows) + "\n"
        archive.append("experiment_dataset", "E004_" + name, utcnow(), {}, body.encode())
        Path(f"reports/E004_{name}.jsonl").write_text(body)
    archive.close()
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "training_examples",
                    "validation_examples",
                    "audit",
                    "quote_coverage",
                    "errors",
                    "network_requests",
                    "profitability_proven",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true")
    main(parser.parse_args().offline)
