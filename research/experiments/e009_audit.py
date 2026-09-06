"""Read-only replay against original future books, tape and finalized results.

Can run alongside the paper broker. Uses one consistent archive read snapshot.
"""

import argparse
import hashlib
import json
from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from research.experiments.e015_audit import realize_return, remove_offset_basis
from weatherpred.archive import Archive, canonical
from weatherpred.books import parse_book
from weatherpred.netted_paper import reduce_netted
from weatherpred.paper import empty_state, reduce_event, reserved
from weatherpred.shadow import validate_lineage
from weatherpred.shadow_outcomes import finalized_labels
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal


def main(run_id):
    archive = Archive()
    try:
        archive.db.execute("BEGIN")
        registration = archive.db.execute("SELECT * FROM records WHERE id=?", (run_id,)).fetchone()
        if registration is None or registration["kind"] != "experiment_protocol":
            raise ValueError("Missing original paper registration")
        protocol = archive.json(registration)
        experiment = protocol["config"]["experiment"]
        if experiment not in ("E009-live-paper-v1", "E018-conditional-hourly-v1"):
            raise ValueError("This audit supports the E009/E018 directional cohorts")
        netted = experiment == "E018-conditional-hourly-v1"
        prefix = "E018" if netted else "E009"
        for path, expected in protocol["code_sha256"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError("Registered execution source changed: " + path)
        records = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind=? AND key=? ORDER BY id",
                ("netted_paper_event" if netted else "paper_event", str(run_id)),
            )
        )
        if not records:
            raise ValueError("No registered paper journal found")
        state, expected_slices, maker_allowed = empty_state(), {}, {}
        cash, fees, carried = {}, defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
        quantities, costs, realized = (defaultdict(lambda: D(0)) for _ in range(3))
        nettings = 0
        taker_count, maker_count, settlements, sources = 0, 0, 0, set()

        def raw(source_id):
            row = archive.db.execute("SELECT * FROM records WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise ValueError("Missing original execution source")
            if hashlib.sha256(archive.body(row)).hexdigest() != row["body_sha256"]:
                raise ValueError("Original source bytes changed")
            sources.add(source_id)
            return row, archive.json(row)

        for record in records:
            event = archive.json(record)
            kind, data = event["type"], event["data"]
            if event["at"] != record["available_at"]:
                raise ValueError("Paper journal timestamp mismatch")
            if kind == "account":
                cash[data["account"]] = D(data["initial_cash"])
            elif kind == "order":
                forecast_source, forecast = raw(data["forecast_record_id"])
                if parse_time(forecast_source["available_at"]) >= parse_time(data["submitted_at"]):
                    raise ValueError("Paper order predates its probability forecast")
                validate_lineage(
                    archive,
                    forecast["evidence_record_ids"],
                    parse_time(forecast["published_at"]),
                    parse_time(forecast["settlement_at"]),
                    forecast["model_record_id"],
                )
                member = next(m for m in forecast["markets"] if m["ticker"] == data["ticker"])
                probability = D(str(member["probabilities"][data["model"]]))
                expected = (probability if data["side"] == "yes" else 1 - probability) - D("0.03")
                if D(data["haircut_probability"]) != expected or D(str(data["floor_strike"])) != D(
                    str(member["floor_strike"])
                ):
                    raise ValueError("Order probability/strike differs from frozen forecast")
            elif kind == "arrival":
                order = state["orders"][data["order_id"]]
                source, body = raw(data["book_record_id"])
                if source["kind"] != "paper_books":
                    raise ValueError("Arrival did not use a newly requested paper book")
                metadata = json.loads(source["metadata"])
                if (
                    parse_time(metadata["request_started_at"]) < parse_time(order["arrival_due_at"])
                    or source["available_at"] != event["at"]
                ):
                    raise ValueError("Arrival reused a pre-delay book or changed receipt time")
                book = parse_book(next(b for b in body["orderbooks"] if b["ticker"] == order["ticker"]))
                if order["style"] == "taker":
                    # Independent depth walk, without invoking taker_slices.
                    remaining, pieces = D(order["remaining"]), []
                    for bid, depth in book["no" if order["side"] == "yes" else "yes"]:
                        price = 1 - bid + D(order["scenario"]["slippage"])
                        if price > D(order["limit"]) or price >= 1:
                            break
                        quantity = min(
                            remaining,
                            (depth * D(order["scenario"]["depth_retained"])).quantize(
                                D("0.01"), rounding=ROUND_FLOOR
                            ),
                        )
                        if quantity > 0:
                            pieces.append((price, quantity))
                            remaining -= quantity
                        if remaining == 0:
                            break
                    expected_slices[order["id"]] = pieces
                else:
                    ahead = sum((q for p, q in book[order["side"]] if p >= D(order["limit"])), D(0))
                    if ahead != D(data["queue_ahead"]):
                        raise ValueError("Maker queue ahead differs from arrival book")
            elif kind == "queue":
                order = state["orders"][data["order_id"]]
                source, body = raw(data["source_record_id"])
                trade = next(t for t in body["trades"] if t["trade_id"] == data["trade_id"])
                opposite = "no" if order["side"] == "yes" else "yes"
                if (
                    source["kind"] != "paper_trades"
                    or trade.get("is_block_trade") is not False
                    or trade["ticker"] != order["ticker"]
                    or trade["taker_outcome_side"] != opposite
                    or trade["taker_book_side"] != ("bid" if opposite == "yes" else "ask")
                ):
                    raise ValueError("Maker queue used an ineligible public trade")
                if (
                    not parse_time(order["arrived_at"])
                    < parse_time(trade["created_time"])
                    <= parse_time(source["available_at"])
                    < parse_time(order["expires_at"])
                ):
                    raise ValueError("Maker trade timestamp lies outside resting order window")
                if D(trade[order["side"] + "_price_dollars"]) >= D(order["limit"]):
                    raise ValueError("Maker queue advanced on a touch or worse print")
                volume, ahead = D(trade["count_fp"]), D(order["queue_ahead"])
                if D(data["queue_ahead"]) != max(D(0), ahead - volume):
                    raise ValueError("Maker queue consumption differs from actual printed volume")
                allowed = (max(D(0), volume - ahead) * D(order["scenario"]["trade_participation"])).quantize(
                    D("0.01"), rounding=ROUND_FLOOR
                )
                maker_allowed[order["id"], trade["trade_id"]] = min(D(order["remaining"]), allowed)
            elif kind == "fill":
                order = state["orders"][data["order_id"]]
                price, quantity = D(data["price"]), D(data["quantity"])
                if order["style"] == "taker":
                    pieces = expected_slices[order["id"]]
                    if not pieces or pieces.pop(0) != (price, quantity):
                        raise ValueError("Paper taker fill differs from future-book depth walk")
                    taker_count += 1
                else:
                    trade_id = data["fill_id"].removeprefix("trade:")
                    if maker_allowed.pop((order["id"], trade_id), None) != quantity or price != D(
                        order["limit"]
                    ):
                        raise ValueError("Paper maker fill exceeds queue/tape evidence")
                    maker_count += 1
                # Recompute fees without invoking the production fee accumulator.
                schedule = order["fee_schedule"]
                if schedule["fee_type"] not in ("quadratic", "quadratic_with_maker_fees"):
                    raise ValueError("Unknown execution fee schedule")
                coefficient = D(".07")
                if order["style"] == "maker":
                    coefficient = D(".0175") if schedule["fee_type"] == "quadratic_with_maker_fees" else D(0)
                precision = D(schedule["balance_precision"])
                if precision not in (D(".01"), D(".0001")):
                    raise ValueError("Unknown balance precision")
                trade_fee = (
                    coefficient * D(schedule["multiplier"]) * price * (1 - price) * quantity
                ).quantize(D(".000001"), rounding=ROUND_CEILING)
                aligned = (-price * quantity - trade_fee).quantize(precision, rounding=ROUND_FLOOR)
                rounding = -price * quantity - trade_fee - aligned
                carried[order["id"]] += rounding
                rebate = min(
                    carried[order["id"]].quantize(precision, rounding=ROUND_FLOOR),
                    (trade_fee + rounding).quantize(precision, rounding=ROUND_FLOOR),
                )
                carried[order["id"]] -= rebate
                cash[order["account"]] += aligned + rebate
                fees[order["account"]] += trade_fee + rounding - rebate
                pkey = (order["account"], order["ticker"], order["side"])
                quantities[pkey] += quantity
                costs[pkey] -= aligned + rebate
                if netted:
                    keys = [(order["account"], order["ticker"], side) for side in ("yes", "no")]
                    offset = min(quantities[k] for k in keys)
                    if offset > 0:
                        allocated = remove_offset_basis(quantities, costs, keys, offset)
                        cash[order["account"]] += offset
                        realized[order["account"]] = realize_return(
                            realized[order["account"]], offset, allocated
                        )
                        nettings += 1
            elif kind == "settlement":
                source, body = raw(data["source_record_id"])
                order = next(o for o in state["orders"].values() if o["event"] == data["event"])
                _, forecast = raw(order["forecast_record_id"])
                labels = finalized_labels(forecast, body)
                if (
                    source["kind"] != "paper_settlement_source"
                    or labels is None
                    or labels != data["labels"]
                    or parse_time(source["available_at"]) > parse_time(event["at"])
                ):
                    raise ValueError("Paper settlement differs from finalized unchanged contracts")
                for position in state["positions"].values():
                    if position["event"] == data["event"]:
                        y = labels[position["ticker"]]
                        key = (position["account"], position["ticker"], position["side"])
                        payout = D(y if position["side"] == "yes" else 1 - y) * quantities.pop(key)
                        cash[position["account"]] += payout
                        realized[position["account"]] = realize_return(
                            realized[position["account"]], payout, costs.pop(key)
                        )
                        settlements += 1
            state = (reduce_netted if netted else reduce_event)(state, event)
        for account, actual in state["accounts"].items():
            if D(actual["cash"]) != cash[account] or D(actual["fees"]) != fees[account]:
                raise ValueError("Independently accumulated cash/fees differ from ledger")
            if cash[account] < reserved(state, account):
                raise ValueError("Paper reservations exceed remaining cash")
            if D(actual["realized_pnl"]) != realized[account]:
                raise ValueError("Independent realized profit differs from ledger")
        actual_positions = {
            (p["account"], p["ticker"], p["side"]): (D(p["quantity"]), D(p["cost"]))
            for p in state["positions"].values()
        }
        expected_positions = {key: (quantity, costs[key]) for key, quantity in quantities.items() if quantity}
        if actual_positions != expected_positions or (netted and len(state.get("nettings", [])) != nettings):
            raise ValueError("Independent net positions or offsets differ from ledger")
        result = {
            "generated_at": iso(utcnow()),
            "run_record_id": run_id,
            "last_journal_record_id": records[-1]["id"],
            "paper_events_replayed": len(records),
            "accounts_reproduced": len(state["accounts"]),
            "orders_reproduced": len(state["orders"]),
            "taker_fills_reproduced_from_future_books": taker_count,
            "maker_fills_reproduced_from_trade_through": maker_count,
            "settled_positions_reproduced": settlements,
            "source_records_checked": len(sources),
            "cash_and_fees_reproduced": True,
            "fees_recomputed_without_production_accumulator": True if taker_count + maker_count else None,
            "net_positions_and_realized_pnl_reproduced": True if taker_count + maker_count else None,
            "nettings_reproduced": nettings,
            "network_requests": 0,
            "real_money_orders": 0,
            "profitability_proven": False,
            "scope_limit": "Raw execution arithmetic and recorded-order lineage; not actual fills, independent days, omitted-signal detection or forecast calibration.",
        }
        archive.db.commit()
        archive.append(
            "experiment_report", prefix + "_execution_replay", utcnow(), {}, canonical(result).encode()
        )
        Path("reports/" + prefix + "_audit.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, default=21756)
    main(parser.parse_args().run_record_id)
