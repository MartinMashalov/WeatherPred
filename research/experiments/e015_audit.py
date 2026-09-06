"""Read-only E015 replay with independent raw-price, queue and cash arithmetic."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

from weatherpred.archive import Archive
from weatherpred.paper import empty_state, reduce_event
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal


def require(value, reason):
    if not value:
        raise ValueError(reason)


def main(run_id):
    archive = Archive()
    try:
        archive.db.execute("BEGIN")
        cache = {}

        def raw(identifier):
            if identifier not in cache:
                row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
                require(row is not None, "Missing source")
                body = archive.body(row)
                require(hashlib.sha256(body).hexdigest() == row["body_sha256"], "Source bytes changed")
                cache[identifier] = (row, json.loads(body))
            return cache[identifier]

        reg, protocol = raw(run_id)
        cfg = protocol["config"]
        require(
            reg["kind"] == "experiment_protocol" and cfg["experiment"] == "E015-paired-maker-v1", "Wrong run"
        )
        require(parse_time(reg["available_at"]) < parse_time(cfg["first_decision_at"]), "Late registration")
        for path, expected in protocol["code_sha256"].items():
            require(
                hashlib.sha256(Path(path).read_bytes()).hexdigest() == expected,
                "Pinned source changed: " + path,
            )
            source = archive.db.execute(
                "SELECT * FROM records WHERE id=?", (protocol["source_record_ids"][path],)
            ).fetchone()
            require(hashlib.sha256(archive.body(source)).hexdigest() == expected, "Archived code differs")
        panel = {m["ticker"]: m for m in protocol["panel"]}
        records = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind='paper_event' AND key=? ORDER BY id", (str(run_id),)
            )
        )
        state = empty_state()
        cash, fees, carried = {}, defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
        quantities, costs = defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
        expected_fill, counts, latencies = {}, Counter(), defaultdict(list)
        scenarios = {s["name"]: s for s in cfg["scenarios"]}

        def book(identifier, ticker):
            row, body = raw(identifier)
            require(row["kind"] == "maker_books", "Unexpected book namespace")
            meta = json.loads(row["metadata"])
            received, started = parse_time(row["available_at"]), parse_time(meta["request_started_at"])
            headers = meta["headers"]
            server = parsedate_to_datetime(headers["date"])
            require(
                -2 <= (received - server).total_seconds() <= 5
                and float(headers.get("age", 0)) <= 5
                and (received - started).total_seconds() <= 5,
                "Stale book",
            )
            rows = [b for b in body["orderbooks"] if b["ticker"] == ticker]
            require(len(rows) == 1, "Book absent or duplicated")
            levels = {
                s: sorted([(D(p), D(q)) for p, q in rows[0]["orderbook_fp"][s + "_dollars"]], reverse=True)
                for s in ("yes", "no")
            }
            return levels, started, received

        def check_context(order, refs, at):
            bodies = []
            for identifier in refs:
                row, body = raw(identifier)
                require(parse_time(row["available_at"]) <= at, "Future context used")
                bodies.append((row["kind"], body))
            market_bodies = [b for k, b in bodies if k == "maker_context"]
            require(len(market_bodies) == 1, "Ambiguous market context")
            member = next(m for m in market_bodies[0]["markets"] if m["ticker"] == order["ticker"])
            require(member["status"] == "active", "Order used inactive market")
            require(
                all(member.get(k) == v for k, v in order["market_predicate"].items()),
                "Order predicate changed",
            )
            require(
                all(panel[order["ticker"]].get(k) == v for k, v in order["market_predicate"].items()),
                "Order does not match registration",
            )
            series = [b["series"] for k, b in bodies if k == "maker_series"]
            require(len(series) == 1, "Ambiguous series fees")
            fee_type, multiplier = series[0]["fee_type"], series[0]["fee_multiplier"]
            changes = sorted(
                [x for k, b in bodies if k == "maker_fees" for x in b["event_fee_changes"]],
                key=lambda x: parse_time(x["scheduled_ts"]),
            )
            for change in changes:
                scheduled = parse_time(change["scheduled_ts"])
                require(not at < scheduled <= parse_time(order["expires_at"]), "Order spans known fee change")
                if scheduled <= at:
                    fee_type = change.get("fee_type_override") or series[0]["fee_type"]
                    override = change.get("fee_multiplier_override")
                    multiplier = series[0]["fee_multiplier"] if override is None else override
            require(
                fee_type == order["fee_schedule"]["fee_type"]
                and D(str(multiplier)) == D(order["fee_schedule"]["multiplier"]),
                "Fee schedule differs from raw context",
            )

        for row in records:
            event = archive.json(row)
            kind, data, at = event["type"], event["data"], parse_time(event["at"])
            require(row["available_at"] == event["at"], "Journal time differs from record")
            counts[kind] += 1
            if kind == "account":
                require(data["initial_cash"] == cfg["initial_cash"], "Changed initial cash")
                cash[data["account"]] = D(data["initial_cash"])
            elif kind == "order":
                policy, scenario = data["account"].split(":")
                require(
                    policy in cfg["policies"] and data["scenario"] == scenarios[scenario],
                    "Unregistered policy/scenario",
                )
                require(
                    data["quantity"] == cfg["quantity_per_side"] and data["style"] == "maker",
                    "Unregistered size/style",
                )
                slot = parse_time(data["scheduled_at"])
                require(
                    parse_time(cfg["first_decision_at"]) <= slot <= parse_time(cfg["last_decision_at"]),
                    "Unregistered date",
                )
                require(
                    (slot - parse_time(cfg["first_decision_at"])).total_seconds()
                    % cfg["round_interval_seconds"]
                    == 0,
                    "Unregistered slot",
                )
                require(
                    0 <= (at - slot).total_seconds() <= cfg["maximum_slot_lateness_seconds"],
                    "Late/backdated decision",
                )
                require(
                    (parse_time(data["arrival_due_at"]) - at).total_seconds()
                    == data["scenario"]["latency_seconds"],
                    "Changed minimum latency",
                )
                check_context(data, data["context_record_ids"], at)
                levels, _, received = book(data["decision_book_record_id"], data["ticker"])
                require(0 <= (at - received).total_seconds() <= 5, "Future/stale decision book")
                y, n = levels["yes"][0][0], levels["no"][0][0]
                require(D(".02") <= 1 - y - n <= D(".20"), "Spread filter differs")
                net = (
                    quantities[(data["account"], data["ticker"], "yes")]
                    - quantities[(data["account"], data["ticker"], "no")]
                )
                require(
                    net == D(data["net_inventory_at_submission"]),
                    "Inventory quote used future or wrong fills",
                )
                improve = D(0) if policy == "join" else D(".01")
                skew = max(D("-.03"), min(D(".03"), net * D(".01"))) if policy == "inventory_skew" else D(0)
                expected = (
                    min(y + improve - skew, 1 - n - D(".01"))
                    if data["side"] == "yes"
                    else min(n + improve + skew, 1 - y - D(".01"))
                )
                require(
                    expected.quantize(D(".01"), rounding=ROUND_FLOOR) == D(data["limit"]),
                    "Quote differs from prior inventory/book",
                )
            elif kind == "arrival":
                order = state["orders"][data["order_id"]]
                check_context(order, data["context_record_ids"], at)
                levels, started, received = book(data["book_record_id"], order["ticker"])
                require(
                    started >= parse_time(order["arrival_due_at"]) and received == at,
                    "Pre-delay or backdated arrival",
                )
                opposite = "no" if order["side"] == "yes" else "yes"
                require(
                    not levels[opposite] or D(order["limit"]) < 1 - levels[opposite][0][0],
                    "Post-only arrival crossed",
                )
                ahead = sum((q for p, q in levels[order["side"]] if p >= D(order["limit"])), D(0)) * D(
                    order["scenario"]["queue_multiplier"]
                )
                require(ahead == D(data["queue_ahead"]), "Queue differs from raw depth/stress")
                latencies[order["scenario"]["name"]].append(
                    (at - parse_time(order["submitted_at"])).total_seconds()
                )
            elif kind == "queue":
                order = state["orders"][data["order_id"]]
                source, body = raw(data["source_record_id"])
                require(source["kind"] == "maker_trades", "Wrong tape namespace")
                trade = next(t for t in body["trades"] if t["trade_id"] == data["trade_id"])
                require(data["source_record_id"] in data["window_record_ids"], "Missing window lineage")
                require(
                    max(parse_time(raw(r)[0]["available_at"]) for r in data["window_record_ids"]) == at,
                    "Tape window backdated",
                )
                direction = "no" if order["side"] == "yes" else "yes"
                require(
                    trade["ticker"] == order["ticker"]
                    and trade.get("is_block_trade") is False
                    and trade["taker_outcome_side"] == direction
                    and trade["taker_book_side"] == ("bid" if direction == "yes" else "ask"),
                    "Wrong direction/block/ticker",
                )
                require(
                    parse_time(order["arrived_at"])
                    < parse_time(trade["created_time"])
                    <= at
                    < parse_time(order["expires_at"]),
                    "Fill outside resting window",
                )
                require(
                    D(trade["yes_price_dollars"]) + D(trade["no_price_dollars"]) == 1
                    and D(trade[order["side"] + "_price_dollars"]) < D(order["limit"]),
                    "Not a complementary strict trade-through",
                )
                ahead, volume = D(order["queue_ahead"]), D(trade["count_fp"])
                require(
                    volume > 0 and max(D(0), ahead - volume) == D(data["queue_ahead"]),
                    "Queue depletion differs",
                )
                allowed = (max(D(0), volume - ahead) * D(order["scenario"]["trade_participation"])).quantize(
                    D(".01"), rounding=ROUND_FLOOR
                )
                expected_fill[(order["id"], trade["trade_id"])] = min(D(order["remaining"]), allowed)
            elif kind == "fill":
                order = state["orders"][data["order_id"]]
                key = (order["id"], data["fill_id"].removeprefix("trade:"))
                quantity, price = D(data["quantity"]), D(data["price"])
                require(
                    expected_fill.pop(key, None) == quantity and price == D(order["limit"]),
                    "Fill exceeds tape/queue/limit",
                )
                fee_type = order["fee_schedule"]["fee_type"]
                require(fee_type in ("quadratic", "quadratic_with_maker_fees"), "Unknown fee")
                coefficient = D(".0175") if fee_type == "quadratic_with_maker_fees" else D(0)
                trade_fee = (
                    coefficient * D(order["fee_schedule"]["multiplier"]) * price * (1 - price) * quantity
                ).quantize(D(".000001"), rounding=ROUND_CEILING)
                precision = D(order["fee_schedule"]["balance_precision"])
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
            elif kind == "settlement":
                source, body = raw(data["source_record_id"])
                market = body["market"]
                require(
                    source["kind"] == "maker_settlement" and parse_time(source["available_at"]) <= at,
                    "Future/wrong settlement source",
                )
                require(
                    market["status"] == "finalized" and market["result"] in ("yes", "no"),
                    "Unfinalized/nonbinary payout",
                )
                originals = [o for o in state["orders"].values() if o["ticker"] == market["ticker"]]
                for order in originals:
                    require(
                        all(market.get(k) == v for k, v in order["market_predicate"].items()),
                        "Final predicate differs",
                    )
                label = int(market["result"] == "yes")
                require(data["labels"] == {market["ticker"]: label}, "Wrong raw outcome")
                for key in list(quantities):
                    account, ticker, side = key
                    if ticker == market["ticker"]:
                        cash[account] += quantities.pop(key) * (label if side == "yes" else 1 - label)
                        costs.pop(key, None)
            state = reduce_event(state, event)
        for account, a in state["accounts"].items():
            require(
                D(a["cash"]) == cash[account] and D(a["fees"]) == fees[account],
                "Cash/fees failed independent replay",
            )
        for p in state["positions"].values():
            key = (p["account"], p["ticker"], p["side"])
            require(
                D(p["quantity"]) == quantities[key] and D(p["cost"]) == costs[key],
                "Position differs from raw fills",
            )
        result = {
            "generated_at": iso(utcnow()),
            "run_record_id": run_id,
            "last_journal_record_id": records[-1]["id"] if records else None,
            "journal_events": len(records),
            "event_counts": dict(counts),
            "raw_records_checked": len(cache),
            "actual_arrival_seconds": {
                k: {"n": len(v), "min": min(v), "max": max(v), "mean": sum(v) / len(v)}
                for k, v in latencies.items()
            },
            "cash_fees_positions_reproduced": True,
            "quotes_reproduced_from_prior_books_and_inventory": True if counts["order"] else None,
            "future_queue_and_tape_fills_reproduced": True if counts["fill"] else None,
            "network_requests": 0,
            "real_money_orders": 0,
            "profitability_proven": False,
            "scope_limit": "Independent arithmetic and source-lineage audit. Not actual exchange fills or independent trading days; absence of omitted eligible signals is not proven by this replay.",
        }
        Path("reports/E015_audit.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int, default=42731)
    main(parser.parse_args().run_record_id)
