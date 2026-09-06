"""Independent E023 money/quantity audit; no production replay or fee imports."""

import argparse
import gzip
import hashlib
import json
import math
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path

D = Decimal
ZERO = D(0)


def cash_flow(price, quantity, coefficient, selling=False):
    """Independent integer micro-fee / cent-balance calculation."""
    p, n, c = D(str(price)), D(quantity), D(str(coefficient))
    fee_micro = int((c * n * p * (1 - p) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
    signed_gross = p * n * (1 if selling else -1)
    cent_change = int(
        ((signed_gross - D(fee_micro) / 1_000_000) * 100).to_integral_value(rounding=ROUND_FLOOR)
    )
    balance = D(cent_change) / 100
    return balance, signed_gross - balance


def check(value, expected, message, tolerance="0.00000001"):
    if abs(D(str(value)) - expected) > D(tolerance):
        raise ValueError(f"{message}: observed={value}, independently expected={expected}")


def choose_quantity(budget, limit, coefficient, cap):
    unit_before_rounding = limit + coefficient * limit * (1 - limit)
    q = min(cap, max(0, int((budget / unit_before_rounding).to_integral_value(rounding=ROUND_FLOOR))))
    while q and -cash_flow(limit, q, coefficient)[0] > budget:
        q -= 1
    while q < cap and -cash_flow(limit, q + 1, coefficient)[0] <= budget:
        q += 1
    return q


def parent_orders(parent, config):
    start = datetime.fromisoformat(config["training_start"]).replace(tzinfo=UTC).timestamp()
    decision_end = (
        datetime.fromisoformat(config["training_decision_end_exclusive"]).replace(tzinfo=UTC).timestamp()
    )
    rows = {}
    outside = 0
    for decision in parent["decisions"]:
        if decision["status"] in ("no_signal", "outside_market_hours"):
            continue
        when = decision["signal_ts"]
        if when >= decision_end:
            continue
        if when < start:
            outside += 1
            continue
        identifier = f"{decision['event']}:{decision['ticker']}:{decision['side']}:{when}"
        if identifier in rows:
            raise ValueError("Duplicate parent order identity")
        rows[identifier] = decision
    return rows, outside


def audit_account(payload, parent, config):
    account = payload["account"]
    if account["execution_mode"] != "hypothetical":
        raise ValueError("This audit entry point expects the registered hypothetical training accounts")
    scenario = next(s for s in config["scenarios"] if s["name"] == payload["scenario"])
    rows, outside = parent_orders(parent, config)
    initial = D(config["initial_cash"])
    cash = initial
    pending, held = {}, {}
    entries, closes, reasons = [], [], Counter()
    if outside:
        reasons["decision_outside_window"] = outside
    realized = fees = ZERO
    cost_peak = zero_peak = initial
    max_cost_dd = max_zero_dd = ZERO
    killed = False
    kill_ts = None
    risk = D(payload["risk_fraction"])
    event_fraction, cluster_fraction, total_fraction = (
        D(config[k]) for k in ("max_event_fraction", "max_cluster_fraction", "max_total_fraction")
    )
    entry_fee, exit_fee = D(scenario["entry_coefficient"]), D(scenario["exit_coefficient"])
    cap, kill = config["conditional_depth_cap"], D(config["drawdown_kill_fraction"])

    def state():
        reserved = sum((p["cost"] for p in pending.values()), ZERO)
        principal = sum((p["principal"] for p in held.values()), ZERO)
        entry_cost = sum((p["cost"] for p in held.values()), ZERO)
        return {
            "cash": cash,
            "reserved_cash": reserved,
            "total_cash": cash + reserved,
            "open_principal": principal,
            "open_entry_cost": entry_cost,
            "cost_basis_equity": cash + reserved + principal,
            "zero_mark_equity": cash + reserved,
        }

    def expected_intent(identifier):
        row = rows[identifier]
        limit = D(
            str(row.get("entry_limit", min(0.99, round(row["signal_ask"] + float(scenario["slippage"]), 8))))
        )
        equity = state()["cost_basis_equity"]
        gross = sum((p["cost"] for p in pending.values()), ZERO) + sum(
            (p["cost"] for p in held.values()), ZERO
        )
        event = sum((p["cost"] for p in pending.values() if p["event"] == row["event"]), ZERO) + sum(
            (p["cost"] for p in held.values() if p["event"] == row["event"]), ZERO
        )
        limits = {
            "cash_limit": cash,
            "risk_fraction_limit": risk * equity,
            "event_cap": event_fraction * equity - event,
            "cluster_cap": cluster_fraction * equity - gross,
            "total_cap": total_fraction * equity - gross,
        }
        binding = min(limits, key=limits.get)
        q = choose_quantity(max(ZERO, limits[binding]), limit, entry_fee, cap)
        reason = "below_one_contract_risk_budget" if binding == "risk_fraction_limit" else binding
        return q, limit, equity, reason

    previous_time = -math.inf
    previous_order = (-math.inf, -1, "")
    seen_decisions, entry_records, close_records = set(), [], []
    start = datetime.fromisoformat(config["training_start"]).replace(tzinfo=UTC).timestamp()
    end = datetime.fromisoformat(config["selection_cutoff"]).replace(tzinfo=UTC).timestamp()
    if account["start_ts"] != start or account["end_ts"] != end:
        raise ValueError("Account calendar differs from its registration")
    boundaries = list(range(int(start) + 86400, int(end) + 1, 86400))
    if [day["timestamp"] for day in account["daily"]] != boundaries:
        raise ValueError("Missing, duplicate, or out-of-order daily calendar row")
    ledger = iter(account["orders"])
    next_row = next(ledger, None)
    prior_cost = prior_zero = initial
    prior_realized = prior_fees = ZERO
    prior_closes = 0
    journal_count = 0
    for daily in account["daily"]:
        while next_row is not None and next_row["timestamp"] < daily["timestamp"]:
            item = next_row
            next_row = next(ledger, None)
            journal_count += 1
            identifier, when, stage, status = (
                item["trade_id"],
                item["timestamp"],
                item["stage"],
                item["status"],
            )
            if when < previous_time:
                raise ValueError("Account journal time moved backwards")
            previous_time = when
            if stage != "kill":
                ordering = (when, {"release": 0, "decision": 1, "entry": 2}[stage], identifier)
                if ordering < previous_order:
                    raise ValueError("Same-time events violate registered cash availability order")
                previous_order = ordering
            if identifier not in rows:
                raise ValueError("Journal order does not occur in the pinned pre-cutoff parent decisions")
            source = rows[identifier]
            if stage == "decision":
                if identifier in seen_decisions:
                    raise ValueError("Repeated source decision")
                seen_decisions.add(identifier)
                if when != source["signal_ts"]:
                    raise ValueError("Decision timestamp differs from its source")
                q, limit, equity, no_size_reason = expected_intent(identifier)
                if status == "reserved":
                    if (
                        killed
                        or q <= 0
                        or item["intended_quantity"] != q
                        or identifier in pending
                        or identifier in held
                    ):
                        raise ValueError("Incorrect or duplicate intended quantity")
                    cost = -cash_flow(limit, q, entry_fee)[0]
                    check(item["limit_price"], limit, "decision limit")
                    check(item["reserved_cash"], cost, "reservation")
                    check(item["sizing_cost_basis_equity"], equity, "sizing equity")
                    cash -= cost
                    pending[identifier] = {
                        "quantity": q,
                        "cost": cost,
                        "limit": limit,
                        "event": source["event"],
                    }
                else:
                    expected_reason = "drawdown_kill_active" if killed else no_size_reason
                    if item["reason"] != expected_reason or (not killed and q > 0):
                        raise ValueError("Unexplained rejected decision")
                    reasons[item["reason"]] += 1
            elif stage == "kill":
                if (
                    not killed
                    or identifier not in pending
                    or item["reason"] != "drawdown_cancelled_pending_order"
                ):
                    raise ValueError("Incorrect drawdown reservation cancellation")
                cash += pending.pop(identifier)["cost"]
                reasons[item["reason"]] += 1
            elif stage == "entry":
                if when != source["entry_ts"] or identifier not in pending:
                    raise ValueError("Entry lacks its earlier reservation or exact source time")
                order = pending.pop(identifier)
                price = source.get("entry_price")
                if status == "not_filled":
                    expected_reason = "missing_entry_quote" if price is None else "entry_limit_not_met"
                    if price is not None and ZERO < D(str(price)) <= order["limit"]:
                        raise ValueError("Executable hypothetical parent entry rejected without explanation")
                    if item["reason"] != expected_reason:
                        raise ValueError("Incorrect later entry rejection")
                    cash += order["cost"]
                    reasons[item["reason"]] += 1
                else:
                    price = D(str(price))
                    if not ZERO < price <= order["limit"] or item["quantity"] != order["quantity"]:
                        raise ValueError("Entry quantity or price violates its earlier intent")
                    quantity = order["quantity"]
                    change, charge = cash_flow(price, quantity, entry_fee)
                    cost = -change
                    if cost > order["cost"]:
                        raise ValueError("Entry cost exceeds reservation")
                    for name, expected in (
                        ("entry_price", price),
                        ("entry_cost", cost),
                        ("entry_principal", price * quantity),
                        ("entry_fee", charge),
                    ):
                        check(item[name], expected, name)
                    cash += order["cost"] - cost
                    fees += charge
                    held[identifier] = {
                        "quantity": quantity,
                        "cost": cost,
                        "principal": price * quantity,
                        "event": source["event"],
                        "fee": charge,
                    }
                    entries.append(identifier)
                    entry_records.append(
                        {k: v for k, v in item.items() if k not in ("stage", "status", "timestamp")}
                    )
            elif stage == "release":
                if status != "closed" or identifier not in held:
                    raise ValueError("Release lacks a held position")
                position = held.pop(identifier)
                if source["status"] != "conditional_trade" or when != source["exit_ts"]:
                    raise ValueError("Release time or terminal outcome is not the pinned endpoint")
                sale = source["exit_kind"] == "scheduled_quote"
                kind = "sale" if sale else "settlement"
                quantity = position["quantity"]
                if item["quantity"] != quantity or item["terminal_kind"] != kind:
                    raise ValueError("Release quantity or kind mismatch")
                if sale and quantity > cap:
                    raise ValueError("Hypothetical aggregate sale exceeds declared capacity")
                value = D(str(source["exit_price"]))
                proceeds, charge = cash_flow(value, quantity, exit_fee if sale else ZERO, True)
                pnl = proceeds - position["cost"]
                for name, expected in (
                    ("terminal_cash_per_contract", value),
                    ("exit_proceeds", proceeds),
                    ("exit_fee", charge),
                    ("entry_cost", position["cost"]),
                    ("entry_fee", position["fee"]),
                    ("pnl", pnl),
                ):
                    check(item[name], expected, name)
                cash += proceeds
                fees += charge
                realized += pnl
                closes.append(identifier)
                close_records.append(
                    {k: v for k, v in item.items() if k not in ("stage", "status", "timestamp")}
                )
            else:
                raise ValueError("Unexpected account journal operation")
            now = state()
            cost_peak = max(cost_peak, now["cost_basis_equity"])
            zero_peak = max(zero_peak, now["zero_mark_equity"])
            cost_dd, zero_dd = (
                1 - now["cost_basis_equity"] / cost_peak,
                1 - now["zero_mark_equity"] / zero_peak,
            )
            max_cost_dd, max_zero_dd = max(max_cost_dd, cost_dd), max(max_zero_dd, zero_dd)
            if not killed and cost_dd >= kill:
                killed, kill_ts = True, when
            if cash < 0:
                raise ValueError("Independent reconstruction found an overdraft")
        now = state()
        for name, value in now.items():
            check(daily[name], value, "daily " + name)
        for name, current, previous in (
            ("cost_basis_log_return", now["cost_basis_equity"], prior_cost),
            ("zero_mark_log_return", now["zero_mark_equity"], prior_zero),
        ):
            expected = math.log(float(current / previous)) if current > 0 and previous > 0 else None
            if expected is None:
                if daily[name] is not None:
                    raise ValueError("Undefined zero-equity log return recorded as finite")
            elif abs(daily[name] - expected) > 1e-12:
                raise ValueError("Daily log return mismatch")
        check(daily["daily_realized_pnl"], realized - prior_realized, "daily realized P&L")
        check(daily["daily_fees"], fees - prior_fees, "daily fees")
        check(daily["cost_basis_drawdown"], 1 - now["cost_basis_equity"] / cost_peak, "daily drawdown")
        check(
            daily["zero_mark_drawdown"], 1 - now["zero_mark_equity"] / zero_peak, "daily zero-mark drawdown"
        )
        expected_date = datetime.fromtimestamp(daily["timestamp"] - 1, UTC).date().isoformat()
        if daily["date"] != expected_date:
            raise ValueError("Daily UTC date mismatch")
        if abs(daily["log_cost_basis_equity"] - math.log(float(now["cost_basis_equity"]))) > 1e-12:
            raise ValueError("Daily absolute log-equity mismatch")
        if daily["released_trades"] != len(closes) - prior_closes or daily["drawdown_killed"] != killed:
            raise ValueError("Daily releases or drawdown stop mismatch")
        prior_cost, prior_zero = now["cost_basis_equity"], now["zero_mark_equity"]
        prior_realized, prior_fees, prior_closes = realized, fees, len(closes)
    if next_row is not None:
        raise ValueError("Journal extends beyond its final daily snapshot")
    if seen_decisions != set(rows):
        raise ValueError("Account omitted a source decision inside its registered window")
    for name, value in state().items():
        check(account[name], value, "terminal " + name)
    for name, value in (
        ("realized_pnl", realized),
        ("total_fees", fees),
        ("max_cost_basis_drawdown", max_cost_dd),
        ("max_zero_mark_drawdown", max_zero_dd),
    ):
        check(account[name], value, name)
    if account["drawdown_killed"] != killed or account["drawdown_kill_ts"] != kill_ts:
        raise ValueError("Terminal drawdown kill state mismatch")
    if account["no_trade_reasons"] != dict(reasons):
        raise ValueError("No-trade reason counts mismatch")
    if [p["trade_id"] for p in account["accepted_trades"]] != entries or [
        p["trade_id"] for p in account["closed_trades"]
    ] != closes:
        raise ValueError("Trade arrays do not reconcile to the event journal")
    if account["accepted_trades"] != entry_records or account["closed_trades"] != close_records:
        raise ValueError("Trade-array contents differ from their independently checked journal records")
    if {p["trade_id"] for p in account["unresolved_holdings"]} != set(held):
        raise ValueError("Unresolved holdings mismatch")
    if {p["trade_id"] for p in account["pending_orders"]} != set(pending):
        raise ValueError("Pending reservation set mismatch")
    return {
        "journal_rows": journal_count,
        "entered_trades": len(entries),
        "closed_trades": len(closes),
        "daily_rows": len(account["daily"]),
        "cash": float(cash),
    }


def self_check():
    cases = [
        (".4019", 3, ".07", False, "-1.26", ".0543"),
        (".4019", 3, ".07", True, "1.15", ".0557"),
        (".4", 10, ".07", False, "-4.17", ".17"),
        ("0", 23, "0", True, "0", "0"),
        ("1", 23, "0", True, "23", "0"),
    ]
    for price, qty, coefficient, sell, balance, fee in cases:
        observed = cash_flow(price, qty, coefficient, sell)
        if observed != (D(balance), D(fee)):
            raise ValueError("Independent fee fixture failed")
    return {"independent_fee_fixtures": len(cases), "model_or_strategy_evaluated": False}


def run(run_id, archive_root, output):
    totals = Counter()
    parents = {}
    checked_ids = []
    with sqlite3.connect(f"file:{archive_root / 'archive.sqlite'}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row

        def read(record):
            content = (archive_root / "blobs" / record["body_sha256"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != record["body_sha256"]:
                raise ValueError("Archived source body hash mismatch")
            return content

        registration = db.execute("SELECT * FROM records WHERE id=?", (run_id,)).fetchone()
        if registration is None or registration["kind"] != "experiment_protocol":
            raise ValueError("Missing E023 registration")
        protocol = json.loads(read(registration))
        if not protocol["config"]["experiment"].startswith("E023-"):
            raise ValueError("Wrong experiment registration")
        for path, expected in protocol["source_hashes"].items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != expected:
                raise ValueError("Pinned registered source changed: " + path)
        records = db.execute(
            "SELECT * FROM records WHERE kind='e023_training_account_gzip' AND key LIKE ? ORDER BY id",
            (f"{run_id}:%",),
        ).fetchall()
        keys = set()
        expected_keys = {
            f"{run_id}:{parent_key.split(':', 1)[1]}:{fraction}"
            for parent_key in protocol["parent_records"]
            for fraction in protocol["config"]["risk_fractions"]
        }
        for record in records:
            if record["key"] in keys:
                raise ValueError("Duplicate frozen account artifact")
            keys.add(record["key"])
            payload = json.loads(gzip.decompress(read(record)))
            if (
                record["key"]
                != f"{run_id}:{payload['policy']['id']}:{payload['scenario']}:{payload['risk_fraction']}"
            ):
                raise ValueError("Account payload identity differs from archive identity")
            if record["key"] not in expected_keys:
                raise ValueError("Account does not belong to the registered candidate set")
            parent_id = payload["parent_record_id"]
            parent_key = (
                f"{protocol['config']['parent_registration']}:{payload['policy']['id']}:{payload['scenario']}"
            )
            pinned_parent = protocol["parent_records"][parent_key]
            if parent_id != pinned_parent["id"]:
                raise ValueError("Account used a parent other than its registered source")
            if parent_id not in parents:
                parent_record = db.execute("SELECT * FROM records WHERE id=?", (parent_id,)).fetchone()
                if parent_record is None or parent_record["body_sha256"] != pinned_parent["body_sha256"]:
                    raise ValueError("Registered parent source hash mismatch")
                parents[parent_id] = json.loads(read(parent_record))
            if (
                payload["policy"] != parents[parent_id]["policy"]
                or payload["scenario"] != parents[parent_id]["scenario"]
            ):
                raise ValueError("Account policy differs from its pinned parent policy")
            result = audit_account(payload, parents[parent_id], protocol["config"])
            totals.update({key: value for key, value in result.items() if key != "cash"})
            checked_ids.append(record["id"])
            if len(checked_ids) % 192 == 0:
                print(json.dumps({"accounts_audited": len(checked_ids)}), flush=True)
        result = {
            "generated_at": datetime.now(UTC).isoformat(),
            "run_record_id": run_id,
            "registration_sha256": registration["record_sha256"],
            "expected_training_accounts": protocol["training_accounts"],
            "training_accounts_audited": len(checked_ids),
            "all_registered_accounts_present": keys == expected_keys
            and len(checked_ids) == protocol["training_accounts"],
            "first_account_record_id": checked_ids[0] if checked_ids else None,
            "last_account_record_id": checked_ids[-1] if checked_ids else None,
            "totals": dict(totals),
            "independent_money_and_quantity_reproduction": bool(checked_ids),
            "network_requests": 0,
            "archive_writes": 0,
            "profitability_proven": False,
            "limits": "Checks hypothetical account arithmetic and its pinned parent decision endpoints. It does not establish historical quote availability, depth, actual fills, independent validation, or annual coverage.",
        }
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-record-id", type=int)
    parser.add_argument("--archive-root", type=Path, default=Path("data"))
    parser.add_argument("--output", type=Path, default=Path("reports/E023_audit.json"))
    parser.add_argument("--self-check", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_check:
        print(json.dumps(self_check()))
    elif arguments.run_record_id is None:
        parser.error("--run-record-id is required unless --self-check is used")
    else:
        run(arguments.run_record_id, arguments.archive_root, arguments.output)
