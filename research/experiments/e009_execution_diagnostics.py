"""Descriptive execution-cost and subsequent-price diagnostics for frozen paper orders.

Each completed order is valued separately, not as a simultaneous portfolio exit.
This does not change the broker, create exit orders, or estimate trading returns.
"""

import argparse
import json
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.books import parse_book
from weatherpred.fees import FeeAccumulator
from weatherpred.paper import empty_state, reduce_event, schedule_for
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal
HORIZONS = (0, 30, 60, 300)


def select_book(books, order, last_fill_at, horizon):
    target = last_fill_at + timedelta(seconds=horizon)
    if horizon == 0 and order["style"] == "taker":
        return next((b for b in books if b["id"] == order["arrival_book_record_id"]), None)
    return next(
        (
            b
            for b in books
            if target <= b["started"] <= b["received"] <= target + timedelta(seconds=15)
            and order["ticker"] in b["books"]
            and b["received"] < parse_time(order["close_at"])
        ),
        None,
    )


def liquidation(book, side, quantity, schedule):
    """Full displayed bids, including sale fees; do not invent missing depth."""
    remaining, gross, net, fee = quantity, D(0), D(0), D(0)
    accumulator = FeeAccumulator(schedule)
    for price, depth in book[side]:
        take = min(remaining, depth)
        if take <= 0:
            break
        charge = accumulator.fill(price, take, sell=True)
        gross += price * take
        net += charge["balance_change"]
        fee += charge["net_fee"]
        remaining -= take
        if remaining == 0:
            break
    return {
        "quantity_quoted_for_sale": str(quantity - remaining),
        "quantity_without_bid_depth": str(remaining),
        "gross_bid_proceeds": str(gross),
        "net_bid_proceeds": str(net),
        "exit_fees": str(fee),
    }


def main(run_id):
    archive = Archive()
    try:
        protocol = {
            "experiment": "E009-execution-diagnostics-v3",
            "run_record_id": run_id,
            "horizons_seconds_after_last_fill": HORIZONS,
            "maximum_quote_wait_seconds": 15,
            "status": "Descriptive after first round, before further scheduled orders. No strategy selection or broker change.",
            "rule": "Only taker zero horizon uses its actual arrival book. Maker zero and all later horizons use first fresh paper_books or separately collected paper_diagnostic_books whose request starts at/after target and arrives at most15seconds later, before market close. Missing data remain missing. V2 added future observations. V3 corrects maker zero-horizon timing: v2 used the earlier arrival book even when the maker filled later. Those old maker zero rows are invalid as post-fill marks; other horizons and original taker evidence are unchanged.",
            "costs": "Value each completed paper order individually at full displayed bids, including sale fees under its entry-verified fee schedule held constant. Quantity without depth has no quoted proceeds; report it separately. This is a liquidation quote, not a simulated exit fill or portfolio return.",
            "dependence": "Models/scenarios and repeated hourly decisions share weather outcomes; never count them as independent trials.",
        }
        pinned = archive.latest("experiment_protocol", protocol["experiment"])
        if pinned is None:
            source_id = archive.append(
                "research_source", str(Path(__file__)), utcnow(), {}, Path(__file__).read_bytes()
            )
            protocol_id = archive.append(
                "experiment_protocol",
                protocol["experiment"],
                utcnow(),
                {},
                canonical({**protocol, "source_record_id": source_id}).encode(),
            )
        else:
            registered = archive.json(pinned)
            if {k: v for k, v in registered.items() if k != "source_record_id"} != json.loads(
                canonical(protocol)
            ):
                raise ValueError("Execution diagnostic definition changed; register a new version")
            source = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (registered["source_record_id"],)
            ).fetchone()
            if archive.body(source) != Path(__file__).read_bytes():
                raise ValueError("Registered execution diagnostic source changed")
            protocol_id = pinned["id"]
        archive.db.execute("BEGIN")
        state = empty_state()
        journal = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind='paper_event' AND key=? ORDER BY id", (str(run_id),)
            )
        )
        for r in journal:
            state = reduce_event(state, archive.json(r))
        fills = defaultdict(list)
        for fill in state["fills"]:
            fills[fill["order_id"]].append(fill)
        books, rejected = [], []
        first = min((parse_time(f["at"]) for fs in fills.values() for f in fs), default=utcnow())
        for r in archive.db.execute(
            "SELECT * FROM records WHERE kind IN ('paper_books','paper_diagnostic_books') AND available_at>=? ORDER BY available_at,id",
            (iso(first - timedelta(seconds=5)),),
        ):
            metadata = json.loads(r["metadata"])
            try:
                received = parse_time(r["available_at"])
                started = parse_time(metadata["request_started_at"])
                headers = metadata["headers"]
                server = parsedate_to_datetime(headers["date"])
                if (
                    metadata["status"] != 200
                    or not -2 <= (received - server).total_seconds() <= 5
                    or not 0 <= (received - started).total_seconds() <= 5
                    or float(headers.get("age", 0)) > 5
                ):
                    raise ValueError("Stale, slow or unsuccessful book")
                body = archive.json(r)
                parsed = {b["ticker"]: parse_book(b) for b in body["orderbooks"]}
                if len(parsed) != len(body["orderbooks"]):
                    raise ValueError("Duplicate book ticker")
                books.append({"id": r["id"], "started": started, "received": received, "books": parsed})
            except (ValueError, KeyError) as exc:
                rejected.append({"source_record_id": r["id"], "reason": str(exc)})
        rows = []
        for order_id, fs in fills.items():
            order = state["orders"][order_id]
            if order["status"] not in ("filled", "cancelled"):
                continue
            quantity = sum((D(f["quantity"]) for f in fs), D(0))
            cost = sum((D(f["cost"]) for f in fs), D(0))
            fees = sum((D(f["fee"]) for f in fs), D(0))
            last = max(parse_time(f["at"]) for f in fs)
            for horizon in HORIZONS:
                target = last + timedelta(seconds=horizon)
                row = {
                    "order_id": order_id,
                    "account": order["account"],
                    "event": order["event"],
                    "ticker": order["ticker"],
                    "side": order["side"],
                    "scenario": order["scenario"]["name"],
                    "horizon_seconds": horizon,
                    "target_at": iso(target),
                    "quantity": str(quantity),
                    "entry_cost": str(cost),
                    "entry_fees": str(fees),
                    "estimated_edge_after_haircut_per_contract": str(
                        D(order["haircut_probability"]) - cost / quantity
                    ),
                }
                if target >= parse_time(order["close_at"]):
                    rows.append({**row, "status": "target_after_close"})
                    continue
                book = select_book(books, order, last, horizon)
                if book is None or order["ticker"] not in book["books"]:
                    rows.append({**row, "status": "no_timely_future_book"})
                    continue
                value = liquidation(
                    book["books"][order["ticker"]], order["side"], quantity, schedule_for(order)
                )
                complete = D(value["quantity_without_bid_depth"]) == 0
                rows.append(
                    {
                        **row,
                        **value,
                        "status": "fully_quoted" if complete else "insufficient_exit_depth",
                        "source_record_id": book["id"],
                        "source_received_at": iso(book["received"]),
                        "quote_delay_seconds": (book["received"] - target).total_seconds(),
                        "net_liquidation_difference_per_contract": str(
                            (D(value["net_bid_proceeds"]) - cost) / quantity
                        )
                        if complete
                        else None,
                        "gross_bid_minus_entry_principal_per_contract": str(
                            (D(value["gross_bid_proceeds"]) - (cost - fees)) / quantity
                        )
                        if complete
                        else None,
                        "hypothetical_exit_fills": 0,
                    }
                )
        groups = []
        for horizon in HORIZONS:
            for scenario in sorted({r["scenario"] for r in rows}):
                group = [r for r in rows if r["horizon_seconds"] == horizon and r["scenario"] == scenario]
                complete = [r for r in group if r["status"] == "fully_quoted"]
                values = [D(r["net_liquidation_difference_per_contract"]) for r in complete]
                groups.append(
                    {
                        "horizon_seconds": horizon,
                        "scenario": scenario,
                        "orders": len(group),
                        "status_counts": dict(Counter(r["status"] for r in group)),
                        "distinct_weather_events": len({r["event"] for r in group}),
                        "net_quote_difference_per_contract_min": str(min(values)) if values else None,
                        "net_quote_difference_per_contract_max": str(max(values)) if values else None,
                    }
                )
        result = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": protocol_id,
            "run_record_id": run_id,
            "last_journal_record_id": journal[-1]["id"],
            "groups": groups,
            "rows": rows,
            "rejected_book_sources": rejected,
            "interpretation": protocol["costs"],
            "dependence": protocol["dependence"],
            "profitability_proven": False,
            "real_money_orders": 0,
            "network_requests": 0,
        }
        archive.db.commit()
        archive.append(
            "experiment_report", "E009_execution_diagnostics", utcnow(), {}, canonical(result).encode()
        )
        Path("reports/E009_execution_diagnostics.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, default=21756)
    main(parser.parse_args().run_record_id)
