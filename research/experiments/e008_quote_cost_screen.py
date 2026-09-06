"""Screen frozen E007 forecasts against conditional unit quote costs; no actual fills."""

import json
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from research.experiments.e007_market_weather_pool import complete_events, inputs
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import block_mean_interval, holm_adjust
from weatherpred.quote_screen import choose_conditional_purchase
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    try:
        protocol = json.loads(Path("config/e008_quote_cost_screen.json").read_text())
        source_paths = [
            Path(__file__),
            Path("config/e008_quote_cost_screen.json"),
            Path("weatherpred/quote_screen.py"),
            Path("weatherpred/fees.py"),
            Path("weatherpred/calibration.py"),
        ]
        sources = {
            str(p): archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
            for p in source_paths
        }
        scored_record = archive.latest("research_dataset", "E007_scored")
        parent_report = archive.json(archive.latest("experiment_report", "E007_market_weather_pool"))
        if parent_report["model_record_id"] != protocol["model_record_id"]:
            raise ValueError("E007 report/model differs from registered E008 parent")
        if archive.body(scored_record) != Path("reports/E007_scored.jsonl").read_bytes():
            raise ValueError("Saved probabilities differ from archived E007 output")
        score_rows = [json.loads(line) for line in archive.body(scored_record).decode().splitlines()]
        protocol_id = archive.append(
            "experiment_protocol",
            protocol["experiment"],
            utcnow(),
            {},
            canonical(
                {"protocol": protocol, "source_record_ids": sources, "score_record_id": scored_record["id"]}
            ).encode(),
        )
        parent = archive.json(
            archive.db.execute("SELECT * FROM records WHERE id=?", (protocol["model_record_id"],)).fetchone()
        )
        _, weather, markets, quotes = inputs(archive, parent["protocol"])
        events, _ = complete_events(weather, markets, quotes, parent["protocol"])
        events = [
            r
            for r in events
            if r["split"] == "validation" and r["horizon_hours"] == protocol["horizon_hours"]
        ]
        quote_map = {(r["ticker"], r["horizon_hours"]): r for r in quotes}
        probabilities = {
            (r["event"], r["ticker"], r["model"]): r["probability"]
            for r in score_rows
            if r["horizon_hours"] == protocol["horizon_hours"]
        }
        expected = {
            (r["event"], m["ticker"], name)
            for r in events
            for m in r["contracts"]
            for name in protocol["models"]
        }
        if set(probabilities) != expected:
            raise ValueError("Conditional screen probability membership differs from complete source events")
        start, end = (
            date.fromisoformat(protocol["validation_start"]),
            date.fromisoformat(protocol["validation_end_exclusive"]),
        )
        days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days)]
        decisions, summaries, intervals = [], [], []
        for model in protocol["models"]:
            for scenario in protocol["scenarios"]:
                daily = {day: Decimal(0) for day in days}
                selected = []
                for event in events:
                    if (
                        not protocol["validation_start"]
                        <= event["day"]
                        < protocol["validation_end_exclusive"]
                    ):
                        raise ValueError("Nonvalidation date reached conditional cost screen")
                    group = [quote_map[m["ticker"], protocol["horizon_hours"]] for m in event["contracts"]]
                    # Selection receives no outcomes, labels, station errors or later quotes.
                    selection_quotes = [{k: q[k] for k in ("ticker", "bid", "ask")} for q in group]
                    p = [probabilities[event["event"], m["ticker"], model] for m in event["contracts"]]
                    purchase = choose_conditional_purchase(selection_quotes, p, scenario)
                    context = {k: event[k] for k in ("event", "day", "series", "decision_ts")}
                    context.update(
                        model=model, scenario=scenario["name"], quote_source_ids=event["quote_source_ids"]
                    )
                    if purchase is None:
                        decisions.append(
                            dict(
                                context,
                                status="abstain_no_positive_expected_edge",
                                conditional_quantity=0,
                                actual_fills=0,
                            )
                        )
                        continue
                    actual = quote_map[purchase["ticker"], protocol["horizon_hours"]]
                    payout = actual["outcome"] if purchase["side"] == "yes" else 1 - actual["outcome"]
                    pnl = Decimal(payout) - Decimal(purchase["assumed_cost"])
                    trade = {
                        **context,
                        **purchase,
                        "status": "conditional_purchase_only",
                        "payout": payout,
                        "conditional_pnl": str(pnl),
                    }
                    decisions.append(trade)
                    selected.append(trade)
                    daily[event["day"]] += pnl
                values = [float(daily[day]) for day in days]
                summary = {
                    "model": model,
                    "scenario": scenario["name"],
                    "eligible_events": len(events),
                    "eligible_event_days": len({r["day"] for r in events}),
                    "calendar_days": len(days),
                    "conditional_purchases": len(selected),
                    "conditional_purchase_days": len({r["day"] for r in selected}),
                    "conditional_yes_count": sum(r["side"] == "yes" for r in selected),
                    "conditional_pnl_usd": str(sum(daily.values(), Decimal(0))),
                    "assumed_total_purchase_cost": str(
                        sum((Decimal(r["assumed_cost"]) for r in selected), Decimal(0))
                    ),
                    "model_expected_pnl_usd": sum(r["expected_edge"] for r in selected),
                    "actual_fills": 0,
                    "daily_conditional_pnl": {day: str(value) for day, value in daily.items()},
                }
                summaries.append(summary)
                for block in (1, 7, 14):
                    # Original estimator tests negative differences as improvements.
                    raw = block_mean_interval(days, [-v for v in values], block)
                    intervals.append(
                        {
                            "model": model,
                            "scenario": scenario["name"],
                            "block_days": block,
                            "mean_daily_conditional_pnl": -raw["mean_difference"],
                            "lower": -raw["upper"],
                            "upper": -raw["lower"],
                            "approximate_positive_mean_pvalue": raw[
                                "one_sided_improvement_pvalue_approximate"
                            ],
                            "resamples": raw["resamples"],
                            "seed": raw["seed"],
                        }
                    )
        adjusted = holm_adjust([r["approximate_positive_mean_pvalue"] for r in intervals])
        for row, pvalue in zip(intervals, adjusted, strict=True):
            row["holm_adjusted_81_diagnostic_pvalue"] = pvalue
        counts = Counter(r["status"] for r in decisions)
        station_counts = Counter(r["series"] for r in events)
        result = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": protocol_id,
            "parent_model_record_id": protocol["model_record_id"],
            "score_record_id": scored_record["id"],
            "source_record_ids": sources,
            "status_counts": dict(counts),
            "eligible_events_by_station": dict(station_counts),
            "conditional_scenario_results": summaries,
            "conditional_day_block_diagnostics": intervals,
            "actual_fills": 0,
            "historical_execution_verified": False,
            "profitability_proven": False,
            "holdout_accessed": False,
            "network_requests": 0,
            "limitations": [
                protocol[k]
                for k in ("status", "fee_limits", "fills", "outcome", "calendar_policy", "uncertainty")
            ],
        }
        archive.append(
            "experiment_report", "E008_quote_cost_screen", utcnow(), {}, canonical(result).encode()
        )
        Path("reports/E008_quote_cost_screen.json").write_text(json.dumps(result, indent=2))
        body = "\n".join(canonical(r) for r in decisions) + "\n"
        archive.append("research_dataset", "E008_decisions", utcnow(), {}, body.encode())
        Path("reports/E008_decisions.jsonl").write_text(body)
        print(
            json.dumps(
                {
                    "protocol_record_id": protocol_id,
                    "scenario_results": [
                        {k: v for k, v in r.items() if k != "daily_conditional_pnl"} for r in summaries
                    ],
                    "minimum_holm_pvalue": min(adjusted),
                    "historical_execution_verified": False,
                    "profitability_proven": False,
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
