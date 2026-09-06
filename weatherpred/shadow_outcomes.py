"""Score immutable prospective forecasts only against finalized contract results."""

import json
from decimal import Decimal
from pathlib import Path

import numpy as np

from weatherpred.archive import canonical
from weatherpred.books import parse_book
from weatherpred.calibration import binary_metrics
from weatherpred.shadow import validate_lineage
from weatherpred.timeutil import iso, parse_time, utcnow


def finalized_labels(forecast, event_data):
    current = {m["ticker"]: m for m in event_data["markets"]}
    tickers = {m["ticker"] for m in forecast["markets"]}
    if set(current) != tickers:
        raise ValueError("Final event membership differs from prospective event")
    if any(current[t].get("status") != "finalized" for t in tickers):
        return None
    labels = {}
    for row in forecast["markets"]:
        m = current[row["ticker"]]
        if m.get("result") not in ("yes", "no"):
            raise ValueError("Nonbinary finalized result requires manual settlement review")
        if m.get("strike_type") != "greater" or Decimal(str(m["floor_strike"])) != Decimal(
            str(row["floor_strike"])
        ):
            raise ValueError("Contract predicate changed after forecast publication")
        labels[row["ticker"]] = int(m["result"] == "yes")
    return labels


def score_shadow(
    client,
    directory="reports",
    *,
    forecast_kind="shadow_forecast",
    invalid_kind="shadow_invalidated",
    report_key="E004_shadow_outcomes",
):
    archive = client.archive
    records = list(
        archive.db.execute("SELECT * FROM records WHERE kind=? ORDER BY available_at,id", (forecast_kind,))
    )
    invalid = {
        int(r["key"]) for r in archive.db.execute("SELECT key FROM records WHERE kind=?", (invalid_kind,))
    }
    event_cache, scored, pending, errors = {}, [], [], []
    for record in records:
        if record["id"] in invalid:
            errors.append({"forecast_record_id": record["id"], "reason": "explicitly_invalidated"})
            continue
        forecast = archive.json(record)
        settlement, published = parse_time(forecast["settlement_at"]), parse_time(forecast["published_at"])
        if parse_time(record["available_at"]) != published:
            raise ValueError("Forecast publication timestamp differs from archive")
        validate_lineage(
            archive, forecast["evidence_record_ids"], published, settlement, forecast["model_record_id"]
        )
        if utcnow() < settlement:
            pending.append({"forecast_record_id": record["id"], "reason": "future_settlement"})
            continue
        event = forecast["event"]
        if event not in event_cache:
            event_cache[event] = client.json("/events/" + event, kind="shadow_outcome_source", key=event)
        data, outcome_id = event_cache[event]
        labels = finalized_labels(forecast, data)
        if labels is None:
            pending.append(
                {
                    "forecast_record_id": record["id"],
                    "reason": "contract_not_finalized",
                    "outcome_source_record_id": outcome_id,
                }
            )
            continue
        book_records = [
            archive.db.execute("SELECT * FROM records WHERE id=? AND kind='shadow_books'", (r,)).fetchone()
            for r in forecast["evidence_record_ids"]
        ]
        book_records = [r for r in book_records if r is not None]
        if len(book_records) != 1:
            raise ValueError("Ambiguous prospective quote lineage")
        books = {b["ticker"]: parse_book(b) for b in archive.json(book_records[0])["orderbooks"]}
        rows = []
        for m in forecast["markets"]:
            book = books[m["ticker"]]
            quote = None
            if book["yes"] and book["no"]:
                bid, ask = book["yes"][0][0], 1 - book["no"][0][0]
                if 0 < bid < ask < 1:
                    quote = float((bid + ask) / 2)
            probabilities = dict(m["probabilities"])
            if quote is not None:
                probabilities["market_midpoint"] = quote
            for name, probability in probabilities.items():
                metrics = binary_metrics([probability], [labels[m["ticker"]]])
                rows.append(
                    {
                        "ticker": m["ticker"],
                        "model": name,
                        "probability": probability,
                        "outcome": labels[m["ticker"]],
                        "paired_quote": quote is not None,
                        **{key: float(value[0]) for key, value in metrics.items()},
                    }
                )
        summary = []
        for name in sorted({r["model"] for r in rows}):
            group = [r for r in rows if r["model"] == name and r["paired_quote"]]
            if group:
                summary.append(
                    {
                        "model": name,
                        "paired_contracts": len(group),
                        **{k: float(np.mean([r[k] for r in group])) for k in ("brier", "log_loss")},
                    }
                )
        scored.append(
            {
                "forecast_record_id": record["id"],
                "event": event,
                "day": settlement.date().isoformat(),
                "horizon_minutes": forecast["horizon_minutes"],
                "model_record_id": forecast["model_record_id"],
                "outcome_source_record_id": outcome_id,
                "published_at": forecast["published_at"],
                "settlement_at": forecast["settlement_at"],
                "rows": rows,
                "paired_event_scores": summary,
            }
        )
    result = {
        "generated_at": iso(utcnow()),
        "forecast_records": len(records),
        "scored_decisions": len(scored),
        "distinct_scored_events": len({r["event"] for r in scored}),
        "independent_days_at_most": len({r["day"] for r in scored}),
        "scored": scored,
        "pending": pending,
        "errors": errors,
        "profitability_proven": False,
        "real_money_orders": 0,
        "fills": 0,
        "limitations": [
            "Abstention-only forecast validation, not evidence of trading returns",
            "Multiple horizons and strikes on one hour are dependent",
            "No forward promotion before the full preregistered sample and execution gates",
        ],
    }
    archive.append("experiment_report", report_key, utcnow(), {}, canonical(result).encode())
    Path(directory, report_key + ".json").write_text(json.dumps(result, indent=2))
    return {k: v for k, v in result.items() if k not in ("scored", "pending")}
