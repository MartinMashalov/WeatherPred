"""Chronological NBM distribution benchmarks; final holdout and trading P&L excluded."""

import argparse
import hashlib
import json
import logging
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from research.experiments.e002_baselines import build_quotes, event_weights, read_rows
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import LogisticCalibration, binary_metrics, block_mean_interval, holm_adjust
from weatherpred.contracts import payout_bounds
from weatherpred.daily_forecasts import daily_features, fit_daily_models, predict_model
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def build_weather_rows(archive, labels, protocol, stations, windows):
    rows, statuses, cache = [], [], {}
    cutoff = int(parse_time(protocol["fit_cutoff"]).timestamp())
    for label in labels:
        if not protocol["training_start"] <= label["day"] < protocol["validation_end_exclusive"]:
            raise ValueError("Sealed/out-of-range date reached daily weather dataset")
        expected_split = "train" if label["day"] < protocol["training_end_exclusive"] else "validation"
        if label["split"] != expected_split:
            raise ValueError("Derived label split differs from date protocol")
        status = {k: label[k] for k in ("event", "series", "day", "split")}
        if label["status"] != "match":
            statuses.append({**status, "reason": "nws_" + label["status"]})
            continue
        settled = int(parse_time(label["settled_at"]).timestamp())
        nws_issue = int(parse_time(label["selected_report"]["issued_at"]).timestamp())
        if label["split"] == "train" and max(settled, nws_issue) >= cutoff:
            statuses.append({**status, "reason": "training_label_unavailable_before_fit"})
            continue
        day = label["day"]
        if day not in cache:
            record = archive.latest("e003_nbm_day", day)
            cache[day] = (archive.json(record), record["id"]) if record else (None, None)
        source, rec = cache[day]
        if source is None:
            statuses.append({**status, "reason": "missing_nbm_day"})
            continue
        station = stations[label["series"]]
        try:
            if source["cards"][station]["station"] != station or source["day"] != day:
                raise ValueError("NBM station/date lineage differs from requested target")
            feature = daily_features(
                day,
                windows[label["series"]]["standard_utc_offset_hours"],
                source["cards"][station],
                source["object"]["last_modified"],
            )
        except (KeyError, ValueError) as exc:
            statuses.append({**status, "reason": "invalid_nbm_features", "detail": str(exc)})
            continue
        rows.append(
            {
                **status,
                "observed_f": label["exchange_value_f"],
                "settled_ts": settled,
                "nws_issue_ts": nws_issue,
                "nws_report": label["selected_report"],
                "nbm_record_id": rec,
                "features": feature,
            }
        )
        statuses.append({**status, "reason": "eligible"})
    return rows, statuses


def wait_for_sources(seconds):
    deadline = time.monotonic() + seconds
    while True:
        reports = []
        for path in ("reports/E002_acquisition.json", "reports/E003_NBM_acquisition.json"):
            try:
                reports.append(json.loads(Path(path).read_text()))
            except (FileNotFoundError, json.JSONDecodeError):
                reports.append({})
        if (
            reports[0].get("stop_reason") == "all_development_markets_attempted"
            and reports[1].get("stop_reason") == "all_development_days_attempted"
        ):
            if any(r.get("errors") for r in reports):
                raise ValueError("Source acquisition has unresolved request errors; reconcile before fitting")
            return reports
        if time.monotonic() >= deadline or Path("data/STOP_E003_ANALYSIS").exists():
            raise ValueError("Daily sources incomplete; no models fitted or errors scored")
        time.sleep(min(5, max(0, deadline - time.monotonic())))


def summaries_and_intervals(scored, names):
    groups, bins = defaultdict(list), defaultdict(list)
    for row in scored:
        groups[(row["model"], row["horizon_hours"])].append(row)
        bins[(row["model"], row["horizon_hours"], min(9, int(row["probability"] * 10)))].append(row)
    summaries, comparisons, reliability = [], [], []
    for (name, horizon), rows in sorted(groups.items()):
        daily = defaultdict(list)
        for row in rows:
            daily[row["day"]].append(row)
        summary = {
            "model": name,
            "horizon_hours": horizon,
            "days": len(daily),
            "events": len({r["event"] for r in rows}),
            "contracts_descriptive": len(rows),
            "daily": {
                day: {
                    metric: float(np.average([r[metric] for r in rs], weights=[r["weight"] for r in rs]))
                    for metric in ("brier", "log_loss")
                }
                for day, rs in sorted(daily.items())
            },
        }
        summary.update(
            {
                metric: float(np.mean([r[metric] for r in summary["daily"].values()]))
                for metric in ("brier", "log_loss")
            }
        )
        summaries.append(summary)
    lookup = {(r["model"], r["horizon_hours"]): r for r in summaries}
    for horizon in sorted({r["horizon_hours"] for r in summaries}):
        market = lookup[("raw_market", horizon)]
        days = sorted(market["daily"])
        for name in names:
            model = lookup[(name, horizon)]
            if sorted(model["daily"]) != days:
                raise ValueError("Models have different validation day coverage")
            for metric in ("brier", "log_loss"):
                differences = [model["daily"][d][metric] - market["daily"][d][metric] for d in days]
                for block in (1, 7, 14):
                    try:
                        interval = block_mean_interval(days, differences, block)
                    except ValueError as exc:
                        interval = {
                            "block_days": block,
                            "days": len(days),
                            "mean_difference": float(np.mean(differences)),
                            "confidence_unavailable": str(exc),
                        }
                    comparisons.append(
                        {"model": name, "horizon_hours": horizon, "metric": metric, **interval}
                    )
    for block in (1, 7, 14):
        for metric in ("brier", "log_loss"):
            group = [r for r in comparisons if r["block_days"] == block and r["metric"] == metric]
            if all("one_sided_improvement_pvalue_approximate" in r for r in group):
                adjusted = holm_adjust([r["one_sided_improvement_pvalue_approximate"] for r in group])
                for r, p in zip(group, adjusted, strict=True):
                    r["holm_adjusted_pvalue_weather_models_and_horizons"] = p
            # Do not shrink the registered multiplicity family when confidence is missing.
    for (name, horizon, bin_id), rows in sorted(bins.items()):
        weights = [r["weight"] for r in rows]
        reliability.append(
            {
                "model": name,
                "horizon_hours": horizon,
                "bin": bin_id,
                "weight": sum(weights),
                "contracts_descriptive": len(rows),
                "mean_probability": float(np.average([r["probability"] for r in rows], weights=weights)),
                "observed_frequency": float(np.average([r["outcome"] for r in rows], weights=weights)),
            }
        )
    return summaries, comparisons, reliability


def main(wait_seconds):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    archive = Archive()
    try:
        protocol = json.loads(Path("config/e003_daily_models.json").read_text())
        source_ids = {}
        for path in (
            Path(__file__),
            Path("weatherpred/daily_forecasts.py"),
            Path("config/e003_daily_models.json"),
            Path("weatherpred/calibration.py"),
            Path("weatherpred/historical_candles.py"),
            Path("research/experiments/e002_baselines.py"),
            Path("reports/E003_NWS_labels.jsonl"),
        ):
            source_ids[str(path)] = archive.append(
                "research_source", str(path), utcnow(), {}, path.read_bytes()
            )
        protocol_id = archive.append(
            "experiment_protocol",
            protocol["experiment"],
            utcnow(),
            {},
            canonical({"protocol": protocol, "source_record_ids": source_ids}).encode(),
        )
        LOG.info("Registered daily forecast scorer=%s before fitting or validation scores", protocol_id)
        acquisition, nbm_acquisition = wait_for_sources(wait_seconds)
        if len(nbm_acquisition["days"]) != 273 or len({r["day"] for r in nbm_acquisition["days"]}) != 273:
            raise ValueError("NBM acquisition is not a complete development-day census")
        labels = read_rows("reports/E003_NWS_labels.jsonl")
        stations = json.loads(Path("config/e003_nbm_acquisition.json").read_text())["stations"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        weather_rows, weather_status = build_weather_rows(archive, labels, protocol, stations, windows)
        train = [r for r in weather_rows if r["split"] == "train"]
        models, fit_audit = fit_daily_models(train, protocol)
        artifact = {
            "experiment": protocol["experiment"],
            "published_at": iso(utcnow()),
            "protocol": protocol,
            "protocol_record_id": protocol_id,
            "source_record_ids": source_ids,
            "models": models,
            "fit_audit": fit_audit,
            "training_events": len(train),
            "training_days": len({r["day"] for r in train}),
            "training_data_sha256": hashlib.sha256(canonical(train).encode()).hexdigest(),
            "promotion_eligible": False,
            "real_money_size": 0,
        }
        model_id = archive.append(
            "model_artifact", protocol["experiment"], utcnow(), {}, canonical(artifact).encode()
        )
        Path("reports/E003_daily_model.json").write_text(
            json.dumps({**artifact, "record_id": model_id}, indent=2)
        )
        LOG.info(
            "Frozen fitted weather model=%s training_events=%s; beginning validation", model_id, len(train)
        )
        markets = read_rows("reports/E002_development_markets.jsonl")
        if {m["ticker"] for m in markets} != {r["ticker"] for r in acquisition["rows"]}:
            raise ValueError("Daily quote acquisition membership mismatch")
        e002_protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
        e002_details = json.loads(Path("config/e002_scoring.json").read_text())
        quotes, quote_status = build_quotes(archive, markets, acquisition, e002_protocol, e002_details)
        e002_model_row = archive.latest("model_artifact", "E002-v1")
        if e002_model_row is None:
            raise ValueError("Frozen market calibration artifact unavailable")
        logistic = {
            r["horizon_hours"]: LogisticCalibration(**r["model"])
            for r in archive.json(e002_model_row)["models"]
        }
        weather = {r["event"]: r for r in weather_rows if r["split"] == "validation"}
        events = defaultdict(list)
        for m in markets:
            if m["event"] in weather:
                events[m["event"]].append(m)
        probabilities, coherence, continuous = {}, [], []
        for event, row in weather.items():
            contracts = events[event]
            if not payout_bounds([dict(m, event_ticker=event) for m in contracts], integer_domain=True)[
                "exhaustive_exclusive"
            ]:
                raise ValueError("Daily market brackets do not partition the integer target")
            for name in protocol["models"]:
                distribution = predict_model(models, name, row)
                p = {m["ticker"]: distribution.probability(m) for m in contracts}
                if abs(sum(p.values()) - 1) > 1e-10:
                    raise ValueError("Forecast probabilities fail full-bracket coherence")
                probabilities[(event, name)] = p
                coherence.append(
                    {"event": event, "model": name, "probability_sum": sum(p.values()), "contracts": len(p)}
                )
                intervals = {str(level): distribution.interval(level) for level in (0.8, 0.95)}
                continuous.append(
                    {
                        "event": event,
                        "day": row["day"],
                        "series": row["series"],
                        "model": name,
                        "observed_f": row["observed_f"],
                        "mean_f": distribution.mean,
                        "error_f": distribution.mean - row["observed_f"],
                        "intervals": intervals,
                        "coverage": {
                            level: lo <= row["observed_f"] <= hi for level, (lo, hi) in intervals.items()
                        },
                    }
                )
        paired = [r for r in quotes if r["split"] == "validation" and r["event"] in weather]
        scored = []
        for horizon in e002_protocol["decision_hours_before_period_end"]:
            group = [r for r in paired if r["horizon_hours"] == horizon]
            weights = event_weights(group)
            for quote, weight in zip(group, weights, strict=True):
                p = {
                    name: probabilities[(quote["event"], name)][quote["ticker"]]
                    for name in protocol["models"]
                }
                p.update(
                    raw_market=quote["midpoint"],
                    logistic_market=float(logistic[horizon].predict([quote["midpoint"]])[0]),
                )
                for name, probability in p.items():
                    scores = binary_metrics([probability], [quote["outcome"]])
                    scored.append(
                        {
                            "event": quote["event"],
                            "ticker": quote["ticker"],
                            "series": quote["series"],
                            "day": quote["day"],
                            "horizon_hours": horizon,
                            "model": name,
                            "probability": probability,
                            "outcome": quote["outcome"],
                            "weight": weight,
                            **{k: float(v[0]) for k, v in scores.items()},
                        }
                    )
        summaries, comparisons, reliability = summaries_and_intervals(scored, protocol["models"])
        continuous_summary = []
        for name in protocol["models"]:
            group = [r for r in continuous if r["model"] == name]
            daily_sizes = Counter(r["day"] for r in group)
            weights = [1 / daily_sizes[r["day"]] for r in group]
            continuous_summary.append(
                {
                    "model": name,
                    "events": len(group),
                    "days": len(daily_sizes),
                    "mae_f": float(np.average([abs(r["error_f"]) for r in group], weights=weights)),
                    "rmse_f": float(np.sqrt(np.average([r["error_f"] ** 2 for r in group], weights=weights))),
                    "coverage": {
                        level: float(np.average([r["coverage"][level] for r in group], weights=weights))
                        for level in ("0.8", "0.95")
                    },
                }
            )
        result = {
            "generated_at": iso(utcnow()),
            "model_artifact_record_id": model_id,
            "market_model_record_id": e002_model_row["id"],
            "training_events": len(train),
            "weather_event_status_counts": dict(Counter(r["reason"] for r in weather_status)),
            "paired_quote_examples": len(paired),
            "validation_scores": summaries,
            "paired_day_block_comparisons": comparisons,
            "calibration": reliability,
            "continuous_scores": continuous_summary,
            "coherence_events_models": len(coherence),
            "maximum_partition_error": max(abs(r["probability_sum"] - 1) for r in coherence),
            "network_requests": 0,
            "holdout_accessed": False,
            "profitability_proven": False,
            "historical_pnl": None,
            "limitations": [
                protocol["availability_limit"],
                protocol["native_proxy"],
                "Forecasts are held fixed from day start; later lead times do not incorporate intraday observations",
                "Source reconciliation and quote eligibility condition the evaluated sample; missing cases retained",
                "No fill/depth/fee/capital inference, final holdout or prospective weather-model validation",
            ],
        }
        archive.append("experiment_report", "E003_daily_baselines", utcnow(), {}, canonical(result).encode())
        Path("reports/E003_daily_baselines.json").write_text(json.dumps(result, indent=2))
        for name, rows in (
            ("weather_rows", weather_rows),
            ("weather_coverage", weather_status),
            ("quote_coverage", quote_status),
            ("scored", scored),
            ("coherence", coherence),
            ("continuous", continuous),
        ):
            body = "\n".join(canonical(r) for r in rows) + "\n"
            archive.append("research_dataset", "E003_" + name, utcnow(), {}, body.encode())
            Path(f"reports/E003_{name}.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "model_artifact_record_id": model_id,
                    "validation_scores": [{k: v for k, v in r.items() if k != "daily"} for r in summaries],
                    "continuous_scores": continuous_summary,
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
    if not 0 <= args.wait_seconds <= 1800:
        parser.error("wait-seconds must be in [0, 1800]")
    main(args.wait_seconds)
