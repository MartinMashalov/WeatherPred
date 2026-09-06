"""Bounded prospective paper broker, public GET only, with replayable account events."""

import argparse
import fcntl
import hashlib
import json
import logging
import time
from collections import Counter
from datetime import timedelta
from decimal import ROUND_FLOOR, Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book, purchase_cost
from weatherpred.fees import FeeAccumulator
from weatherpred.freshness import paired_forecast
from weatherpred.http import PublicClient
from weatherpred.paper import OPEN, PaperLedger, exposure, maker_trade, reserve_unit, reserved, taker_slices
from weatherpred.report import dashboard
from weatherpred.shadow import forecast_snapshot, validate_lineage
from weatherpred.shadow_outcomes import finalized_labels
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal
LOG = logging.getLogger(__name__)


def record(archive, record_id):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone()
    if row is None:
        raise ValueError("Missing archived source")
    return row


def fresh_books(client, tickers):
    data, rec = client.json(
        "/markets/orderbooks", [("tickers", t) for t in sorted(tickers)], kind="paper_books", key="E009"
    )
    source = record(client.archive, rec)
    metadata = json.loads(source["metadata"])
    received = parse_time(source["available_at"])
    started = parse_time(metadata["request_started_at"])
    headers = metadata["headers"]
    server = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    if (
        server is None
        or not -2 <= (received - server).total_seconds() <= 5
        or float(headers.get("age", 0)) > 5
        or (received - started).total_seconds() > 5
    ):
        raise ValueError("Stale, slow or undated paper arrival book")
    books = {b["ticker"]: parse_book(b) for b in data["orderbooks"]}
    if set(books) != set(tickers) or len(books) != len(data["orderbooks"]):
        raise ValueError("Incomplete or duplicate paper book membership")
    return books, rec, started, received


def current_context(client, event):
    data, event_rec = client.json("/events/" + event, kind="paper_market_state", key=event)
    series, series_rec = client.json("/series/KXTEMPMIAH", kind="paper_series", key="KXTEMPMIAH")
    changes, refs = [], [event_rec, series_rec]
    for page, rec in client.pages(
        "/events/fee_changes",
        "event_fee_changes",
        {"event_ticker": event, "limit": 1000},
        kind="paper_fees",
        key=event,
    ):
        changes.extend(page)
        refs.append(rec)
    return data, resolve_current_fee(series["series"], changes, utcnow()), refs


def fee_dict(schedule):
    return {
        "fee_type": schedule.fee_type,
        "multiplier": str(schedule.multiplier),
        "balance_precision": str(schedule.balance_precision),
    }


def register(archive):
    config = json.loads(Path("config/e009_paper.json").read_text())
    if utcnow() >= parse_time(config["first_decision_at"]):
        raise ValueError("Paper registration deadline passed; register a new future run")
    if archive.latest("experiment_protocol", config["experiment"]):
        raise ValueError("Paper run already registered; use --run-record-id")
    slots = []
    first, last = parse_time(config["first_decision_at"]), parse_time(config["last_decision_at"])
    hour = first.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    while hour - timedelta(minutes=30) <= last:
        for horizon in config["horizons_minutes"]:
            decision = hour - timedelta(minutes=horizon)
            if first <= decision <= last:
                slots.append(
                    {"decision_at": iso(decision), "settlement_at": iso(hour), "horizon_minutes": horizon}
                )
        hour += timedelta(hours=1)
    paths = [
        Path(__file__),
        Path("config/e009_paper.json"),
        *[
            Path("weatherpred") / name
            for name in (
                "paper.py",
                "fees.py",
                "books.py",
                "shadow.py",
                "freshness.py",
                "forecasts.py",
                "index.py",
                "shadow_outcomes.py",
                "basket.py",
                "http.py",
                "report.py",
            )
        ],
    ]
    sources, hashes = {}, {}
    for path in paths:
        body = path.read_bytes()
        sources[str(path)] = archive.append("research_source", str(path), utcnow(), {}, body)
        hashes[str(path)] = hashlib.sha256(body).hexdigest()
    old = archive.json(record(archive, config["fresh_parameter_record_id"]))
    for path in ("weatherpred/forecasts.py", "weatherpred/index.py", "weatherpred/freshness.py"):
        if old["code_sha256"][path] != hashes[path]:
            raise ValueError("Frozen probability implementation has changed")
    reuse = {
        **old,
        "slots": slots,
        "protocol": {**old["protocol"], "experiment": "E009_fresh_reuse"},
        "parameters_reused_from": config["fresh_parameter_record_id"],
        "model_refits": 0,
        "published_at": iso(utcnow()),
    }
    fresh_id = archive.append("model_artifact", "E009_fresh_reuse", utcnow(), {}, canonical(reuse).encode())
    result = {
        "config": config,
        "slots": slots,
        "fresh_model_record_id": fresh_id,
        "source_record_ids": sources,
        "code_sha256": hashes,
        "registered_at": iso(utcnow()),
    }
    run_id = archive.append(
        "experiment_protocol", config["experiment"], utcnow(), {}, canonical(result).encode()
    )
    paper = PaperLedger(archive, run_id)
    for model in config["models"]:
        for scenario in config["scenarios"]:
            paper.emit(
                "account",
                {
                    "account": model + ":" + scenario["name"],
                    "initial_cash": config["initial_cash_per_account"],
                },
            )
    Path("reports/E009_registration.json").write_text(json.dumps({**result, "record_id": run_id}, indent=2))
    return run_id


def source_book_and_fee(archive, forecast):
    refs = [record(archive, rec) for rec in forecast["evidence_record_ids"]]
    books = [r for r in refs if r["kind"] == "shadow_books"]
    series = [r for r in refs if r["kind"] == "shadow_series"]
    changes = [
        change for r in refs if r["kind"] == "shadow_fees" for change in archive.json(r)["event_fee_changes"]
    ]
    if len(books) != 1 or len(series) != 1:
        raise ValueError("Ambiguous decision book or fee source")
    return (
        {b["ticker"]: parse_book(b) for b in archive.json(books[0])["orderbooks"]},
        resolve_current_fee(archive.json(series[0])["series"], changes, parse_time(forecast["published_at"])),
        books[0]["id"],
    )


def make_orders(paper, forecast, forecast_id, config):
    archive = paper.archive
    books, schedule, book_id = source_book_and_fee(archive, forecast)
    forecast_time = parse_time(forecast["published_at"])
    validate_lineage(
        archive,
        forecast["evidence_record_ids"],
        forecast_time,
        parse_time(forecast["settlement_at"]),
        forecast["model_record_id"],
    )
    for model in config["models"]:
        for scenario in config["scenarios"]:
            account_id = model + ":" + scenario["name"]
            account = paper.state["accounts"][account_id]
            if account["halted"]:
                paper.emit(
                    "abstain",
                    {"account": account_id, "reason": "drawdown_halt", "forecast_record_id": forecast_id},
                )
                continue
            candidates = []
            for market in forecast["markets"]:
                book = books[market["ticker"]]
                if not book["yes"] or not book["no"]:
                    continue
                for side in ("yes", "no"):
                    p = D(str(market["probabilities"][model]))
                    p = (p if side == "yes" else 1 - p) - D("0.03")
                    if p <= 0:
                        continue
                    limit = min(D("0.99"), p.quantize(D("0.01"), rounding=ROUND_FLOOR))
                    while (
                        limit > 0
                        and -FeeAccumulator(schedule).fill(limit, 1, maker=scenario["style"] == "maker")[
                            "balance_change"
                        ]
                        > p
                    ):
                        limit -= D("0.01")
                    if limit <= 0:
                        continue
                    if scenario["style"] == "maker":
                        ask = 1 - book["no" if side == "yes" else "yes"][0][0]
                        limit = min(limit, book[side][0][0] + D("0.01"), ask - D("0.01")).quantize(
                            D("0.01"), rounding=ROUND_FLOOR
                        )
                        if limit <= 0:
                            continue
                        cost = -FeeAccumulator(schedule).fill(limit, 1, maker=True)["balance_change"]
                    else:
                        quoted = purchase_cost(
                            book, side, 1, schedule, scenario["depth_retained"], scenario["slippage"]
                        )
                        if quoted is None or quoted.worst_price > limit:
                            continue
                        cost = quoted.total
                    if p > cost and cost < 1:
                        candidates.append(
                            {
                                "market": market,
                                "side": side,
                                "p": p,
                                "cost": cost,
                                "limit": limit,
                                "edge": p - cost,
                            }
                        )
            candidates.sort(key=lambda c: (-c["edge"], c["market"]["ticker"], c["side"]))
            if not candidates:
                paper.emit(
                    "abstain",
                    {
                        "account": account_id,
                        "reason": "no_edge_after_probability_haircut_and_cost",
                        "forecast_record_id": forecast_id,
                    },
                )
                continue
            choice = candidates[0]
            cash = D(account["cash"])
            equity = min(
                D(account["marked_equity"]),
                cash + exposure(paper.state, account_id) - reserved(paper.state, account_id),
            )
            kelly = D("0.25") * choice["edge"] / (1 - choice["cost"])
            budget = min(
                cash - reserved(paper.state, account_id),
                equity * kelly,
                equity * D("0.05") - exposure(paper.state, account_id, forecast["event"]),
                equity * D("0.10") - exposure(paper.state, account_id),
            )
            submitted = utcnow()
            due = submitted + timedelta(seconds=scenario["latency_seconds"])
            expires = min(
                due + timedelta(seconds=60), parse_time(forecast["settlement_at"]) - timedelta(seconds=1)
            )
            order = {
                "id": forecast["scheduled_at"] + ":" + account_id,
                "account": account_id,
                "event": forecast["event"],
                "ticker": choice["market"]["ticker"],
                "side": choice["side"],
                "style": scenario["style"],
                "limit": str(choice["limit"]),
                "submitted_at": iso(submitted),
                "arrival_due_at": iso(due),
                "expires_at": iso(expires),
                "close_at": forecast["settlement_at"],
                "floor_strike": choice["market"]["floor_strike"],
                "fee_schedule": fee_dict(schedule),
                "forecast_record_id": forecast_id,
                "decision_book_record_id": book_id,
                "model": model,
                "scenario": scenario,
                "haircut_probability": str(choice["p"]),
                "estimated_unit_edge": str(choice["edge"]),
                "kelly_fraction": str(kelly),
            }
            quantity = (max(D(0), budget) / reserve_unit(order)).to_integral_value(rounding=ROUND_FLOOR)
            if quantity < 1 or expires <= due:
                paper.emit(
                    "abstain",
                    {
                        "account": account_id,
                        "reason": "risk_budget_below_one_contract",
                        "forecast_record_id": forecast_id,
                    },
                )
                continue
            order["quantity"] = str(quantity)
            paper.emit("order", order, submitted)
    paper.emit("slot", {"scheduled_at": forecast["scheduled_at"], "forecast_record_id": forecast_id})


def arrival_orders(client, paper):
    due = [
        o
        for o in paper.state["orders"].values()
        if o["status"] == "pending" and parse_time(o["arrival_due_at"]) <= utcnow()
    ]
    if not due:
        return
    for event in sorted({o["event"] for o in due}):
        group = [o for o in due if o["event"] == event]
        try:
            data, schedule, context_refs = current_context(client, event)
            market_map = {m["ticker"]: m for m in data["markets"]}
            books, rec, started, received = fresh_books(client, {o["ticker"] for o in group})
            for order in group:
                market = market_map[order["ticker"]]
                if (
                    parse_time(order["arrival_due_at"]) > started
                    or (received - parse_time(order["arrival_due_at"])).total_seconds() > 15
                    or received >= parse_time(order["expires_at"])
                    or market["status"] != "active"
                    or parse_time(market["close_time"]) != parse_time(order["close_at"])
                    or market["strike_type"] != "greater"
                    or D(str(market["floor_strike"])) != D(str(order["floor_strike"]))
                    or fee_dict(schedule) != order["fee_schedule"]
                ):
                    paper.emit(
                        "cancel",
                        {
                            "order_id": order["id"],
                            "reason": "arrival_late_closed_predicate_or_fee_changed",
                            "source_record_ids": [*context_refs, rec],
                        },
                    )
                    continue
                book = books[order["ticker"]]
                opposite = "no" if order["side"] == "yes" else "yes"
                if (
                    order["style"] == "maker"
                    and book[opposite]
                    and D(order["limit"]) >= 1 - book[opposite][0][0]
                ):
                    paper.emit(
                        "cancel",
                        {"order_id": order["id"], "reason": "post_only_would_cross", "book_record_id": rec},
                    )
                    continue
                ahead = (
                    sum((size for price, size in book[order["side"]] if price >= D(order["limit"])), D(0))
                    if order["style"] == "maker"
                    else D(0)
                )
                paper.emit(
                    "arrival",
                    {
                        "order_id": order["id"],
                        "queue_ahead": str(ahead),
                        "book_record_id": rec,
                        "context_record_ids": context_refs,
                    },
                    received,
                )
                if order["style"] == "taker":
                    slices = taker_slices(
                        book,
                        order["side"],
                        order["remaining"],
                        order["limit"],
                        order["scenario"]["depth_retained"],
                        order["scenario"]["slippage"],
                    )
                    for i, fill in enumerate(slices):
                        paper.emit(
                            "fill",
                            {
                                **fill,
                                "order_id": order["id"],
                                "fill_id": f"book:{rec}:{i}",
                                "source_record_id": rec,
                                "evidence_at": iso(received),
                            },
                        )
                    if paper.state["orders"][order["id"]]["status"] in OPEN:
                        paper.emit("cancel", {"order_id": order["id"], "reason": "IOC_unfilled_remainder"})
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "arrival", "event": event, "error": str(exc)})
            for order in group:
                if paper.state["orders"][order["id"]]["status"] in OPEN:
                    paper.emit("cancel", {"order_id": order["id"], "reason": "arrival_source_error"})


def service_makers(client, paper):
    resting = [
        o for o in paper.state["orders"].values() if o["status"] == "resting" and o["style"] == "maker"
    ]
    for ticker in sorted({o["ticker"] for o in resting}):
        group = [o for o in resting if o["ticker"] == ticker]
        try:
            minimum = int(min(parse_time(o["arrived_at"]).timestamp() for o in group)) - 1
            trades, refs, cursors = [], [], set()
            params = {"ticker": ticker, "min_ts": minimum, "limit": 1000, "is_block_trade": "false"}
            for _ in range(10):
                page, rec = client.json("/markets/trades", params, kind="paper_trades", key=ticker)
                refs.append(rec)
                trades.extend((t, rec) for t in page["trades"])
                cursor = page.get("cursor")
                if not cursor:
                    break
                if cursor in cursors:
                    raise ValueError("Repeated trade cursor")
                cursors.add(cursor)
                params["cursor"] = cursor
            else:
                raise ValueError("Maker tape pagination cap; no fill from incomplete tape")
            for trade, source_id in sorted(
                trades, key=lambda pair: (parse_time(pair[0]["created_time"]), pair[0]["trade_id"])
            ):
                received = parse_time(record(client.archive, source_id)["available_at"])
                for original in group:
                    order = paper.state["orders"][original["id"]]
                    if order["status"] != "resting" or utcnow() >= parse_time(order["expires_at"]):
                        continue
                    update = maker_trade(order, trade, received, order["scenario"]["trade_participation"])
                    if update is None:
                        continue
                    paper.emit(
                        "queue",
                        {
                            "order_id": order["id"],
                            "queue_ahead": update["queue_ahead"],
                            "trade_id": trade["trade_id"],
                            "source_record_id": source_id,
                        },
                    )
                    if D(update["quantity"]) > 0:
                        paper.emit(
                            "fill",
                            {
                                "order_id": order["id"],
                                "quantity": update["quantity"],
                                "price": order["limit"],
                                "fill_id": "trade:" + trade["trade_id"],
                                "source_record_id": source_id,
                                "evidence_at": update["evidence_at"],
                            },
                        )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "maker_tape", "ticker": ticker, "error": str(exc)})
            for order in group:
                if paper.state["orders"][order["id"]]["status"] in OPEN:
                    paper.emit("cancel", {"order_id": order["id"], "reason": "maker_tape_unreliable"})


def expire_orders(paper, reason=None):
    for order in list(paper.state["orders"].values()):
        if order["status"] in OPEN and (
            reason
            or utcnow() >= parse_time(order["expires_at"])
            or paper.state["accounts"][order["account"]]["halted"]
        ):
            paper.emit("cancel", {"order_id": order["id"], "reason": reason or "expiry_or_drawdown"})


def settle_and_mark(client, paper):
    positions = list(paper.state["positions"].values())
    for event in sorted({p["event"] for p in positions if parse_time(p["close_at"]) <= utcnow()}):
        order = next(o for o in paper.state["orders"].values() if o["event"] == event)
        forecast = client.archive.json(record(client.archive, order["forecast_record_id"]))
        try:
            data, rec = client.json("/events/" + event, kind="paper_settlement_source", key=event)
            labels = finalized_labels(forecast, data)
            if labels is not None:
                paper.emit("settlement", {"event": event, "labels": labels, "source_record_id": rec})
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "settlement", "event": event, "error": str(exc)})
    active = [p for p in paper.state["positions"].values() if parse_time(p["close_at"]) > utcnow()]
    books, book_id = {}, None
    if active:
        try:
            books, book_id, _, _ = fresh_books(client, {p["ticker"] for p in active})
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "mark", "error": str(exc)})
            return  # No invented liquidation prices for a failed network request.
    for key, account in paper.state["accounts"].items():
        value = D(account["cash"])
        pending_value = D(0)
        for position in paper.state["positions"].values():
            if position["account"] != key:
                continue
            if parse_time(position["close_at"]) <= utcnow():
                pending_value += D(position["cost"])
                continue
            remaining = D(position["quantity"])
            for price, depth in books[position["ticker"]][position["side"]]:
                take = min(remaining, depth)
                value += price * take
                remaining -= take
                if remaining == 0:
                    break
        # Closed-but-unfinalized positions stay at cost, separately disclosed.
        paper.emit(
            "mark",
            {
                "account": key,
                "equity": str(value + pending_value),
                "pending_settlement_at_cost": str(pending_value),
                "book_record_id": book_id,
                "exit_fees_included": False,
            },
        )


def report(paper, run):
    accounts = []
    for key, account in paper.state["accounts"].items():
        accounts.append(
            {
                "account": key,
                **account,
                "reserved_cash": str(reserved(paper.state, key)),
                "available_cash": str(D(account["cash"]) - reserved(paper.state, key)),
                "open_exposure_at_cost": str(exposure(paper.state, key) - reserved(paper.state, key)),
                "paper_fill_count": sum(f["account"] == key for f in paper.state["fills"]),
            }
        )
    result = {
        "generated_at": iso(utcnow()),
        "run_record_id": int(paper.run_id),
        "accounts": accounts,
        "orders": list(paper.state["orders"].values()),
        "fills": paper.state["fills"],
        "positions": list(paper.state["positions"].values()),
        "settlements": paper.state["settlements"],
        "processed_slots": paper.state["slots"],
        "order_status_counts": dict(Counter(o["status"] for o in paper.state["orders"].values())),
        "paper_fill_count": len(paper.state["fills"]),
        "real_money_orders": 0,
        "profitability_proven": False,
        "stop_at": run["config"]["stop_at"],
        "limitations": [
            run["config"]["limits"],
            "Marked equity excludes exit fees and carries closed/unfinalized positions at cost; it is provisional",
        ],
    }
    temporary = Path("reports/E009_paper_state.json.tmp")
    temporary.write_text(json.dumps(result, indent=2))
    temporary.replace("reports/E009_paper_state.json")
    dashboard(paper.archive)
    return result


def run_loop(client, run_id):
    archive = client.archive
    run = archive.json(record(archive, run_id))
    config = run["config"]
    for path, expected in run["code_sha256"].items():
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
            raise ValueError("Paper implementation changed; a new future protocol is required: " + path)
    paper = PaperLedger(archive, run_id)
    expire_orders(paper, "restart_cancels_unobserved_orders")
    last_mark, last_tape = 0.0, 0.0
    try:
        while utcnow() < parse_time(config["stop_at"]):
            if (archive.root / "STOP_PAPER").exists():
                break
            expire_orders(paper)
            for slot in run["slots"]:
                if slot["decision_at"] in paper.state["slots"]:
                    continue
                scheduled = parse_time(slot["decision_at"])
                if utcnow() < scheduled:
                    continue
                try:
                    deadline = scheduled + timedelta(seconds=20)
                    if utcnow() > deadline:
                        raise ValueError("Missed future paper forecast slot; no backfill")
                    parent = None
                    if scheduled <= parse_time(config["existing_parent_last_decision_at"]):
                        matches = [
                            r
                            for r in archive.db.execute(
                                "SELECT * FROM records WHERE kind='shadow_forecast' AND available_at>=? AND available_at<=?",
                                (slot["decision_at"], iso(deadline)),
                            )
                            if archive.json(r)["scheduled_at"] == slot["decision_at"]
                        ]
                        if len(matches) > 1:
                            raise ValueError("Duplicate original forecasts")
                        if not matches:
                            continue
                        parent = matches[0]
                    else:
                        created = forecast_snapshot(
                            client,
                            record(archive, config["original_model_record_id"]),
                            scheduled,
                            parse_time(slot["settlement_at"]),
                            slot["horizon_minutes"],
                        )
                        parent = record(archive, created["record_id"])
                    forecast = paired_forecast(
                        archive, parent, record(archive, run["fresh_model_record_id"]), utcnow()
                    )
                    publication = utcnow()
                    if publication > deadline:
                        raise ValueError("Paper forecast computation missed deadline")
                    forecast["published_at"] = iso(publication)
                    forecast_id = archive.append(
                        "paper_forecast", str(run_id), publication, {}, canonical(forecast).encode()
                    )
                    make_orders(paper, forecast, forecast_id, config)
                    LOG.info(
                        "Paper slot %s processed, orders=%s fills=%s",
                        slot["decision_at"],
                        len(paper.state["orders"]),
                        len(paper.state["fills"]),
                    )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    paper.emit(
                        "error", {"stage": "forecast", "scheduled_at": slot["decision_at"], "error": str(exc)}
                    )
                    if slot["decision_at"] not in paper.state["slots"]:
                        paper.emit(
                            "slot", {"scheduled_at": slot["decision_at"], "skipped": True, "reason": str(exc)}
                        )
            arrival_orders(client, paper)
            if time.monotonic() - last_tape >= 5:
                service_makers(client, paper)
                last_tape = time.monotonic()
            if time.monotonic() - last_mark >= 30:
                settle_and_mark(client, paper)
                report(paper, run)
                last_mark = time.monotonic()
            time.sleep(0.2)
    finally:
        expire_orders(paper, "bounded_runner_stop")
        paper.emit(
            "stopped",
            {"reason": "stop_file_or_time_limit", "pending_positions": len(paper.state["positions"])},
        )
        result = report(paper, run)
        archive.append("experiment_report", "E009_paper_run", utcnow(), {}, canonical(result).encode())
    return {
        "paper_fill_count": len(paper.state["fills"]),
        "positions_pending": len(paper.state["positions"]),
        "real_money_orders": 0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--replay-only", action="store_true")
    parser.add_argument("--settle-only", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    archive = Archive()
    client = PublicClient(archive)
    try:
        with (archive.root / "paper.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another paper execution process holds the lock") from None
            run_id = args.run_record_id or register(archive)
            if args.register_only:
                print(json.dumps({"run_record_id": run_id}), flush=True)
            elif args.replay_only or args.settle_only:
                paper = PaperLedger(archive, run_id)
                if args.settle_only:
                    expire_orders(paper, "settlement_service_cancels_open_orders")
                    settle_and_mark(client, paper)
                result = report(paper, archive.json(record(archive, run_id)))
                print(
                    json.dumps(
                        {
                            "run_record_id": run_id,
                            "paper_fill_count": result["paper_fill_count"],
                            "network_requests": "public_settlement_and_marks" if args.settle_only else 0,
                        }
                    )
                )
            else:
                print(json.dumps({"run_record_id": run_id, "mode": "paper_only"}), flush=True)
                print(json.dumps(run_loop(client, run_id)), flush=True)
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
