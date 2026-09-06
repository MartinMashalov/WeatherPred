"""Registered prospective rain-calendar pairs. Public GETs, simulated IOC orders."""

import argparse
import fcntl
import hashlib
import json
import time
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path

import httpx
from pypdf import PdfReader

from weatherpred.archive import Archive, canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book
from weatherpred.http import PublicClient
from weatherpred.market_making import predicate, valid_tick
from weatherpred.paper import OPEN, PaperLedger, exposure, reduce_event, reserved, taker_slices
from weatherpred.rain_relations import calendar_rule, confirmed_dry, payout_bounds, plan_pair, relation
from weatherpred.timeutil import iso, parse_time, utcnow

D = Decimal
CONFIG = Path("config/e019_rain_pairs.json")


def record(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("Missing rain experiment evidence")
    return row


def fee_dict(schedule):
    return {
        "fee_type": schedule.fee_type,
        "multiplier": str(schedule.multiplier),
        "balance_precision": str(schedule.balance_precision),
    }


def context(client, event):
    data, event_id = client.json("/events/" + event, kind="rain_context", key=event)
    series_name = data["event"]["series_ticker"]
    if data["event"]["settlement_sources"] != [
        {"name": "The Weather Company", "url": "https://weather.com/kalshi"}
    ]:
        raise ValueError("Rain event source changed")
    series, series_id = client.json("/series/" + series_name, kind="rain_series", key=series_name)
    refs, changes = [event_id, series_id], []
    for page, rec in client.pages(
        "/events/fee_changes",
        "event_fee_changes",
        {"event_ticker": event, "limit": 1000},
        kind="rain_fees",
        key=event,
    ):
        changes.extend(page)
        refs.append(rec)
    terms = {
        k: series["series"].get(k)
        for k in ("contract_terms_url", "contract_url", "settlement_sources", "product_metadata")
    }
    return {**data, "series_terms": terms}, resolve_current_fee(series["series"], changes, utcnow()), refs


def books(client, tickers):
    data, rec = client.json(
        "/markets/orderbooks", [("tickers", t) for t in sorted(tickers)], kind="rain_books", key="E019"
    )
    row = record(client.archive, rec)
    meta = json.loads(row["metadata"])
    received, started = parse_time(row["available_at"]), parse_time(meta["request_started_at"])
    headers = meta["headers"]
    server = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    if (
        server is None
        or not -2 <= (received - server).total_seconds() <= 5
        or float(headers.get("age", 0)) > 5
        or (received - started).total_seconds() > 5
    ):
        raise ValueError("Rain book is stale, slow or undated")
    parsed = {b["ticker"]: parse_book(b) for b in data["orderbooks"]}
    if set(parsed) != set(tickers) or len(parsed) != len(data["orderbooks"]):
        raise ValueError("Rain book membership incomplete or duplicated")
    return parsed, rec, started, received


def saturday_sources(client, cfg):
    previous, previous_id = client.json(
        "/events/" + cfg["saturday_event"], kind="rain_saturday", key=cfg["saturday_event"]
    )
    source, source_id = client.json(
        "https://weather.com/kalshi/api/climate/primary",
        {"date": cfg["source_day"]},
        kind="rain_official_source",
        key=cfg["source_day"],
    )
    return {m["ticker"]: m for m in previous["markets"]}, source, [previous_id, source_id]


def historical_check(archive):
    daily = archive.latest("precipitation_settled_census", "KXRAIN")
    weekly = archive.latest("precipitation_settled_census", "KXRAINWKND")
    if any(archive.json(r).get("cursor") for r in (daily, weekly)):
        raise ValueError("Historical consistency check requires complete single-page sources")
    d = archive.json(daily)["markets"]
    rows = []
    for w in archive.json(weekly)["markets"]:
        row = {"weekend": w["ticker"]}
        try:
            rule = calendar_rule(w)
            members = [
                m
                for m in d
                if calendar_rule(m)["station"] == rule["station"]
                and calendar_rule(m)["start"] in (rule["start"], rule["end"])
            ]
            if len(members) != 2:
                raise ValueError("Missing or duplicated daily members")
            a, b = sorted(members, key=lambda m: calendar_rule(m)["start"])
            if any(m["status"] != "finalized" or m["result"] not in ("yes", "no") for m in (a, b, w)):
                raise ValueError("Missing finalized binary labels")
            expected = "yes" if a["result"] == "yes" or b["result"] == "yes" else "no"
            row["observed_binary_identity_matches"] = expected == w["result"]
            relation(a, b, w)
            row.update(
                saturday=a["ticker"],
                sunday=b["ticker"],
                saturday_result=a["result"],
                sunday_result=b["result"],
                weekend_result=w["result"],
                identity_matches=expected == w["result"],
                eligible_after_dry_saturday=a["result"] == "no",
            )
        except (ValueError, KeyError) as exc:
            row["error"] = str(exc)
        rows.append(row)
    terms = [record(archive, i) for i in (68872, 68873)]
    texts = ["\n".join(p.extract_text() for p in PdfReader(BytesIO(archive.body(r))).pages) for r in terms]
    result = {
        "generated_at": iso(utcnow()),
        "raw_source_ids": [daily["id"], weekly["id"], *[r["id"] for r in terms]],
        "current_terms_bytes_identical": len({r["body_sha256"] for r in terms}) == 1,
        "current_terms_extracted_text_identical": texts[0] == texts[1],
        "historical_city_weekends": len(rows),
        "calendar_weekends": len({w["weekend"].rsplit("-", 1)[0] for w in rows}),
        "matching_binary_identities": sum(r.get("identity_matches", False) for r in rows),
        "observed_binary_identity_matches_before_strict_rule_screen": sum(
            r.get("observed_binary_identity_matches", False) for r in rows
        ),
        "dry_saturday_pairs": sum(r.get("eligible_after_dry_saturday", False) for r in rows),
        "errors": [r for r in rows if "error" in r],
        "rows": rows,
        "limits": "Finalized labels checked after the fact, not historical availability or trading returns. Two weekends cannot bound future source-review failures. Current general terms and future trade rules remain subject to exchange exceptions.",
        "profitability_proven": False,
    }
    identifier = archive.append(
        "experiment_report", "E019_source_consistency", utcnow(), {}, canonical(result).encode()
    )
    Path("reports/E019_source_consistency.json").write_text(
        json.dumps({"record_id": identifier, **result}, indent=2)
    )
    return {"record_id": identifier, **{k: v for k, v in result.items() if k != "rows"}}


def register(client):
    archive = client.archive
    cfg = json.loads(CONFIG.read_text())
    if utcnow() >= parse_time(cfg["first_decision_at"]) or archive.latest(
        "experiment_protocol", cfg["experiment"]
    ):
        raise ValueError("Late or duplicate rain-pair registration")
    previous, source, refs = saturday_sources(client, cfg)
    daily, _, drefs = context(client, cfg["daily_event"])
    weekend, _, wrefs = context(client, cfg["weekend_event"])
    dm = {m["ticker"].rsplit("-", 1)[1]: m for m in daily["markets"]}
    wm = {m["ticker"].rsplit("-", 1)[1]: m for m in weekend["markets"]}
    panel, excluded = [], []
    for city in sorted(set(dm) | set(wm)):
        try:
            a, b, w = previous[cfg["saturday_event"] + "-" + city], dm[city], wm[city]
            r = relation(a, b, w)
            station = confirmed_dry(a, source, utcnow())
            if any(
                m["status"] != "active" or parse_time(m["close_time"]) <= parse_time(cfg["last_decision_at"])
                for m in (b, w)
            ):
                raise ValueError("Pair members not active through the registered window")
            panel.append(
                {
                    "city": city,
                    "relation": r,
                    "station": station,
                    "saturday": a,
                    "markets": [b, w],
                    "series_terms": [daily["series_terms"], weekend["series_terms"]],
                }
            )
        except (ValueError, KeyError) as exc:
            excluded.append({"city": city, "reason": str(exc)})
    if not panel:
        raise ValueError("No eligible rain-calendar pairs")
    paths = [
        CONFIG,
        Path(__file__),
        *[
            Path("weatherpred") / name
            for name in (
                "rain_relations.py",
                "paper.py",
                "books.py",
                "fees.py",
                "basket.py",
                "market_making.py",
                "http.py",
                "archive.py",
                "timeutil.py",
            )
        ],
    ]
    sources, hashes = {}, {}
    for path in paths:
        body = path.read_bytes()
        sources[str(path)] = archive.append("research_source", str(path), utcnow(), {}, body)
        hashes[str(path)] = hashlib.sha256(body).hexdigest()
    protocol = {
        "config": cfg,
        "panel": panel,
        "excluded": excluded,
        "selection_source_ids": [*refs, *drefs, *wrefs],
        "code_sha256": hashes,
        "source_record_ids": sources,
        "registered_at": iso(utcnow()),
    }
    if utcnow() >= parse_time(cfg["first_decision_at"]):
        raise ValueError("Registration crossed its deadline")
    run_id = archive.append(
        "experiment_protocol", cfg["experiment"], utcnow(), {}, canonical(protocol).encode()
    )
    paper = PaperLedger(archive, run_id)
    for scenario in cfg["scenarios"]:
        paper.emit("account", {"account": scenario["name"], "initial_cash": cfg["initial_cash"]})
    Path("reports/E019_registration.json").write_text(
        json.dumps({"run_record_id": run_id, **protocol}, indent=2)
    )
    return run_id


def submit_pair(paper, member, plan, scenario, schedules, slot, decision_id, book_id, submitted_at=None):
    now = submitted_at or utcnow()
    orders = []
    for i, market in enumerate(member["markets"]):
        due = now + timedelta(seconds=scenario["latency_seconds"] + i * scenario["leg_stagger_seconds"])
        order = {
            "id": iso(slot) + ":" + scenario["name"] + ":" + member["city"] + ":" + str(i),
            "account": scenario["name"],
            "event": market["event_ticker"],
            "ticker": market["ticker"],
            "city": member["city"],
            "leg_index": i,
            "side": plan["sides"][i],
            "style": "taker",
            "quantity": plan["quantity"],
            "limit": plan["limits"][i],
            "submitted_at": iso(now),
            "arrival_due_at": iso(due),
            "expires_at": iso(due + timedelta(seconds=60)),
            "close_at": market["close_time"],
            "floor_strike": 0,
            "fee_schedule": fee_dict(schedules[i]),
            "decision_record_id": decision_id,
            "decision_book_record_id": book_id,
            "scenario": scenario,
            "market_predicate": predicate(market),
            "pair_plan": plan,
            "series_terms": member["series_terms"][i],
        }
        if not valid_tick(market, D(order["limit"])):
            raise ValueError("Pair limit is outside the venue price grid")
        orders.append(order)
    # Validate both reservations together before publishing either intent.
    proposed = paper.state
    for order in orders:
        proposed = reduce_event(proposed, {"type": "order", "data": order, "at": iso(now)})
    for order in orders:
        paper.emit("order", order, now)


def decision(client, paper, protocol, slot):
    cfg = protocol["config"]
    if (utcnow() - slot).total_seconds() > cfg["maximum_slot_lateness_seconds"]:
        raise ValueError("Missed rain decision; no backfill")
    previous, source, refs = saturday_sources(client, cfg)
    contexts = [context(client, cfg[k]) for k in ("daily_event", "weekend_event")]
    schedules = [c[1] for c in contexts]
    refs += [r for c in contexts for r in c[2]]
    current = {m["ticker"]: m for c in contexts for m in c[0]["markets"]}
    eligible, rejections = [], []
    for member in protocol["panel"]:
        try:
            confirmed_dry(previous[member["saturday"]["ticker"]], source, utcnow())
            if predicate(previous[member["saturday"]["ticker"]]) != predicate(member["saturday"]):
                raise ValueError("Saturday contract changed")
            if [c[0]["series_terms"] for c in contexts] != member["series_terms"]:
                raise ValueError("Series terms or settlement exceptions changed")
            for m in member["markets"]:
                if (
                    predicate(current[m["ticker"]]) != predicate(m)
                    or current[m["ticker"]]["status"] != "active"
                ):
                    raise ValueError("Pair changed or closed")
            eligible.append(member)
        except (ValueError, KeyError) as exc:
            rejections.append({"city": member["city"], "reason": str(exc)})
    if not eligible:
        raise ValueError("No currently source-confirmed pairs")
    snapshot, book_id, _, received = books(client, {m["ticker"] for p in eligible for m in p["markets"]})
    if (received - slot).total_seconds() > cfg["maximum_slot_lateness_seconds"]:
        raise ValueError("Decision sources arrived after the registered lateness limit")
    refs.append(book_id)
    plans = []
    for scenario in cfg["scenarios"]:
        account = scenario["name"]
        for member in eligible:
            if any(
                o["account"] == account and o["city"] == member["city"]
                for o in paper.state["orders"].values()
            ):
                rejections.append(
                    {
                        "city": member["city"],
                        "account": account,
                        "reason": "one_attempt_per_city_already_used",
                    }
                )
                continue
            equity = min(
                D(paper.state["accounts"][account]["initial_cash"]),
                D(paper.state["accounts"][account]["cash"])
                + exposure(paper.state, account)
                - reserved(paper.state, account),
            )
            budget = min(
                equity * D(cfg["pair_cap_fraction"]),
                D(paper.state["accounts"][account]["cash"]) - reserved(paper.state, account),
                equity * D("0.10") - exposure(paper.state, account),
            )
            plan = plan_pair(
                [snapshot[m["ticker"]] for m in member["markets"]],
                schedules,
                scenario,
                budget=max(D(0), budget),
                maximum_quantity=cfg["maximum_quantity"],
                surplus=cfg["minimum_surplus_per_pair"],
                allowance=cfg["source_allowance_per_pair"],
            )
            if plan:
                plans.append({"city": member["city"], "account": account, "plan": plan})
            else:
                rejections.append(
                    {
                        "city": member["city"],
                        "account": account,
                        "reason": "no_pair_after_depth_cost_source_allowance_and_budget",
                    }
                )
    published = utcnow()
    evidence = {
        "run_record_id": int(paper.run_id),
        "scheduled_at": iso(slot),
        "published_at": iso(published),
        "source_record_ids": refs,
        "decision_book_record_id": book_id,
        "fee_schedules": [fee_dict(s) for s in schedules],
        "plans": plans,
        "rejections": rejections,
    }
    identifier = paper.archive.append(
        "rain_decision", paper.run_id, published, {}, canonical(evidence).encode()
    )
    for row in sorted(plans, key=lambda p: (-D(p["plan"]["conditional_surplus"]), p["city"], p["account"])):
        member = next(m for m in eligible if m["city"] == row["city"])
        scenario = next(s for s in cfg["scenarios"] if s["name"] == row["account"])
        try:
            submit_pair(paper, member, row["plan"], scenario, schedules, slot, identifier, book_id)
        except ValueError as exc:
            paper.emit(
                "abstain",
                {
                    "city": row["city"],
                    "account": row["account"],
                    "decision_record_id": identifier,
                    "reason": str(exc),
                },
            )
    return identifier


def cancel(paper, order, reason, refs=None):
    if paper.state["orders"][order["id"]]["status"] in OPEN:
        paper.emit("cancel", {"order_id": order["id"], "reason": reason, "source_record_ids": refs or []})


def arrivals(client, paper):
    due = [
        o
        for o in paper.state["orders"].values()
        if o["status"] == "pending" and parse_time(o["arrival_due_at"]) <= utcnow()
    ]
    for event in sorted({o["event"] for o in due}):
        group = [o for o in due if o["event"] == event]
        try:
            data, schedule, refs = context(client, event)
            current = {m["ticker"]: m for m in data["markets"]}
            snapshot, rec, started, received = books(client, {o["ticker"] for o in group})
            for order in group:
                m = current[order["ticker"]]
                if (
                    parse_time(order["arrival_due_at"]) > started
                    or (received - parse_time(order["arrival_due_at"])).total_seconds() > 15
                    or received >= parse_time(order["expires_at"])
                    or m["status"] != "active"
                    or predicate(m) != order["market_predicate"]
                    or fee_dict(schedule) != order["fee_schedule"]
                    or data["series_terms"] != order["series_terms"]
                ):
                    cancel(paper, order, "arrival_late_closed_rules_or_fees_changed", [*refs, rec])
                    continue
                paper.emit(
                    "arrival",
                    {
                        "order_id": order["id"],
                        "queue_ahead": "0",
                        "book_record_id": rec,
                        "context_record_ids": refs,
                    },
                    received,
                )
                for i, fill in enumerate(
                    taker_slices(
                        snapshot[order["ticker"]],
                        order["side"],
                        order["remaining"],
                        order["limit"],
                        order["scenario"]["depth_retained"],
                        order["scenario"]["slippage"],
                    )
                ):
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
                cancel(paper, order, "IOC_unfilled_remainder")
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "arrival", "event": event, "error": str(exc)})
            for order in group:
                cancel(paper, order, "arrival_source_error")


def settle(client, paper):
    for event in sorted(
        {p["event"] for p in paper.state["positions"].values() if parse_time(p["close_at"]) <= utcnow()}
    ):
        try:
            data, rec = client.json("/events/" + event, kind="rain_settlement", key=event)
            current = {m["ticker"]: m for m in data["markets"]}
            positions = [p for p in paper.state["positions"].values() if p["event"] == event]
            labels = {}
            for p in positions:
                m = current[p["ticker"]]
                order = next(o for o in paper.state["orders"].values() if o["ticker"] == p["ticker"])
                expected = order["market_predicate"]
                actual = {**predicate(m), "close_time": expected["close_time"]}
                if actual != expected:
                    raise ValueError("Final rain settlement rule changed")
                if (
                    m["status"] != "finalized"
                    or m["result"] not in ("yes", "no")
                    or not m.get("settlement_ts")
                    or parse_time(m["settlement_ts"]) > utcnow()
                ):
                    break
                labels[p["ticker"]] = int(m["result"] == "yes")
            if set(labels) == {p["ticker"] for p in positions}:
                paper.emit("settlement", {"event": event, "labels": labels, "source_record_id": rec})
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            paper.emit("error", {"stage": "settlement", "event": event, "error": str(exc)})


def report(paper):
    accounts = []
    for account, value in paper.state["accounts"].items():
        pairs = []
        for city in sorted({o["city"] for o in paper.state["orders"].values() if o["account"] == account}):
            orders = sorted(
                [o for o in paper.state["orders"].values() if o["account"] == account and o["city"] == city],
                key=lambda o: o["leg_index"],
            )
            positions = [
                next(
                    (
                        p
                        for p in paper.state["positions"].values()
                        if p["account"] == account and p["ticker"] == o["ticker"] and p["side"] == o["side"]
                    ),
                    {},
                )
                for o in orders
            ]
            quantities = [D(p.get("quantity", "0")) for p in positions]
            costs = sum(D(p.get("cost", "0")) for p in positions)
            low, high, broken = payout_bounds(quantities, [o["side"] for o in orders])
            # Once one member has finalized, the shared outcome is known. Report
            # remaining bounds conservatively instead of crediting a fake pair.
            pairs.append(
                {
                    "city": city,
                    "quantities": list(map(str, quantities)),
                    "matched_open_quantity": str(min(quantities)),
                    "open_cost": str(costs),
                    "conditional_open_pnl_low": str(low - costs),
                    "conditional_open_pnl_high": str(high - costs),
                    "source_break_open_pnl_low": str(broken - costs),
                    "orders": [o["id"] for o in orders],
                }
            )
        accounts.append(
            {
                "account": account,
                **value,
                "reserved_cash": str(reserved(paper.state, account)),
                "pairs": pairs,
            }
        )
    result = {
        "generated_at": iso(utcnow()),
        "run_record_id": int(paper.run_id),
        "accounts": accounts,
        "orders": list(paper.state["orders"].values()),
        "fills": paper.state["fills"],
        "settlements": paper.state["settlements"],
        "slots": paper.state["slots"],
        "order_status_counts": dict(Counter(o["status"] for o in paper.state["orders"].values())),
        "real_money_orders": 0,
        "profitability_proven": False,
        "limits": "Conditional payout bounds assume unchanged normal source-consistent settlement; distinct contracts do not net. Source-break bound allows both held sides to lose. Outstanding orders are excluded. Paper fills are simulations, not proven executions or independent trading days.",
    }
    Path("reports/E019_paper_state.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {k: result[k] for k in ("generated_at", "run_record_id", "order_status_counts")}, default=str
        ),
        flush=True,
    )


def main(args):
    with Path("data/e019.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        archive = Archive()
        client = PublicClient(archive)
        try:
            if args.inspect:
                print(json.dumps(historical_check(archive), indent=2))
                return
            run_id = args.run_record_id or register(client)
            protocol = archive.json(record(archive, run_id))
            if protocol["config"]["experiment"] != "E019-rain-calendar-pairs-v1":
                raise ValueError("Not an E019 registration")
            for path, digest in protocol["code_sha256"].items():
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                    raise ValueError("Frozen rain-pair source changed: " + path)
            paper = PaperLedger(archive, run_id)
            for o in list(paper.state["orders"].values()):
                cancel(paper, o, "restart_no_unobserved_fills")
            if args.register_only:
                print(
                    json.dumps(
                        {"run_record_id": run_id, "panel_cities": [p["city"] for p in protocol["panel"]]}
                    )
                )
                return
            if args.settle_only:
                settle(client, paper)
                report(paper)
                return
            cfg = protocol["config"]
            slots = []
            at = parse_time(cfg["first_decision_at"])
            while at <= parse_time(cfg["last_decision_at"]):
                slots.append(at)
                at += timedelta(seconds=cfg["interval_seconds"])
            last_report, last_settlement = 0.0, 0.0
            while utcnow() < parse_time(cfg["stop_at"]) and not Path("data/STOP_E019").exists():
                for o in list(paper.state["orders"].values()):
                    if utcnow() >= parse_time(o["expires_at"]):
                        cancel(paper, o, "expired")
                arrivals(client, paper)
                due = next((s for s in slots if s <= utcnow() and iso(s) not in paper.state["slots"]), None)
                if due:
                    identifier = None
                    try:
                        identifier = decision(client, paper, protocol, due)
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        paper.emit(
                            "error", {"stage": "decision", "scheduled_at": iso(due), "error": str(exc)}
                        )
                    paper.emit("slot", {"scheduled_at": iso(due), "decision_record_id": identifier})
                if time.monotonic() - last_settlement > 60:
                    settle(client, paper)
                    last_settlement = time.monotonic()
                if time.monotonic() - last_report > 60:
                    report(paper)
                    last_report = time.monotonic()
                time.sleep(0.25)
            for o in list(paper.state["orders"].values()):
                cancel(paper, o, "bounded_stop_or_kill_switch")
            report(paper)
            paper.emit("stopped", {"reason": "bounded_stop_or_kill_switch"})
        finally:
            client.close()
            archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--settle-only", action="store_true")
    parser.add_argument("--inspect", action="store_true")
    main(parser.parse_args())
