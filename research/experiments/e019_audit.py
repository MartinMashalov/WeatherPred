"""Independent rain-pair receipt, future-depth, fee and cash replay; no network."""

import argparse
import hashlib
import json
from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book
from weatherpred.market_making import predicate
from weatherpred.paper import empty_state, reduce_event
from weatherpred.rain_relations import confirmed_dry, relation
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal


def independent_quote(book, side, quantity, schedule, scenario):
    remaining, total, carried, worst = D(quantity), D(0), D(0), None
    precision = schedule.balance_precision
    for bid, depth in book["no" if side == "yes" else "yes"]:
        price = 1 - bid + D(scenario["slippage"])
        if price >= 1:
            continue
        take = min(
            remaining, (depth * D(scenario["depth_retained"])).quantize(D(".01"), rounding=ROUND_FLOOR)
        )
        if take <= 0:
            continue
        fee = (D(".07") * schedule.multiplier * price * (1 - price) * take).quantize(
            D(".000001"), rounding=ROUND_CEILING
        )
        change = (-price * take - fee).quantize(precision, rounding=ROUND_FLOOR)
        rounding = -price * take - fee - change
        carried += rounding
        rebate = min(
            carried.quantize(precision, rounding=ROUND_FLOOR),
            (fee + rounding).quantize(precision, rounding=ROUND_FLOOR),
        )
        carried -= rebate
        total -= change + rebate
        worst = price
        remaining -= take
        if not remaining:
            return total, worst
    raise ValueError("Decision pair lacks the claimed retained depth")


def main(run_id):
    archive = Archive()
    try:
        archive.db.execute("BEGIN")
        sources = set()

        def raw(identifier):
            r = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
            if r is None or hashlib.sha256(archive.body(r)).hexdigest() != r["body_sha256"]:
                raise ValueError("Missing or altered original rain source")
            sources.add(identifier)
            return r, archive.json(r)

        registration, protocol = raw(run_id)
        cfg = protocol["config"]
        if cfg["experiment"] != "E019-rain-calendar-pairs-v1" or parse_time(
            registration["available_at"]
        ) >= parse_time(cfg["first_decision_at"]):
            raise ValueError("Invalid or late rain-pair registration")
        for path, digest in protocol["code_sha256"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("Registered source changed: " + path)
        journal = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind='paper_event' AND key=? ORDER BY id", (str(run_id),)
            )
        )
        state, decisions, allowed = empty_state(), {}, {}
        cash, fees, carried, qty, costs, realized = {}, *[defaultdict(lambda: D(0)) for _ in range(5)]
        arrivals, fills, settlements = 0, 0, 0
        paired = defaultdict(list)
        for r in journal:
            event = archive.json(r)
            kind, data = event["type"], event["data"]
            if event["at"] != r["available_at"]:
                raise ValueError("Journal receipt timestamp changed")
            if kind == "account":
                cash[data["account"]] = D(data["initial_cash"])
            elif kind == "order":
                drow, decision = raw(data["decision_record_id"])
                if (
                    drow["kind"] != "rain_decision"
                    or str(decision["run_record_id"]) != str(run_id)
                    or not parse_time(drow["available_at"]) < parse_time(data["submitted_at"])
                ):
                    raise ValueError("Order has missing or future decision evidence")
                member = next(m for m in protocol["panel"] if m["city"] == data["city"])
                if data["decision_record_id"] not in decisions:
                    bodies = []
                    for identifier in decision["source_record_ids"]:
                        row, body = raw(identifier)
                        if parse_time(row["available_at"]) > parse_time(drow["available_at"]):
                            raise ValueError("Future source used at rain decision")
                        bodies.append((row["kind"], body))
                    previous = next(b for k, b in bodies if k == "rain_saturday")
                    source = next(b for k, b in bodies if k == "rain_official_source")
                    decisions[data["decision_record_id"]] = previous, source
                previous, source = decisions[data["decision_record_id"]]
                a = next(m for m in previous["markets"] if m["ticker"] == member["saturday"]["ticker"])
                confirmed_dry(a, source, parse_time(drow["available_at"]))
                relation(a, *member["markets"])
                plan = next(
                    p["plan"]
                    for p in decision["plans"]
                    if p["city"] == data["city"] and p["account"] == data["account"]
                )
                i = data["leg_index"]
                scenario = next(s for s in cfg["scenarios"] if s["name"] == data["account"])
                delay = (
                    parse_time(data["arrival_due_at"]) - parse_time(data["submitted_at"])
                ).total_seconds()
                if (
                    data["pair_plan"] != plan
                    or data["quantity"] != plan["quantity"]
                    or data["limit"] != plan["limits"][i]
                    or data["side"] != plan["sides"][i]
                    or data["market_predicate"] != predicate(member["markets"][i])
                    or data["scenario"] != scenario
                    or delay != scenario["latency_seconds"] + i * scenario["leg_stagger_seconds"]
                ):
                    raise ValueError("Order differs from published pair plan or registered delay")
                if D(plan["conditional_surplus"]) / D(plan["quantity"]) - D(
                    cfg["source_allowance_per_pair"]
                ) < D(cfg["minimum_surplus_per_pair"]):
                    raise ValueError("Pair decision failed its cost/source margin")
                _, quoted_batch = raw(decision["decision_book_record_id"])
                decision_sources = [raw(j) for j in decision["source_record_ids"]]
                reconstructed_cost = D(0)
                for leg, market in enumerate(member["markets"]):
                    event_name = market["event_ticker"]
                    event_body = next(
                        body
                        for row, body in decision_sources
                        if row["kind"] == "rain_context" and row["key"] == event_name
                    )
                    series_name = event_body["event"]["series_ticker"]
                    series = next(
                        body["series"]
                        for row, body in decision_sources
                        if row["kind"] == "rain_series" and row["key"] == series_name
                    )
                    changes = [
                        c
                        for row, body in decision_sources
                        if row["kind"] == "rain_fees" and row["key"] == event_name
                        for c in body["event_fee_changes"]
                    ]
                    schedule = resolve_current_fee(series, changes, parse_time(drow["available_at"]))
                    quote = parse_book(
                        next(b for b in quoted_batch["orderbooks"] if b["ticker"] == market["ticker"])
                    )
                    cost, worst = independent_quote(
                        quote, plan["sides"][leg], plan["quantity"], schedule, scenario
                    )
                    if worst != D(plan["limits"][leg]):
                        raise ValueError("Decision limit differs from the costed depth")
                    reconstructed_cost += cost
                if (
                    reconstructed_cost != D(plan["decision_cost"])
                    or D(plan["conditional_surplus"]) != D(plan["quantity"]) - reconstructed_cost
                ):
                    raise ValueError("Published pair surplus differs from independently costed asks")
                paired[data["account"], data["city"]].append(data)
            elif kind == "arrival":
                o = state["orders"][data["order_id"]]
                brow, batch = raw(data["book_record_id"])
                meta = json.loads(brow["metadata"])
                if (
                    brow["kind"] != "rain_books"
                    or brow["available_at"] != event["at"]
                    or parse_time(meta["request_started_at"]) < parse_time(o["arrival_due_at"])
                    or (parse_time(event["at"]) - parse_time(o["arrival_due_at"])).total_seconds() > 15
                ):
                    raise ValueError("Arrival did not use a timely independent future book")
                ctx = [raw(i) for i in data["context_record_ids"]]
                if any(parse_time(row["available_at"]) > parse_time(event["at"]) for row, _ in ctx):
                    raise ValueError("Future arrival context")
                event_source = next(b for row, b in ctx if row["kind"] == "rain_context")
                market = next(m for m in event_source["markets"] if m["ticker"] == o["ticker"])
                series = next(b["series"] for row, b in ctx if row["kind"] == "rain_series")
                changes = [c for row, b in ctx if row["kind"] == "rain_fees" for c in b["event_fee_changes"]]
                fee = resolve_current_fee(series, changes, parse_time(event["at"]))
                terms = {
                    k: series.get(k)
                    for k in ("contract_terms_url", "contract_url", "settlement_sources", "product_metadata")
                }
                if (
                    market["status"] != "active"
                    or predicate(market) != o["market_predicate"]
                    or terms != o["series_terms"]
                    or (fee.fee_type, fee.multiplier, fee.balance_precision)
                    != (
                        o["fee_schedule"]["fee_type"],
                        D(o["fee_schedule"]["multiplier"]),
                        D(o["fee_schedule"]["balance_precision"]),
                    )
                ):
                    raise ValueError("Arrival rule or fee differs from intended contract")
                book = parse_book(next(b for b in batch["orderbooks"] if b["ticker"] == o["ticker"]))
                remaining, slices = D(o["remaining"]), []
                for bid, depth in book["no" if o["side"] == "yes" else "yes"]:
                    price = 1 - bid + D(o["scenario"]["slippage"])
                    if price > D(o["limit"]) or price >= 1:
                        break
                    take = min(
                        remaining,
                        (depth * D(o["scenario"]["depth_retained"])).quantize(D(".01"), rounding=ROUND_FLOOR),
                    )
                    if take > 0:
                        slices.append((price, take))
                        remaining -= take
                    if not remaining:
                        break
                allowed[o["id"]] = slices
                arrivals += 1
            elif kind == "fill":
                o = state["orders"][data["order_id"]]
                price, quantity = D(data["price"]), D(data["quantity"])
                if (
                    not allowed[o["id"]]
                    or allowed[o["id"]].pop(0) != (price, quantity)
                    or data["source_record_id"] != o["arrival_book_record_id"]
                    or data["evidence_at"] != o["arrived_at"]
                ):
                    raise ValueError("Fill cannot be reconstructed from its future book")
                # Independent cash arithmetic, without the production accumulator.
                f = o["fee_schedule"]
                precision = D(f["balance_precision"])
                trade = (D(".07") * D(f["multiplier"]) * price * (1 - price) * quantity).quantize(
                    D(".000001"), rounding=ROUND_CEILING
                )
                change = (-price * quantity - trade).quantize(precision, rounding=ROUND_FLOOR)
                rounding = -price * quantity - trade - change
                carried[o["id"]] += rounding
                rebate = min(
                    carried[o["id"]].quantize(precision, rounding=ROUND_FLOOR),
                    (trade + rounding).quantize(precision, rounding=ROUND_FLOOR),
                )
                carried[o["id"]] -= rebate
                cash[o["account"]] += change + rebate
                fees[o["account"]] += trade + rounding - rebate
                key = (o["account"], o["ticker"], o["side"])
                qty[key] += quantity
                costs[key] -= change + rebate
                fills += 1
            elif kind == "settlement":
                source, body = raw(data["source_record_id"])
                if source["kind"] != "rain_settlement" or parse_time(source["available_at"]) > parse_time(
                    event["at"]
                ):
                    raise ValueError("Invalid settlement receipt")
                for p in state["positions"].values():
                    if p["event"] != data["event"]:
                        continue
                    m = next(m for m in body["markets"] if m["ticker"] == p["ticker"])
                    y = int(m["result"] == "yes")
                    original = next(o for o in state["orders"].values() if o["ticker"] == m["ticker"])[
                        "market_predicate"
                    ]
                    if (
                        m["status"] != "finalized"
                        or m["result"] not in ("yes", "no")
                        or y != data["labels"][p["ticker"]]
                        or {**predicate(m), "close_time": original["close_time"]} != original
                        or parse_time(m["settlement_ts"]) > parse_time(event["at"])
                    ):
                        raise ValueError("Settlement does not match the finalized unchanged rain market")
                    key = (p["account"], p["ticker"], p["side"])
                    payout = qty.pop(key) * (y if p["side"] == "yes" else 1 - y)
                    cash[p["account"]] += payout
                    realized[p["account"]] += payout - costs.pop(key)
                    settlements += 1
            state = reduce_event(state, event)
        for orders in paired.values():
            if (
                len(orders) != 2
                or {o["leg_index"] for o in orders} != {0, 1}
                or len({o["submitted_at"] for o in orders}) != 1
            ):
                raise ValueError("Missing pair intent or repeated attempt for the same city")
        for account, value in state["accounts"].items():
            if (D(value["cash"]), D(value["fees"]), D(value["realized_pnl"])) != (
                cash[account],
                fees[account],
                realized[account],
            ):
                raise ValueError("Independent cash, fees or realized result differs")
        actual = {
            (p["account"], p["ticker"], p["side"]): (D(p["quantity"]), D(p["cost"]))
            for p in state["positions"].values()
        }
        if actual != {key: (q, costs[key]) for key, q in qty.items()}:
            raise ValueError("Independent remaining inventory differs")
        result = {
            "generated_at": iso(utcnow()),
            "run_record_id": run_id,
            "journal_records": len(journal),
            "last_record_id": journal[-1]["id"] if journal else None,
            "accounts": len(cash),
            "paired_intents": len(paired),
            "orders": len(state["orders"]),
            "independent_arrivals": arrivals,
            "fills_from_future_books": fills,
            "settled_positions": settlements,
            "raw_sources_checked": len(sources),
            "cash_and_positions_reproduced": True,
            "fees_recomputed_without_production_accumulator": True if fills else None,
            "network_requests": 0,
            "real_money_orders": 0,
            "profitability_proven": False,
            "limits": "Execution arithmetic and recorded source/order lineage; no actual fills, independent days, future source-failure guarantee or omitted-signal proof.",
        }
        archive.db.commit()
        archive.append("experiment_report", "E019_audit", utcnow(), {}, canonical(result).encode())
        Path("reports/E019_audit.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, required=True)
    main(parser.parse_args().run_record_id)
