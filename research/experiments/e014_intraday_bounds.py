"""Test observed-high constraints as trades instead of an overall forecast contest."""

import hashlib
import itertools
import json
import math
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from research.experiments.e013_autoresearch import (
    load_inputs,
    simultaneous_diagnostics,
    summarize,
    timestamp,
)
from weatherpred.archive import Archive, canonical
from weatherpred.intraday_bounds import below_observed_bound, conservative_bound_probability, partial_high
from weatherpred.timeutil import iso, parse_time, utcnow
from weatherpred.trading_research import conditional_trade, order_cash, portfolio, valid_quote


def main():
    archive = Archive()
    try:
        config = json.loads(Path("config/e014_intraday_bounds.json").read_text())
        execution = json.loads(Path("config/e013_autoresearch.json").read_text())
        products = json.loads(Path("config/e003_nws_labels.json").read_text())["products"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        paths = [
            Path(__file__),
            Path("config/e014_intraday_bounds.json"),
            Path("weatherpred/intraday_bounds.py"),
            Path("weatherpred/nws_climate.py"),
            Path("weatherpred/trading_research.py"),
            Path("research/experiments/e013_autoresearch.py"),
            Path("config/e013_autoresearch.json"),
        ]
        source_ids = {
            str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes()) for p in paths
        }
        cutoff = utcnow()
        registration = archive.append(
            "experiment_protocol",
            config["experiment"],
            cutoff,
            {},
            canonical({"config": config, "source_record_ids": source_ids}).encode(),
        )
        print(json.dumps({"registration": registration}), flush=True)
        markets, events, raw_sources, failures = load_inputs(archive, execution, cutoff)
        label_record = archive.latest("research_dataset", "E003_nws_labels", cutoff)
        raw_sources.append(label_record["id"])
        labels = {}
        for line in archive.body(label_record).decode().splitlines():
            r = json.loads(line)
            if not config["development_start"] <= r["day"] < config["validation_end_exclusive"]:
                raise ValueError("Unregistered label date")
            labels[r["event"]] = r
        partials, errors = defaultdict(list), []
        for series, product in products.items():
            raw = archive.latest("e003_nws_zip", product, cutoff)
            raw_sources.append(raw["id"])
            with ZipFile(BytesIO(archive.body(raw))) as zipped:
                for index, entry in enumerate(zipped.infolist()):
                    body = zipped.read(entry)
                    try:
                        r = partial_high(
                            body.decode("ascii"),
                            entry.filename,
                            product,
                            config["development_start"],
                            config["validation_end_exclusive"],
                            windows[series]["standard_utc_offset_hours"],
                        )
                        if r is None:
                            continue
                        partials[series, r["day"]].append(
                            {
                                **r,
                                "source_record_id": raw["id"],
                                "zip_entry_index": index,
                                "raw_sha256": hashlib.sha256(body).hexdigest(),
                            }
                        )
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append(
                            {"source_record_id": raw["id"], "entry_index": index, "error": str(exc)}
                        )
        for rows in partials.values():
            rows.sort(key=lambda r: (r["issued_at"], r["zip_entry_index"]))
        calibration, violations = {}, []
        fit_ts = timestamp(config["train_end_exclusive"])
        for margin in config["margins_f"]:
            daily = {}
            for label in labels.values():
                rows = partials.get((label["series"], label["day"]), [])
                if not rows or label["day"] >= config["train_end_exclusive"]:
                    continue
                if label["settled_at"] is None or parse_time(label["settled_at"]).timestamp() >= fit_ts:
                    continue
                failed = any(r["maximum_so_far_f"] - margin > label["exchange_value_f"] for r in rows)
                daily[label["day"]] = daily.get(label["day"], False) or failed
                if failed:
                    violations.append(
                        {
                            "event": label["event"],
                            "margin_f": margin,
                            "exchange_maximum": label["exchange_value_f"],
                            "reports": rows,
                        }
                    )
            calibration[margin] = {
                "training_days": len(daily),
                "failure_days": sum(daily.values()),
                "probability_no_lower_bound": conservative_bound_probability(list(daily.values())),
            }
        model_id = archive.append(
            "research_model",
            "E014_bound_probabilities",
            utcnow(),
            {},
            canonical(
                {
                    "protocol_record_id": registration,
                    "fit_cutoff": "2025-07-01T00:00:00Z",
                    "calibration": calibration,
                    "source_record_ids": raw_sources,
                }
            ).encode(),
        )
        print(json.dumps({"model_record_id": model_id, "calibration": calibration}), flush=True)
        results = []
        for margin, delay, cap in itertools.product(
            config["margins_f"], config["publication_delays_minutes"], config["maximum_no_prices"]
        ):
            policy = {
                "family": "observed_high_no",
                "margin_f": margin,
                "publication_delay_minutes": delay,
                "maximum_no_price": cap,
                "exit_hours": None,
            }
            policy["id"] = hashlib.sha256(canonical(policy).encode()).hexdigest()[:16]
            probability = calibration[margin]["probability_no_lower_bound"]
            for scenario in execution["scenarios"]:
                decisions, trades = [], []
                for event, tickers in sorted(events.items()):
                    m = markets[tickers[0]]["metadata"]
                    if m["day"] < config["train_end_exclusive"]:
                        continue
                    reports = partials.get((m["series"], m["day"]), [])
                    decision = {"event": event, "day": m["day"], "status": "no_eligible_signal"}
                    if probability is None:
                        decision["status"] = "insufficient_training_days"
                        decisions.append(decision)
                        continue
                    moments = sorted(
                        {
                            math.ceil((parse_time(r["issued_at"]).timestamp() + delay * 60) / 3600) * 3600
                            for r in reports
                        }
                    )
                    for signal_ts in moments:
                        if signal_ts >= m["close_ts"]:
                            continue
                        known = [
                            r
                            for r in reports
                            if parse_time(r["issued_at"]).timestamp() + delay * 60 <= signal_ts
                        ]
                        latest = [r for r in known if r["issued_at"] == known[-1]["issued_at"]]
                        if len({r["maximum_so_far_f"] for r in latest}) != 1:
                            decision["status"] = "ambiguous_same_issue_report"
                            continue
                        source = latest[-1]
                        bound = source["maximum_so_far_f"] - margin
                        options = []
                        for ticker in tickers:
                            item = markets[ticker]
                            quote = item["quotes"].get(signal_ts)
                            if not valid_quote(quote) or not below_observed_bound(item["metadata"], bound):
                                continue
                            price = round(1 - quote["bid"] + float(scenario["slippage"]), 8)
                            if not 0 < price < 1 or price > cap + 1e-12:
                                continue
                            debit, _ = order_cash(price, scenario)
                            if probability + debit > 0:
                                options.append((-debit, ticker, quote))
                        options.sort(key=lambda x: (x[0], x[1]))
                        if not options:
                            continue
                        _, ticker, quote = options[0]
                        item = markets[ticker]
                        selection = {"ticker": ticker, "side": "no", "signal_ask": 1 - quote["bid"]}
                        decision.update(
                            conditional_trade(
                                selection, item["metadata"], item["quotes"], signal_ts, policy, scenario
                            ),
                            probability_no_lower_bound=probability,
                            preliminary_bound_f=bound,
                            preliminary_source=source,
                        )
                        break  # first signal only, irrespective of later fill or outcome
                    decisions.append(decision)
                    if decision["status"] == "conditional_trade":
                        trades.append(decision)
                account = portfolio(trades, execution, fit_ts, timestamp(config["validation_end_exclusive"]))
                result = {
                    "policy": policy,
                    "scenario": scenario["name"],
                    "status_counts": dict(Counter(r["status"] for r in decisions)),
                    "split_results": {
                        "validation": summarize(
                            account,
                            config["train_end_exclusive"],
                            config["validation_end_exclusive"],
                            execution,
                        )
                    },
                    "decisions": decisions,
                    "trades": trades,
                }
                archive.append(
                    "autoresearch_candidate",
                    f"{registration}:{policy['id']}:{scenario['name']}",
                    utcnow(),
                    {},
                    canonical(result).encode(),
                )
                results.append(result)
        simultaneous_diagnostics(results, execution)
        report = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": registration,
            "model_record_id": model_id,
            "calibration": calibration,
            "partial_reports": sum(map(len, partials.values())),
            "source_record_ids": raw_sources,
            "parse_errors": errors,
            "source_failures": failures,
            "training_violations": violations,
            "experiments": len(results),
            "results": [{k: v for k, v in r.items() if k not in ("decisions", "trades")} for r in results],
            "actual_fills": 0,
            "network_requests": 0,
            "holdout_accessed": False,
            "profitability_proven": False,
            "limitations": [
                config["decision_timing"],
                config["bound_probability"],
                config["validation"],
                "NWS issue timestamps are not independent receipt evidence; hourly quotes have no verified depth. All prices/fills are conditional assumptions.",
            ],
        }
        archive.append("experiment_report", "E014_intraday_bounds", utcnow(), {}, canonical(report).encode())
        Path("reports/E014_intraday_bounds.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "experiments": len(results),
                    "partial_reports": report["partial_reports"],
                    "parse_errors": len(errors),
                    "positive_costed_results": sum(
                        r["scenario"] == "costed" and r["split_results"]["validation"]["conditional_pnl"] > 0
                        for r in results
                    ),
                    "conditional_trades_across_alternative_cases": sum(len(r["trades"]) for r in results),
                    "profitability_proven": False,
                }
            ),
            flush=True,
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
