"""Chronological weather/market combinations; complete brackets and no fills."""

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import numpy as np

from research.experiments.e002_baselines import build_quotes, read_rows
from research.experiments.e003_daily_baselines import build_weather_rows, summaries_and_intervals
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import binary_metrics
from weatherpred.contracts import payout_bounds
from weatherpred.daily_forecasts import fit_daily_models, predict_model
from weatherpred.forecast_pool import ForecastPool, chronological_prefix, fit_pool
from weatherpred.timeutil import iso, parse_time, utcnow


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def by_id(archive, record_id):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if row is None:
        raise ValueError("Missing pinned source record")
    return archive.json(row)


def inputs(archive, protocol):
    artifact = by_id(archive, protocol["weather_model_record_id"])
    labels = by_source_lines(archive, artifact["source_record_ids"]["reports/E003_NWS_labels.jsonl"])
    stations = json.loads(Path("config/e003_nbm_acquisition.json").read_text())["stations"]
    windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
    rows, _ = build_weather_rows(archive, labels, artifact["protocol"], stations, windows)
    train = [r for r in rows if r["split"] == "train"]
    if digest(train) != artifact["training_data_sha256"]:
        raise ValueError("Weather training inputs differ from pinned E003 artifact")
    markets = read_rows("reports/E002_development_markets.jsonl")
    quotes, _ = build_quotes(
        archive,
        markets,
        json.loads(Path("reports/E002_acquisition.json").read_text()),
        json.loads(Path("config/e002_market_baseline.json").read_text()),
        json.loads(Path("config/e002_scoring.json").read_text()),
    )
    return artifact, rows, markets, quotes


def by_source_lines(archive, record_id):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    return [json.loads(line) for line in archive.body(row).decode().splitlines()]


def complete_events(weather_rows, markets, quotes, protocol):
    weather = {r["event"]: r for r in weather_rows}
    members, grouped = defaultdict(list), defaultdict(list)
    for market in markets:
        if market["event"] in weather:
            members[market["event"]].append(market)
    for quote in quotes:
        grouped[quote["event"], quote["horizon_hours"]].append(quote)
    result, coverage = [], []
    for event, row in sorted(weather.items()):
        contracts = sorted(members[event], key=lambda m: m["ticker"])
        if (
            len(contracts) != 6
            or not payout_bounds([dict(m, event_ticker=event) for m in contracts], integer_domain=True)[
                "exhaustive_exclusive"
            ]
        ):
            raise ValueError("Event does not have six exhaustive/exclusive contracts")
        for horizon in protocol["horizons_hours"]:
            group = sorted(grouped[event, horizon], key=lambda q: q["ticker"])
            status = {k: row[k] for k in ("event", "day", "split", "series")}
            status["horizon_hours"] = horizon
            eligible = [q["ticker"] for q in group] == [m["ticker"] for m in contracts]
            coverage.append(dict(status, status="eligible" if eligible else "incomplete_quote_partition"))
            if not eligible:
                continue
            if len({q["decision_ts"] for q in group}) != 1 or sum(q["outcome"] for q in group) != 1:
                raise ValueError("Mixed decision timestamps or noncategorical target")
            if row["split"] == "train" and row["day"] < protocol["combiner_training_start"]:
                continue
            raw = np.asarray([q["midpoint"] for q in group])
            result.append(
                {
                    **status,
                    "decision_ts": group[0]["decision_ts"],
                    "contracts": contracts,
                    "quote_source_ids": [q["candle_record_id"] for q in group],
                    "raw_midpoints": raw.tolist(),
                    "market_probabilities": (raw / raw.sum()).tolist(),
                    "winner": next(i for i, q in enumerate(group) if q["outcome"] == 1),
                    "weather_row": row,
                }
            )
    return result, coverage


def cross_fit(archive, rows, events, base_protocol, protocol):
    train = [r for r in rows if r["split"] == "train"]
    folds, predictions = [], {}
    for month in range(2, 7):
        start, end = f"2025-{month:02}-01", f"2025-{month + 1:02}-01"
        cutoff = int(parse_time(start + "T00:00:00Z").timestamp())
        prefix = chronological_prefix(train, start, cutoff)
        config = dict(base_protocol, training_end_exclusive=start, fit_cutoff=start + "T00:00:00Z")
        models, audit = fit_daily_models(prefix, config)
        artifact = {
            "month": start,
            "fit_cutoff": config["fit_cutoff"],
            "training_data_sha256": digest(prefix),
            "training_events": len(prefix),
            "latest_training_day": max(r["day"] for r in prefix),
            "latest_training_settlement": max(r["settled_ts"] for r in prefix),
            "latest_training_nws_issue": max(r["nws_issue_ts"] for r in prefix),
            "models": models,
            "fit_audit": audit,
            "retrospective_cross_fit": True,
        }
        record_id = archive.append(
            "model_artifact", "E007_fold_" + start, utcnow(), {}, canonical(artifact).encode()
        )
        folds.append({**artifact, "record_id": record_id})
        for event in events:
            if event["split"] != "train" or not start <= event["day"] < end:
                continue
            if event["decision_ts"] < cutoff:
                raise ValueError("Out-of-fit forecast decision precedes its simulated fit")
            event_key = event["event"]
            if event_key in predictions:
                continue
            predictions[event_key] = {
                name: [
                    predict_model(models, name, event["weather_row"]).probability(m)
                    for m in event["contracts"]
                ]
                for name in protocol["weather_models"]
            }
    train_events = {r["event"] for r in events if r["split"] == "train"}
    if set(predictions) != train_events:
        raise ValueError("Incomplete chronological training prediction coverage")
    return folds, predictions


def fit_combiners(events, probabilities, protocol):
    fits, coverage = {}, []
    for horizon in protocol["horizons_hours"]:
        group = [r for r in events if r["split"] == "train" and r["horizon_hours"] == horizon]
        counts = Counter(r["day"] for r in group)
        status = {"horizon_hours": horizon, "training_events": len(group), "training_days": len(counts)}
        if len(counts) < protocol["minimum_combiner_training_event_days"]:
            coverage.append(dict(status, status="insufficient_training_days"))
            continue
        coverage.append(dict(status, status="fitted"))
        q = [r["market_probabilities"] for r in group]
        y = [r["winner"] for r in group]
        weights = [1 / counts[r["day"]] for r in group]
        fits[str(horizon)] = {}
        for name in ["temperature_market", *protocol["weather_models"]]:
            weather_name = protocol["weather_models"][0] if name == "temperature_market" else name
            p = [probabilities[r["event"]][weather_name] for r in group]
            model, objective = fit_pool(q, p, y, weights, market_only=name == "temperature_market")
            fits[str(horizon)][name] = {"model": asdict(model), "objective": objective}
    return fits, coverage


def validation_scores(events, fits, weather_models, protocol):
    scored, categorical, partitions = [], [], []
    for horizon in protocol["horizons_hours"]:
        if str(horizon) not in fits:
            continue
        group = [r for r in events if r["split"] == "validation" and r["horizon_hours"] == horizon]
        days = Counter(r["day"] for r in group)
        for row in group:
            q = row["market_probabilities"]
            weather = {
                name: [
                    predict_model(weather_models, name, row["weather_row"]).probability(m)
                    for m in row["contracts"]
                ]
                for name in protocol["weather_models"]
            }
            probabilities = {"raw_market": row["raw_midpoints"], "normalized_market": q}
            for name, fitted in fits[str(horizon)].items():
                weather_name = protocol["weather_models"][0] if name == "temperature_market" else name
                probabilities[name] = ForecastPool(**fitted["model"]).predict([q], [weather[weather_name]])[0]
            y = np.zeros(len(q), dtype=int)
            y[row["winner"]] = 1
            for name, p in probabilities.items():
                context = {k: row[k] for k in ("event", "day", "series", "horizon_hours")}
                if name != "raw_market":
                    if abs(sum(p) - 1) > 1e-10:
                        raise ValueError("Pool prediction does not sum to one")
                    partitions.append({**context, "model": name, "sum": float(sum(p))})
                    categorical.append(
                        {
                            **context,
                            "model": name,
                            "log_loss": float(-np.log(max(p[row["winner"]], 1e-6))),
                            "weight": 1 / days[row["day"]],
                        }
                    )
                scores = binary_metrics(p, y)
                for i, contract in enumerate(row["contracts"]):
                    scored.append(
                        {
                            **context,
                            "model": name,
                            "ticker": contract["ticker"],
                            "probability": float(p[i]),
                            "outcome": int(y[i]),
                            "weight": 1 / len(q) / days[row["day"]],
                            **{metric: float(values[i]) for metric, values in scores.items()},
                        }
                    )
    return scored, categorical, partitions


def comparison_report(scored, protocol):
    names = protocol["weather_models"]
    summaries, _, reliability = summaries_and_intervals(scored, names)
    comparisons = []
    for reference in ("raw_market", "normalized_market", "temperature_market"):
        subset = [
            dict(r, model="raw_market" if r["model"] == reference else r["model"])
            for r in scored
            if r["model"] in {*names, reference}
        ]
        _, intervals, _ = summaries_and_intervals(subset, names)
        for row in intervals:
            row.pop("holm_adjusted_pvalue_weather_models_and_horizons", None)
            comparisons.append(dict(row, reference=reference))
    # Coverage currently omits registered horizons. Do not shrink that family.
    return summaries, comparisons, reliability


def main():
    archive = Archive()
    try:
        protocol = json.loads(Path("config/e007_market_weather_pool.json").read_text())
        paths = [
            Path(__file__),
            Path("config/e007_market_weather_pool.json"),
            Path("weatherpred/forecast_pool.py"),
            Path("weatherpred/daily_forecasts.py"),
            Path("weatherpred/calibration.py"),
            Path("research/experiments/e003_daily_baselines.py"),
            Path("research/experiments/e002_baselines.py"),
        ]
        sources = {
            str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes()) for p in paths
        }
        protocol_id = archive.append(
            "experiment_protocol",
            protocol["experiment"],
            utcnow(),
            {},
            canonical({"protocol": protocol, "source_record_ids": sources}).encode(),
        )
        base, weather_rows, markets, quotes = inputs(archive, protocol)
        events, coverage = complete_events(weather_rows, markets, quotes, protocol)
        folds, out_of_fit = cross_fit(archive, weather_rows, events, base["protocol"], protocol)
        fits, fit_coverage = fit_combiners(events, out_of_fit, protocol)
        artifact = {
            "published_at": iso(utcnow()),
            "protocol": protocol,
            "protocol_record_id": protocol_id,
            "source_record_ids": sources,
            "folds": folds,
            "fits": fits,
            "fit_coverage": fit_coverage,
            "training_events_sha256": digest([r for r in events if r["split"] == "train"]),
            "out_of_fit_probabilities_sha256": digest(out_of_fit),
            "promotion_eligible": False,
        }
        model_id = archive.append(
            "model_artifact", protocol["experiment"], utcnow(), {}, canonical(artifact).encode()
        )
        Path("reports/E007_model.json").write_text(json.dumps(dict(artifact, record_id=model_id), indent=2))
        print(
            json.dumps({"model_frozen_before_validation_scores": model_id, "fit_coverage": fit_coverage}),
            flush=True,
        )
        scored, categorical, partitions = validation_scores(events, fits, base["models"], protocol)
        summaries, comparisons, reliability = comparison_report(scored, protocol)
        multiclass = []
        for name in sorted({r["model"] for r in categorical}):
            for horizon in protocol["horizons_hours"]:
                group = [r for r in categorical if r["model"] == name and r["horizon_hours"] == horizon]
                if group:
                    multiclass.append(
                        {
                            "model": name,
                            "horizon_hours": horizon,
                            "log_loss": float(
                                np.average(
                                    [r["log_loss"] for r in group], weights=[r["weight"] for r in group]
                                )
                            ),
                        }
                    )
        result = {
            "generated_at": iso(utcnow()),
            "model_record_id": model_id,
            "fit_coverage": fit_coverage,
            "validation_scores": summaries,
            "paired_day_block_comparisons": comparisons,
            "multiclass_log_loss": multiclass,
            "calibration": reliability,
            "maximum_partition_error": max(abs(r["sum"] - 1) for r in partitions),
            "uncertainty_limit": "No reduced-family multiple-comparison claim; registered horizons are missing and evaluated validation days have gaps",
            "historical_pnl": None,
            "holdout_accessed": False,
            "profitability_proven": False,
            "network_requests": 0,
            "limitations": [
                protocol["status"],
                protocol["source_limits"],
                protocol["quote_eligibility"],
                "Weights are forecasts, not investments; no fees, depth, latency, fills or bankroll inference",
            ],
        }
        archive.append(
            "experiment_report", "E007_market_weather_pool", utcnow(), {}, canonical(result).encode()
        )
        Path("reports/E007_market_weather_pool.json").write_text(json.dumps(result, indent=2))
        for name, rows in (
            ("scored", scored),
            ("coverage", coverage),
            ("categorical", categorical),
            ("partitions", partitions),
        ):
            body = "\n".join(canonical(r) for r in rows) + "\n"
            archive.append("research_dataset", "E007_" + name, utcnow(), {}, body.encode())
            Path(f"reports/E007_{name}.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "validation_scores": [{k: v for k, v in r.items() if k != "daily"} for r in summaries],
                    "multiclass_log_loss": multiclass,
                    "profitability_proven": False,
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
