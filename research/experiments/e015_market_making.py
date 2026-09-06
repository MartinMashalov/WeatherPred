"""Registered two-sided maker feasibility run. Public GETs and paper journal only."""

import argparse
import fcntl
import hashlib
import json
import time
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book
from weatherpred.fees import FeeAccumulator
from weatherpred.http import PublicClient
from weatherpred.market_making import inventory_report, net_inventory, passive_quotes, predicate
from weatherpred.paper import OPEN, PaperLedger, maker_trade, reduce_event, schedule_for
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal
CONFIG = Path("config/e015_market_making.json")


def record(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("Missing E015 evidence record")
    return row


def fee_dict(schedule):
    return {
        "fee_type": schedule.fee_type,
        "multiplier": str(schedule.multiplier),
        "balance_precision": str(schedule.balance_precision),
    }


def context(client, member):
    event, event_id = client.json(
        "/events/" + member["event_ticker"], kind="maker_context", key=member["event_ticker"]
    )
    current = next(m for m in event["markets"] if m["ticker"] == member["ticker"])
    if predicate(current) != predicate(member):
        raise ValueError("Maker contract predicates changed")
    series, series_id = client.json(
        "/series/" + member["series_ticker"], kind="maker_series", key=member["series_ticker"]
    )
    changes, refs = [], [event_id, series_id]
    for page, identifier in client.pages(
        "/events/fee_changes",
        "event_fee_changes",
        {"event_ticker": member["event_ticker"], "limit": 1000},
        kind="maker_fees",
        key=member["event_ticker"],
    ):
        changes.extend(page)
        refs.append(identifier)
    now = utcnow()
    upcoming = [c["scheduled_ts"] for c in changes if parse_time(c["scheduled_ts"]) > now]
    current = {**current, "maker_fee_valid_until": min(upcoming, key=parse_time) if upcoming else None}
    return current, resolve_current_fee(series["series"], changes, now), refs


def books(client, tickers):
    data, identifier = client.json(
        "/markets/orderbooks", [("tickers", t) for t in sorted(tickers)], kind="maker_books", key="E015"
    )
    source = record(client.archive, identifier)
    meta = json.loads(source["metadata"])
    received, started = parse_time(source["available_at"]), parse_time(meta["request_started_at"])
    headers = meta["headers"]
    server = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    if (
        server is None
        or not -2 <= (received - server).total_seconds() <= 5
        or float(headers.get("age", 0)) > 5
        or (received - started).total_seconds() > 5
    ):
        raise ValueError("Stale/undated/slow maker book")
    parsed = {b["ticker"]: parse_book(b) for b in data["orderbooks"]}
    if set(parsed) != set(tickers) or len(parsed) != len(data["orderbooks"]):
        raise ValueError("Maker book membership incomplete/duplicated")
    return parsed, identifier, started, received


def register(client):
    archive = client.archive
    config = json.loads(CONFIG.read_text())
    first = parse_time(config["first_decision_at"])
    if utcnow() >= first or archive.latest("experiment_protocol", config["experiment"]):
        raise ValueError("Registration deadline passed or run already exists; do not silently re-register")
    panel, selection, refs = [], [], []
    for series in config["series"]:
        candidates = []
        for page, identifier in client.pages(
            "/markets",
            "markets",
            {"series_ticker": series, "status": "open", "limit": 1000},
            kind="maker_universe",
            key=series,
        ):
            refs.append(identifier)
            candidates.extend(page)
        active = [
            m
            for m in candidates
            if m["status"] == "active"
            and first + timedelta(minutes=30) <= parse_time(m["close_time"]) <= first + timedelta(hours=36)
        ]
        if not active:
            selection.append(
                {"series": series, "candidate_count": len(candidates), "reason": "no_eligible_event"}
            )
            continue
        earliest = min((parse_time(m["close_time"]), m["event_ticker"]) for m in active)[1]
        eligible = []
        for m in active:
            if (
                m["event_ticker"] != earliest
                or m.get("market_type") != "binary"
                or D(m.get("notional_value_dollars", "0")) != 1
            ):
                continue
            bid, ask = D(m.get("yes_bid_dollars", "0")), D(m.get("yes_ask_dollars", "0"))
            if (
                D("0.10") <= bid < ask <= D("0.90")
                and D("0.02") <= ask - bid <= D("0.20")
                and m.get("price_ranges")
            ):
                eligible.append(m)
        chosen = sorted(eligible, key=lambda m: (-D(m["volume_fp"]), m["ticker"]))
        selection.append(
            {
                "series": series,
                "event": earliest,
                "candidate_count": len(candidates),
                "eligible_tickers": [m["ticker"] for m in chosen],
                "chosen": chosen[0]["ticker"] if chosen else None,
            }
        )
        if chosen:
            panel.append({**chosen[0], "series_ticker": series})
    if not panel or utcnow() >= first:
        raise ValueError("Empty panel or registration missed deadline")
    paths = [
        Path(__file__),
        CONFIG,
        *[
            Path("weatherpred") / name
            for name in (
                "market_making.py",
                "paper.py",
                "fees.py",
                "books.py",
                "basket.py",
                "http.py",
                "archive.py",
                "timeutil.py",
            )
        ],
    ]
    hashes, sources = {}, {}
    for path in paths:
        body = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(body).hexdigest()
        sources[str(path)] = archive.append("research_source", str(path), utcnow(), {}, body)
    protocol = {
        "config": config,
        "registered_at": iso(utcnow()),
        "panel": panel,
        "selection": selection,
        "universe_record_ids": refs,
        "code_sha256": hashes,
        "source_record_ids": sources,
    }
    identifier = archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(protocol).encode()
    )
    Path("reports/E015_registration.json").write_text(
        json.dumps({"run_record_id": identifier, **protocol}, indent=2)
    )
    return identifier, protocol


def cancel(ledger, order, reason):
    if ledger.state["orders"][order["id"]]["status"] in OPEN:
        ledger.emit("cancel", {"order_id": order["id"], "reason": reason})


def submit_round(client, ledger, protocol, slot):
    cfg = protocol["config"]
    if (utcnow() - slot).total_seconds() > cfg["maximum_slot_lateness_seconds"]:
        ledger.emit("abstain", {"scheduled_at": iso(slot), "reason": "slot_missed_no_retroactive_quotes"})
        ledger.emit("slot", {"scheduled_at": iso(slot)})
        return
    # Rotate panel priority deterministically so cash limits do not always favor
    # the first city. The entire panel and all rotation dates were frozen.
    panel = protocol["panel"]
    offset = int(
        (slot - parse_time(cfg["first_decision_at"])).total_seconds() // cfg["round_interval_seconds"]
    ) % len(panel)
    for member in panel[offset:] + panel[:offset]:
        if utcnow() + timedelta(seconds=90) >= parse_time(member["close_time"]):
            continue
        try:
            current, schedule, refs = context(client, member)
            if current["status"] != "active":
                raise ValueError("Market not active")
            snapshot, book_id, _, received = books(client, [member["ticker"]])
            book = snapshot[member["ticker"]]
            for policy in cfg["policies"]:
                for scenario in cfg["scenarios"]:
                    account = policy + ":" + scenario["name"]
                    prices = passive_quotes(
                        book,
                        current,
                        policy,
                        net_inventory(ledger.state, account, member["ticker"]),
                        schedule,
                    )
                    if prices is None:
                        ledger.emit(
                            "abstain",
                            {
                                "account": account,
                                "ticker": member["ticker"],
                                "scheduled_at": iso(slot),
                                "book_record_id": book_id,
                                "reason": "no_fee_positive_valid_pair_quote",
                            },
                        )
                        continue
                    now = utcnow()
                    if (now - received).total_seconds() > 5 or (now - slot).total_seconds() > cfg[
                        "maximum_slot_lateness_seconds"
                    ]:
                        ledger.emit(
                            "abstain",
                            {
                                "account": account,
                                "ticker": member["ticker"],
                                "reason": "stale_decision_or_slot",
                            },
                        )
                        continue
                    due = now + timedelta(seconds=scenario["latency_seconds"])
                    expires = min(
                        due + timedelta(seconds=cfg["order_ttl_seconds"]),
                        parse_time(member["close_time"]) - timedelta(seconds=1),
                    )
                    if current["maker_fee_valid_until"] and expires >= parse_time(
                        current["maker_fee_valid_until"]
                    ):
                        ledger.emit(
                            "abstain",
                            {
                                "account": account,
                                "ticker": member["ticker"],
                                "reason": "fee_change_during_order_window",
                                "context_record_ids": refs,
                            },
                        )
                        continue
                    proposed = []
                    for side, price in prices.items():
                        proposed.append(
                            {
                                "id": f"{iso(slot)}:{account}:{member['ticker']}:{side}",
                                "account": account,
                                "model": policy,
                                "scenario": scenario,
                                "style": "maker",
                                "side": side,
                                "event": member["event_ticker"],
                                "ticker": member["ticker"],
                                "floor_strike": member.get("floor_strike"),
                                "quantity": cfg["quantity_per_side"],
                                "limit": str(price),
                                "submitted_at": iso(now),
                                "arrival_due_at": iso(due),
                                "expires_at": iso(expires),
                                "close_at": member["close_time"],
                                "fee_schedule": fee_dict(schedule),
                                "decision_book_record_id": book_id,
                                "context_record_ids": refs,
                                "market_predicate": predicate(member),
                                "scheduled_at": iso(slot),
                                "net_inventory_at_submission": str(
                                    net_inventory(ledger.state, account, member["ticker"])
                                ),
                            }
                        )
                    try:
                        trial = ledger.state
                        for order in proposed:
                            trial = reduce_event(trial, {"type": "order", "data": order, "at": iso(now)})
                    except ValueError as exc:
                        ledger.emit(
                            "abstain",
                            {
                                "account": account,
                                "ticker": member["ticker"],
                                "scheduled_at": iso(slot),
                                "reason": str(exc),
                                "proposed": proposed,
                            },
                        )
                        continue
                    for order in proposed:
                        ledger.emit("order", order, now)
        except (httpx.HTTPError, ValueError, KeyError, StopIteration) as exc:
            ledger.emit("error", {"phase": "decision", "ticker": member["ticker"], "error": str(exc)})
    ledger.emit("slot", {"scheduled_at": iso(slot)})


def arrivals(client, ledger, protocol):
    pending = [
        o
        for o in ledger.state["orders"].values()
        if o["status"] == "pending" and parse_time(o["arrival_due_at"]) <= utcnow()
    ]
    by_ticker = {m["ticker"]: m for m in protocol["panel"]}
    for ticker in sorted({o["ticker"] for o in pending}):
        orders = [o for o in pending if o["ticker"] == ticker]
        try:
            market, schedule, refs = context(client, by_ticker[ticker])
            snapshot, book_id, started, received = books(client, [ticker])
            book = snapshot[ticker]
            for order in orders:
                if (
                    received >= parse_time(order["expires_at"])
                    or market["status"] != "active"
                    or fee_dict(schedule) != order["fee_schedule"]
                    or (
                        market["maker_fee_valid_until"]
                        and parse_time(order["expires_at"]) >= parse_time(market["maker_fee_valid_until"])
                    )
                ):
                    cancel(ledger, order, "expired_inactive_or_changed_fee")
                    continue
                if started < parse_time(order["arrival_due_at"]):
                    raise ValueError("Arrival book requested before registered delay")
                opposite = "no" if order["side"] == "yes" else "yes"
                if book[opposite] and D(order["limit"]) >= 1 - book[opposite][0][0]:
                    cancel(ledger, order, "post_only_would_cross")
                    continue
                ahead = sum((q for p, q in book[order["side"]] if p >= D(order["limit"])), D(0)) * D(
                    order["scenario"]["queue_multiplier"]
                )
                ledger.emit(
                    "arrival",
                    {
                        "order_id": order["id"],
                        "book_record_id": book_id,
                        "queue_ahead": str(ahead),
                        "context_record_ids": refs,
                    },
                    received,
                )
        except (httpx.HTTPError, ValueError, KeyError, StopIteration) as exc:
            ledger.emit("error", {"phase": "arrival", "ticker": ticker, "error": str(exc)})
            for order in orders:
                cancel(ledger, order, "arrival_evidence_unavailable")


def tape(client, ledger):
    resting = [o for o in ledger.state["orders"].values() if o["status"] == "resting"]
    for ticker in sorted({o["ticker"] for o in resting}):
        orders = [o for o in resting if o["ticker"] == ticker]
        try:
            since = min(parse_time(o["arrived_at"]) for o in orders)
            pages = []
            for page, identifier in client.pages(
                "/markets/trades",
                "trades",
                {
                    "ticker": ticker,
                    "min_ts": int(since.timestamp()),
                    "max_ts": int(utcnow().timestamp()),
                    "limit": 1000,
                    "is_block_trade": "false",
                },
                kind="maker_trades",
                key=ticker,
            ):
                pages.append((page, identifier))
                if len(pages) > 10:
                    raise ValueError("Tape pagination exceeds bounded window")
            # Replay chronological prints only after the whole window has been
            # received. Using each page's earlier receipt would backdate events
            # when a newer page follows older prints in this sort.
            received = max(parse_time(record(client.archive, rid)["available_at"]) for _, rid in pages)
            trades = sorted(
                [(t, rid) for page, rid in pages for t in page],
                key=lambda x: (parse_time(x[0]["created_time"]), x[0]["trade_id"]),
            )
            for trade, identifier in trades:
                for old in orders:
                    order = ledger.state["orders"][old["id"]]
                    if order["status"] != "resting":
                        continue
                    advance = maker_trade(order, trade, received, order["scenario"]["trade_participation"])
                    if advance is None:
                        continue
                    ledger.emit(
                        "queue",
                        {
                            "order_id": order["id"],
                            "queue_ahead": advance["queue_ahead"],
                            "trade_id": trade["trade_id"],
                            "source_record_id": identifier,
                            "window_record_ids": [rid for _, rid in pages],
                        },
                        received,
                    )
                    if D(advance["quantity"]) > 0:
                        ledger.emit(
                            "fill",
                            {
                                "order_id": order["id"],
                                "quantity": advance["quantity"],
                                "price": order["limit"],
                                "fill_id": "trade:" + trade["trade_id"],
                                "evidence_at": trade["created_time"],
                                "source_record_id": identifier,
                                "window_record_ids": [rid for _, rid in pages],
                            },
                            received,
                        )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            ledger.emit("error", {"phase": "tape", "ticker": ticker, "error": str(exc)})
            for order in orders:
                cancel(ledger, order, "ambiguous_or_missing_tape")


def settlements(client, ledger, protocol):
    for member in protocol["panel"]:
        if member["event_ticker"] in ledger.state["settled_events"] or utcnow() < parse_time(
            member["close_time"]
        ):
            continue
        try:
            data, identifier = client.json(
                "/markets/" + member["ticker"], kind="maker_settlement", key=member["ticker"]
            )
            market = data["market"]
            if predicate(market) != predicate(member):
                raise ValueError("Final contract predicate changed")
            if market["status"] != "finalized":
                continue
            if market.get("result") not in ("yes", "no"):
                raise ValueError("Nonbinary/discretionary final outcome")
            for order in list(ledger.state["orders"].values()):
                if order["event"] == member["event_ticker"]:
                    cancel(ledger, order, "finalized")
            ledger.emit(
                "settlement",
                {
                    "event": member["event_ticker"],
                    "labels": {member["ticker"]: int(market["result"] == "yes")},
                    "source_record_id": identifier,
                },
            )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            ledger.emit("error", {"phase": "settlement", "ticker": member["ticker"], "error": str(exc)})


def mark(client, ledger):
    tickers = {p["ticker"] for p in ledger.state["positions"].values()}
    if not tickers:
        return
    try:
        snapshot, identifier, _, received = books(client, tickers)
        for account, a in ledger.state["accounts"].items():
            equity = D(a["cash"])
            report = inventory_report(ledger.state, account)
            for row in report["positions"]:
                # Normal binary paired payout is locked, not available cash.
                equity += D(row["paired_quantity"])
                net = D(row["net_yes_quantity"])
                side = "yes" if net > 0 else "no"
                remaining = abs(net)
                order = next(
                    o
                    for o in ledger.state["orders"].values()
                    if o["account"] == account and o["ticker"] == row["ticker"]
                )
                fee = FeeAccumulator(schedule_for(order))
                for price, depth in snapshot[row["ticker"]][side]:
                    quantity = min(remaining, depth)
                    if quantity <= 0:
                        break
                    equity += max(D(0), fee.fill(price, quantity, sell=True)["balance_change"])
                    remaining -= quantity
            ledger.emit(
                "mark",
                {
                    "account": account,
                    "equity": str(equity),
                    "book_record_id": identifier,
                    "paired_value_is_not_cash": True,
                },
                received,
            )
        for order in list(ledger.state["orders"].values()):
            if ledger.state["accounts"][order["account"]]["halted"]:
                cancel(ledger, order, "marked_drawdown_halt")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        ledger.emit("error", {"phase": "mark", "error": str(exc)})


def report(ledger, protocol):
    state = ledger.state
    output = {
        "generated_at": iso(utcnow()),
        "run_record_id": int(ledger.run_id),
        "panel_tickers": [m["ticker"] for m in protocol["panel"]],
        "slots": len(state["slots"]),
        "orders": len(state["orders"]),
        "fill_records": len(state["fills"]),
        "order_status": dict(Counter(o["status"] for o in state["orders"].values())),
        "events_with_fills": sorted({o["event"] for o in state["orders"].values() if o["fill_ids"]}),
        "settled_events": state["settled_events"],
        "accounts": [inventory_report(state, a) for a in state["accounts"]],
        "real_money_orders": 0,
        "profitability_proven": False,
    }
    Path("reports/E015_market_making.json").write_text(json.dumps(output, indent=2))
    return output


def main(run_id=None, register_only=False, settle_only=False):
    archive = Archive()
    client = PublicClient(archive, interval=0.15)
    try:
        with (archive.root / "market_making.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if run_id is None:
                run_id, protocol = register(client)
            else:
                protocol = archive.json(record(archive, run_id))
            for path, expected in protocol["code_sha256"].items():
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                    raise ValueError("Frozen E015 source changed: " + path)
            print(
                json.dumps(
                    {
                        "run_record_id": run_id,
                        "panel": [m["ticker"] for m in protocol["panel"]],
                        "first_decision_at": protocol["config"]["first_decision_at"],
                    }
                ),
                flush=True,
            )
            if register_only:
                return
            cfg = protocol["config"]
            ledger = PaperLedger(archive, run_id)
            if not ledger.state["accounts"]:
                for policy in cfg["policies"]:
                    for scenario in cfg["scenarios"]:
                        ledger.emit(
                            "account",
                            {"account": policy + ":" + scenario["name"], "initial_cash": cfg["initial_cash"]},
                        )
            for order in list(ledger.state["orders"].values()):
                cancel(ledger, order, "restart_no_gap_fills")
            if settle_only:
                settlements(client, ledger, protocol)
                print(json.dumps(report(ledger, protocol)), flush=True)
                return
            slots, slot = [], parse_time(cfg["first_decision_at"])
            while slot <= parse_time(cfg["last_decision_at"]):
                slots.append(slot)
                slot += timedelta(seconds=cfg["round_interval_seconds"])
            next_monitor = utcnow()
            while utcnow() < parse_time(cfg["stop_at"]):
                if (archive.root / "STOP_MARKET_MAKING").exists():
                    break
                for order in list(ledger.state["orders"].values()):
                    if utcnow() >= parse_time(order["expires_at"]):
                        cancel(ledger, order, "venue_expiry")
                due = [s for s in slots if s <= utcnow() and iso(s) not in ledger.state["slots"]]
                for slot in due:
                    submit_round(client, ledger, protocol, slot)
                arrivals(client, ledger, protocol)
                tape(client, ledger)
                if utcnow() >= next_monitor:
                    settlements(client, ledger, protocol)
                    mark(client, ledger)
                    output = report(ledger, protocol)
                    print(
                        json.dumps(
                            {
                                k: output[k]
                                for k in ("generated_at", "slots", "orders", "fill_records", "settled_events")
                            }
                        ),
                        flush=True,
                    )
                    next_monitor = utcnow() + timedelta(seconds=60)
                time.sleep(cfg["poll_seconds"])
            for order in list(ledger.state["orders"].values()):
                cancel(ledger, order, "bounded_stop_or_kill_switch")
            ledger.emit(
                "stopped",
                {
                    "reason": "bounded_stop_or_kill_switch",
                    "pending_positions": len(ledger.state["positions"]),
                },
            )
            report(ledger, protocol)
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--settle-only", action="store_true")
    args = parser.parse_args()
    main(args.run_record_id, args.register_only, args.settle_only)
