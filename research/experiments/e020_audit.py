"""Reconstruct E020 capacities and sell proceeds from original source receipts."""

import hashlib
import json
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from research.experiments.e019_audit import independent_quote
from weatherpred.archive import Archive
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book
from weatherpred.paper import empty_state, reduce_event
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal


def main():
    archive = Archive()
    try:
        sources = []

        def raw(identifier):
            row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
            assert row is not None
            assert hashlib.sha256(archive.body(row)).hexdigest() == row["body_sha256"]
            sources.append(identifier)
            return row, archive.json(row)

        registered, plan = raw(78157)
        reported, report = raw(78171)
        for path, digest in plan["code_sha256"].items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        bodies = [raw(i) for i in report["raw_source_ids"]]
        assert all(registered["id"] < row["id"] < reported["id"] for row, _ in bodies)
        batch = next(body for row, body in bodies if row["kind"] == "rain_books")
        books = {b["ticker"]: parse_book(b) for b in batch["orderbooks"]}
        fees = []
        for event in plan["events"]:
            context = next(
                body for row, body in bodies if row["kind"] == "rain_context" and row["key"] == event
            )
            series_name = context["event"]["series_ticker"]
            series = next(
                body["series"]
                for row, body in bodies
                if row["kind"] == "rain_series" and row["key"] == series_name
            )
            changes = [
                c
                for row, body in bodies
                if row["kind"] == "rain_fees" and row["key"] == event
                for c in body["event_fee_changes"]
            ]
            assert any(row["kind"] == "rain_fees" and row["key"] == event for row, _ in bodies)
            schedule = resolve_current_fee(series, changes, parse_time(report["book_received_at"]))
            assert schedule.fee_type in ("quadratic", "quadratic_with_maker_fees")
            fees.append(schedule)
        scenarios = {s["name"]: s for s in plan["scenarios"]}
        for row in report["capacity_curves"]:
            try:
                quotes = [
                    independent_quote(books[t], side, row["quantity"], fee, scenarios[row["scenario"]])
                    for t, side, fee in zip(plan["tickers"], plan["sides"], fees, strict=True)
                ]
            except ValueError as error:
                assert "retained depth" in str(error)
                assert not row["full_displayed_size_available"]
            else:
                assert row["full_displayed_size_available"]
                assert sum(q[0] for q in quotes) == D(row["total_cost"])
                assert D(row["conditional_surplus"]) == row["quantity"] - D(row["total_cost"])
        for cap in report["capital_caps"]:
            eligible = [
                r
                for r in report["capacity_curves"]
                if r["scenario"] == cap["scenario"]
                and r["full_displayed_size_available"]
                and D(r["total_cost"]) <= cap["capital_cap"]
                and D(r["conditional_surplus_per_pair"]) >= D(plan["minimum_conditional_surplus_per_pair"])
            ]
            assert (
                max(eligible, key=lambda r: (D(r["conditional_surplus"]), -r["quantity"]), default=None)
                == cap["best_displayed_surplus_candidate"]
            )
        state = empty_state()
        for row in archive.db.execute(
            "SELECT * FROM records WHERE kind='paper_event' AND key=? AND id<? ORDER BY id",
            (str(plan["parent_paper_run_record_id"]), reported["id"]),
        ):
            state = reduce_event(state, archive.json(row))
        leg_count = 0
        for result in report["current_position_exit_diagnostics"]:
            scenario = scenarios[result["exit_scenario"]]
            positions = [p for p in state["positions"].values() if p["account"] == result["paper_account"]]
            total = D(0)
            for leg in result["legs"]:
                position = next(p for p in positions if p["ticker"] == leg["ticker"])
                schedule = fees[plan["tickers"].index(leg["ticker"])]
                left, proceeds, carry = D(position["quantity"]), D(0), D(0)
                for bid, depth in books[leg["ticker"]][position["side"]]:
                    price = bid - D(scenario["slippage"])
                    quantity = min(
                        left, (depth * D(scenario["depth_retained"])).quantize(D(".01"), rounding=ROUND_FLOOR)
                    )
                    if quantity <= 0 or price <= 0:
                        continue
                    fee = (D(".07") * schedule.multiplier * quantity * price * (1 - price)).quantize(
                        D(".000001"), rounding=ROUND_CEILING
                    )
                    net = quantity * price - fee
                    rounded = net.quantize(schedule.balance_precision, rounding=ROUND_FLOOR)
                    dust = net - rounded
                    carry += dust
                    rebate = min(
                        carry.quantize(schedule.balance_precision, rounding=ROUND_FLOOR),
                        (fee + dust).quantize(schedule.balance_precision, rounding=ROUND_FLOOR),
                    )
                    carry -= rebate
                    proceeds += rounded + rebate
                    left -= quantity
                assert proceeds == D(leg["proceeds_after_fees"])
                assert left == D(leg["unfilled_quantity"])
                total += proceeds
                leg_count += 1
            assert total == D(result["cash_proceeds_if_all_quoted_slices_execute"])
            if result["both_legs_have_full_displayed_exit"]:
                assert total - sum(D(p["cost"]) for p in positions) == D(
                    result["conditional_exit_pnl_if_fully_exited"]
                )
        print(
            json.dumps(
                {
                    "generated_at": iso(utcnow()),
                    "registration": 78157,
                    "report": 78171,
                    "source_ids": sorted(set(sources)),
                    "capacity_rows_reproduced": len(report["capacity_curves"]),
                    "capital_cap_selections_reproduced": len(report["capital_caps"]),
                    "exit_legs_reproduced": leg_count,
                    "fees_recomputed_without_production_accumulator": True,
                    "network_requests": 0,
                    "actual_fills": 0,
                    "profitability_proven": False,
                },
                indent=2,
            )
        )
    finally:
        archive.close()


if __name__ == "__main__":
    main()
