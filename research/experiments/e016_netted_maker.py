"""A separate netted forward cohort; original E015 code and ledger stay frozen."""

import argparse
import fcntl
import hashlib
import json
import time
from collections import Counter
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from research.experiments.e015_market_making import (
    arrivals,
    cancel,
    mark,
    record,
    settlements,
    submit_round,
    tape,
)
from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.market_making import inventory_report
from weatherpred.netted_paper import NettedPaperLedger, reduce_netted
from weatherpred.paper import PaperLedger, empty_state
from weatherpred.timeutil import iso, parse_time, utcnow

CONFIG = Path("config/e016_netted_maker.json")


def register(archive):
    amendment = json.loads(CONFIG.read_text())
    parent_row = record(archive, amendment["parent_run_record_id"])
    parent = archive.json(parent_row)
    cfg = {**parent["config"], **amendment}
    cfg["risk"] = amendment["risk_limitations"]
    cfg["selection"] = amendment["panel_and_policies"]
    first = parse_time(cfg["first_decision_at"])
    if utcnow() >= first or archive.latest("experiment_protocol", cfg["experiment"]):
        raise ValueError("Late/duplicate E016 registration")
    hashes, sources = dict(parent["code_sha256"]), dict(parent["source_record_ids"])
    for path in (Path(__file__), CONFIG, Path("weatherpred/netted_paper.py")):
        body = path.read_bytes()
        hashes[str(path)] = hashlib.sha256(body).hexdigest()
        sources[str(path)] = archive.append("research_source", str(path), utcnow(), {}, body)
    protocol = {
        "config": cfg,
        "parent_run_record_id": amendment["parent_run_record_id"],
        "registered_at": iso(utcnow()),
        "panel": parent["panel"],
        "selection": parent["selection"],
        "universe_record_ids": parent["universe_record_ids"],
        "code_sha256": hashes,
        "source_record_ids": sources,
    }
    identifier = archive.append(
        "experiment_protocol", cfg["experiment"], utcnow(), {}, canonical(protocol).encode()
    )
    Path("reports/E016_registration.json").write_text(
        json.dumps({"run_record_id": identifier, **protocol}, indent=2)
    )
    return identifier, protocol


def report(ledger, protocol):
    s = ledger.state
    result = {
        "generated_at": iso(utcnow()),
        "run_record_id": int(ledger.run_id),
        "panel_tickers": [m["ticker"] for m in protocol["panel"]],
        "slots": len(s["slots"]),
        "orders": len(s["orders"]),
        "fill_records": len(s["fills"]),
        "order_status": dict(Counter(o["status"] for o in s["orders"].values())),
        "events_with_fills": sorted({o["event"] for o in s["orders"].values() if o["fill_ids"]}),
        "settled_events": s["settled_events"],
        "nettings": s.get("nettings", []),
        "accounts": [inventory_report(s, a) for a in s["accounts"]],
        "real_money_orders": 0,
        "profitability_proven": False,
    }
    Path("reports/E016_netted_maker.json").write_text(json.dumps(result, indent=2))
    return result


def project_locked(run_id):
    """Identical recorded orders/fills, without inventing trades enabled by netting."""
    archive = Archive()
    try:
        archive.db.execute("BEGIN")
        locked = PaperLedger(archive, run_id).state
        netted = empty_state()
        rows = list(
            archive.db.execute(
                "SELECT * FROM records WHERE kind='paper_event' AND key=? ORDER BY id", (str(run_id),)
            )
        )
        for row in rows:
            netted = reduce_netted(netted, archive.json(row))
        results = []
        for account in locked["accounts"]:
            original, projected = inventory_report(locked, account), inventory_report(netted, account)
            for key in (
                "terminal_pnl_lower_bound_on_current_fills",
                "terminal_pnl_upper_bound_on_current_fills",
            ):
                if Decimal(original[key]) != Decimal(projected[key]):
                    raise ValueError("Same-fill projection changed terminal payout economics")
            results.append({"account": account, "locked": original, "netted_same_fills": projected})
        result = {
            "generated_at": iso(utcnow()),
            "source_run_record_id": run_id,
            "last_source_journal_id": rows[-1]["id"],
            "same_fill_count": len(locked["fills"]),
            "additional_orders": 0,
            "additional_fills": 0,
            "nettings": netted.get("nettings", []),
            "accounts": results,
            "network_requests": 0,
            "profitability_proven": False,
        }
        Path("reports/E016_same_fill_projection.json").write_text(json.dumps(result, indent=2))
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "source_run_record_id",
                        "last_source_journal_id",
                        "same_fill_count",
                        "additional_orders",
                        "additional_fills",
                        "network_requests",
                    )
                }
            )
        )
        print(json.dumps({"netting_records": len(result["nettings"]), "terminal_economics_unchanged": True}))
    finally:
        archive.close()


def main(run_id=None, register_only=False, settle_only=False):
    archive = Archive()
    client = PublicClient(archive, interval=0.15)
    try:
        with (archive.root / "netted_maker.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if run_id is None:
                run_id, protocol = register(archive)
            else:
                protocol = archive.json(record(archive, run_id))
            for path, expected in protocol["code_sha256"].items():
                if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                    raise ValueError("Frozen E016 source changed: " + path)
            cfg = protocol["config"]
            print(
                json.dumps(
                    {
                        "run_record_id": run_id,
                        "first_decision_at": cfg["first_decision_at"],
                        "panel": [m["ticker"] for m in protocol["panel"]],
                    }
                ),
                flush=True,
            )
            if register_only:
                return
            ledger = NettedPaperLedger(archive, run_id)
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
                print(json.dumps(report(ledger, protocol)))
                return
            slots, slot = [], parse_time(cfg["first_decision_at"])
            while slot <= parse_time(cfg["last_decision_at"]):
                slots.append(slot)
                slot += timedelta(seconds=cfg["round_interval_seconds"])
            next_monitor = utcnow()
            while utcnow() < parse_time(cfg["stop_at"]):
                if (archive.root / "STOP_NETTED_MAKER").exists():
                    break
                for order in list(ledger.state["orders"].values()):
                    if utcnow() >= parse_time(order["expires_at"]):
                        cancel(ledger, order, "venue_expiry")
                for slot in [s for s in slots if s <= utcnow() and iso(s) not in ledger.state["slots"]]:
                    submit_round(client, ledger, protocol, slot)
                arrivals(client, ledger, protocol)
                tape(client, ledger)
                if utcnow() >= next_monitor:
                    settlements(client, ledger, protocol)
                    mark(client, ledger)
                    result = report(ledger, protocol)
                    print(
                        json.dumps(
                            {
                                k: result[k]
                                for k in ("generated_at", "slots", "orders", "fill_records", "settled_events")
                            }
                        ),
                        flush=True,
                    )
                    next_monitor = utcnow() + timedelta(seconds=60)
                time.sleep(cfg["poll_seconds"])
            for order in list(ledger.state["orders"].values()):
                cancel(ledger, order, "bounded_stop_or_kill_switch")
            ledger.emit("stopped", {"reason": "bounded_stop_or_kill_switch"})
            report(ledger, protocol)
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--register-only", action="store_true")
    parser.add_argument("--settle-only", action="store_true")
    parser.add_argument("--project-locked-run", type=int)
    args = parser.parse_args()
    if args.project_locked_run:
        project_locked(args.project_locked_run)
    else:
        main(args.run_record_id, args.register_only, args.settle_only)
