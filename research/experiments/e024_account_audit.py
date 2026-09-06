"""Independent account reconciliation for E024; no production execution imports.

This checks money, quantities, event timing and supplied schedule provenance.
It does not establish that a supplied quote was executable or that a policy was
selected without future information. Those need the separate raw-data audit.
"""

import heapq
import math
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal

from research.experiments.e023_audit import cash_flow, check, choose_quantity

D = Decimal
ZERO = D(0)


def availability(source, mode):
    if not isinstance(source, dict) or not source:
        return None, "missing_provenance"
    if source.get("availability_verified") is True:
        value, missing = source.get("available_ts"), "missing_available_time"
    elif mode == "verified":
        return None, "unverified_availability"
    else:
        value, missing = source.get("assumed_available_ts"), "missing_assumed_available_time"
    if value is None:
        return None, missing
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid source availability clock")
    return value, None


def audit_account(account, trades, config, schedule=None, *, start_ts, end_ts):
    """Independently generate expected operations and consume the supplied ledger.

    Missing releases fail even if the account consistently omits their cash.
    Whole-account equivalence is tested with deliberate corruptions of amounts,
    schedules, source times and complete release records.
    """
    start, end = start_ts, end_ts
    if account["start_ts"] != start or account["end_ts"] != end:
        raise ValueError("Account calendar differs from independently supplied bounds")
    mode = config["mode"]
    if mode not in ("verified", "hypothetical") or account["execution_mode"] != mode:
        raise ValueError("Unexpected execution mode")
    if end <= start or config.get("balance_precision", "0.01") != "0.01":
        raise ValueError("Audit requires an increasing calendar and cent cash precision")
    cash = initial = D(str(config.get("initial_cash", 200)))
    check(account["initial_cash"], initial, "initial cash")
    entry_fee, exit_fee = D(str(config["entry_coefficient"])), D(str(config["exit_coefficient"]))
    cap = config.get("conditional_depth_cap")
    if mode == "hypothetical" and (isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0):
        raise ValueError("Missing hypothetical capacity assumption")
    if schedule is not None:
        if not schedule or schedule[0]["effective_ts"] != start:
            raise ValueError("Schedule must start at the account start")
        previous = -math.inf
        for row in schedule:
            at = row["effective_ts"]
            if not start <= at < end or at <= previous:
                raise ValueError("Schedule is not strictly chronological")
            previous = at
            if row["policy_id"] is None:
                if row["risk_fraction"] is not None:
                    raise ValueError("Cash cannot have a risk fraction")
            elif row["policy_id"] not in config["allowed_policy_ids"] or D(str(row["risk_fraction"])) not in {
                D(str(v)) for v in config["allowed_risk_fractions"]
            }:
                raise ValueError("Unregistered policy or risk fraction")

    def gate(source, at, kind):
        when, reason = availability(source, mode)
        if reason:
            return kind + "_" + reason
        return kind + "_not_available" if when > at else None

    orders = iter(account["orders"])
    operations = 0
    accepted, closed, reasons, notes, regimes = [], [], Counter(), [], []
    pending, held, rows, queue = {}, {}, {}, []
    fees = realized = ZERO
    peak = zero_peak = initial
    worst = zero_worst = ZERO
    killed, killed_at = False, None
    kill = D(str(config["drawdown_kill_fraction"]))
    for trade in trades:
        identifier = trade["trade_id"]
        if identifier in rows:
            raise ValueError("Repeated source intent")
        rows[identifier] = trade
        if not start <= trade["decision_ts"] < end:
            reasons["decision_outside_window"] += 1
            continue
        if trade["entry_ts"] < trade["decision_ts"]:
            raise ValueError("Entry precedes decision")
        heapq.heappush(queue, (trade["decision_ts"], 1, identifier))
        heapq.heappush(queue, (trade["entry_ts"], 2, identifier))

    def state():
        reserve = sum((p["cost"] for p in pending.values()), ZERO)
        principal = sum((p["principal"] for p in held.values()), ZERO)
        return {
            "cash": cash,
            "reserved_cash": reserve,
            "total_cash": cash + reserve,
            "open_principal": principal,
            "open_entry_cost": sum((p["cost"] for p in held.values()), ZERO),
            "cost_basis_equity": cash + reserve + principal,
            "zero_mark_equity": cash + reserve,
        }

    def consume(identifier, at, stage, status, **expected):
        nonlocal operations
        row = next(orders, None)
        if row is None:
            raise ValueError(f"Missing expected {stage} for {identifier} at {at}")
        for key, value in {
            "trade_id": identifier,
            "timestamp": at,
            "stage": stage,
            "status": status,
            **expected,
        }.items():
            if isinstance(value, D):
                check(row[key], value, f"{identifier} {key}")
            elif row.get(key) != value:
                raise ValueError(f"Incorrect {stage} {key} for {identifier}")
        operations += 1
        return {k: v for k, v in row.items() if k not in ("stage", "status", "timestamp")}

    def reject(identifier, at, reason, stage):
        nonlocal cash
        if identifier in pending:
            cash += pending.pop(identifier)["cost"]
        reasons[reason] += 1
        consume(identifier, at, stage, "not_filled", reason=reason)

    def monitor(at):
        nonlocal peak, zero_peak, worst, zero_worst, killed, killed_at
        now = state()
        peak = max(peak, now["cost_basis_equity"])
        zero_peak = max(zero_peak, now["zero_mark_equity"])
        drawdown = 1 - now["cost_basis_equity"] / peak
        worst = max(worst, drawdown)
        zero_worst = max(zero_worst, 1 - now["zero_mark_equity"] / zero_peak)
        if not killed and drawdown >= kill:
            killed, killed_at = True, at
            for identifier in sorted(pending):
                reject(identifier, at, "drawdown_cancelled_pending_order", "kill")
        if cash < 0:
            raise ValueError("Independent audit found an overdraft")

    def decision(identifier, at):
        nonlocal cash
        trade = rows[identifier]
        if schedule is None:
            risk = D(str(config["risk_fraction"]))
            regime = None
        else:
            regime = next(row for row in reversed(schedule) if row["effective_ts"] <= at)
            regimes.append(
                {
                    "trade_id": identifier,
                    "decision_ts": at,
                    "intent_policy_id": trade["policy_id"],
                    "effective_ts": regime["effective_ts"],
                    "selected_policy_id": regime["policy_id"],
                    "risk_fraction": str(D(str(regime["risk_fraction"])))
                    if regime["risk_fraction"] is not None
                    else None,
                }
            )
            risk = D(str(regime["risk_fraction"])) if regime["risk_fraction"] is not None else ZERO
        reason = None
        if killed:
            reason = "drawdown_kill_active"
        elif regime is not None and regime["policy_id"] is None:
            reason = "scheduled_cash"
        elif regime is not None and regime["policy_id"] != trade["policy_id"]:
            reason = "policy_not_selected_at_decision"
        else:
            reason = gate(trade.get("signal_provenance"), at, "signal")
        if reason:
            reject(identifier, at, reason, "decision")
            return
        if trade.get("limit_price") is None:
            reject(identifier, at, "missing_decision_limit", "decision")
            return
        price = D(str(trade["limit_price"]))
        if not ZERO < price < 1:
            reject(identifier, at, "invalid_decision_limit", "decision")
            return
        exposures = [(rows[key], p["cost"]) for store in (pending, held) for key, p in store.items()]
        equity = state()["cost_basis_equity"]
        bounds = {"cash_limit": cash, "risk_fraction_limit": equity * risk}
        for key, field, fraction in (
            ("event_cap", "event", "max_event_fraction"),
            ("cluster_cap", "cluster", "max_cluster_fraction"),
            ("total_cap", None, "max_total_fraction"),
        ):
            exposure = sum(
                (v for other, v in exposures if field is None or other[field] == trade[field]), ZERO
            )
            bounds[key] = D(str(config[fraction])) * equity - exposure
        binding = min(bounds, key=bounds.get)
        budget = max(ZERO, bounds[binding])
        maximum = cap if mode == "hypothetical" else max(1, int(budget / price) + 1)
        quantity = choose_quantity(budget, price, entry_fee, maximum)
        if quantity == 0:
            reason = "below_one_contract_risk_budget" if binding == "risk_fraction_limit" else binding
            reject(identifier, at, reason, "decision")
            return
        cost = -cash_flow(price, quantity, entry_fee)[0]
        consume(
            identifier,
            at,
            "decision",
            "reserved",
            intended_quantity=quantity,
            limit_price=price,
            reserved_cash=cost,
            sizing_cost_basis_equity=equity,
            binding_limit=binding,
            signal_provenance=trade.get("signal_provenance"),
        )
        pending[identifier] = {"quantity": quantity, "cost": cost, "limit": price}
        cash -= cost

    def entry(identifier, at):
        nonlocal cash, fees
        if identifier not in pending:
            return
        trade, order = rows[identifier], pending[identifier]
        price = trade.get("entry_price")
        reason = (
            "missing_entry_quote"
            if price is None
            else gate(trade.get("entry_price_provenance"), at, "entry_price")
        )
        if reason:
            reject(identifier, at, reason, "entry")
            return
        price = D(str(price))
        if not ZERO < price < 1 or price > order["limit"]:
            reject(identifier, at, "entry_limit_not_met", "entry")
            return
        depth = trade.get("available_quantity")
        if depth is None and mode == "verified":
            reject(identifier, at, "unknown_entry_depth", "entry")
            return
        capacity = cap if depth is None else int(D(str(depth)).to_integral_value(rounding=ROUND_FLOOR))
        if mode == "hypothetical":
            capacity = min(capacity, cap)
        quantity = choose_quantity(order["cost"], price, entry_fee, min(capacity, order["quantity"]))
        if quantity <= 0:
            reject(identifier, at, "no_integer_entry_capacity", "entry")
            return
        change, charge = cash_flow(price, quantity, entry_fee)
        cost = -change
        record = consume(
            identifier,
            at,
            "entry",
            "filled",
            event=trade["event"],
            cluster=trade["cluster"],
            side=trade["side"],
            decision_ts=trade["decision_ts"],
            entry_ts=at,
            intended_quantity=order["quantity"],
            quantity=quantity,
            unfilled_quantity=order["quantity"] - quantity,
            entry_price=price,
            entry_cost=cost,
            entry_principal=price * quantity,
            entry_fee=charge,
            execution_mode=mode,
            available_quantity=depth,
            entry_price_provenance=trade.get("entry_price_provenance"),
        )
        cash += order["cost"] - cost
        fees += charge
        del pending[identifier]
        held[identifier] = {
            "quantity": quantity,
            "cost": cost,
            "principal": price * quantity,
            "fee": charge,
            "entry_ts": at,
        }
        accepted.append(record)
        if any(
            trade.get(k) is None for k in ("exit_ts", "terminal_cash_per_contract", "outcome_available_ts")
        ):
            notes.append({"trade_id": identifier, "reason": "missing_terminal_endpoint"})
        elif trade["exit_ts"] <= at:
            notes.append({"trade_id": identifier, "reason": "terminal_time_not_after_entry"})
        else:
            receipt, error = availability(trade.get("terminal_provenance"), mode)
            if error:
                notes.append({"trade_id": identifier, "reason": "terminal_" + error})
            else:
                heapq.heappush(
                    queue, (max(trade["exit_ts"], trade["outcome_available_ts"], receipt), 0, identifier)
                )

    def release(identifier, at):
        nonlocal cash, fees, realized
        trade, position = rows[identifier], held[identifier]
        kind = trade.get("terminal_kind")
        reason = None
        if kind not in ("sale", "settlement"):
            reason = "invalid_terminal_kind"
        elif kind == "sale":
            depth = trade.get("terminal_available_quantity")
            if depth is None and mode == "verified":
                reason = "unknown_terminal_depth"
            else:
                depth = cap if depth is None else int(D(str(depth)).to_integral_value(rounding=ROUND_FLOOR))
                if mode == "hypothetical":
                    depth = min(depth, cap)
                if depth < position["quantity"]:
                    reason = "insufficient_terminal_depth"
        if reason:
            notes.append({"trade_id": identifier, "reason": reason})
            return
        value = D(str(trade["terminal_cash_per_contract"]))
        if not ZERO <= value <= 1:
            raise ValueError("Invalid terminal cash")
        proceeds, charge = cash_flow(value, position["quantity"], exit_fee if kind == "sale" else ZERO, True)
        if proceeds < 0:
            notes.append({"trade_id": identifier, "reason": "negative_net_exit_proceeds"})
            return
        pnl = proceeds - position["cost"]
        record = consume(
            identifier,
            at,
            "release",
            "closed",
            event=trade["event"],
            cluster=trade["cluster"],
            side=trade["side"],
            entry_ts=position["entry_ts"],
            exit_ts=trade["exit_ts"],
            cash_release_ts=at,
            quantity=position["quantity"],
            entry_cost=position["cost"],
            entry_fee=position["fee"],
            terminal_kind=kind,
            terminal_cash_per_contract=value,
            terminal_available_quantity=trade.get("terminal_available_quantity"),
            conditional_depth_cap=cap if mode == "hypothetical" else None,
            exit_proceeds=proceeds,
            exit_fee=charge,
            pnl=pnl,
            terminal_provenance=trade.get("terminal_provenance"),
        )
        cash += proceeds
        fees += charge
        realized += pnl
        del held[identifier]
        closed.append(record)

    boundary = min(end, (math.floor(start / 86400) + 1) * 86400)
    snapshots = iter(account["daily"])
    previous_cost = previous_zero = initial
    previous_realized = previous_fees = ZERO
    previous_closed = 0
    days = 0
    while True:
        while queue and queue[0][0] < boundary:
            at, operation, identifier = heapq.heappop(queue)
            (release, decision, entry)[operation](identifier, at)
            monitor(at)
        daily = next(snapshots, None)
        if daily is None or daily["timestamp"] != boundary:
            raise ValueError("Missing or shifted daily account snapshot")
        if daily["date"] != datetime.fromtimestamp(boundary - 0.000001, UTC).date().isoformat():
            raise ValueError("Daily date differs from its UTC account interval")
        now = state()
        for key, value in now.items():
            check(daily[key], value, "daily " + key)
        cost, zero = now["cost_basis_equity"], now["zero_mark_equity"]
        for key, value in {
            "cost_basis_log_return": math.log(float(cost / previous_cost))
            if cost > 0 and previous_cost > 0
            else None,
            "zero_mark_log_return": math.log(float(zero / previous_zero))
            if zero > 0 and previous_zero > 0
            else None,
            "log_cost_basis_equity": math.log(float(cost)) if cost > 0 else None,
        }.items():
            if value is None:
                if daily[key] is not None:
                    raise ValueError("Undefined log equity reported as finite")
            elif daily[key] is None or abs(daily[key] - value) > 1e-12:
                raise ValueError("Incorrect daily " + key)
        for key, value in {
            "daily_realized_pnl": realized - previous_realized,
            "daily_fees": fees - previous_fees,
            "cost_basis_drawdown": 1 - cost / peak,
            "zero_mark_drawdown": 1 - zero / zero_peak,
        }.items():
            check(daily[key], value, key)
        if daily["released_trades"] != len(closed) - previous_closed or daily["drawdown_killed"] != killed:
            raise ValueError("Daily release or halt count differs")
        days += 1
        previous_cost, previous_zero = cost, zero
        previous_realized, previous_fees, previous_closed = realized, fees, len(closed)
        if boundary == end:
            break
        boundary = min(end, boundary + 86400)
    if next(snapshots, None) is not None or next(orders, None) is not None:
        raise ValueError("Unexpected extra daily snapshot or journal operation")
    for key, value in {
        **state(),
        "realized_pnl": realized,
        "total_fees": fees,
        "max_cost_basis_drawdown": worst,
        "max_zero_mark_drawdown": zero_worst,
    }.items():
        check(account[key], value, "terminal " + key)
    if account["drawdown_killed"] != killed or account["drawdown_kill_ts"] != killed_at:
        raise ValueError("Drawdown state differs")
    if account["no_trade_reasons"] != dict(reasons) or account["terminal_notes"] != notes:
        raise ValueError("Abstention or unresolved-terminal explanations differ")
    if account["accepted_trades"] != accepted or account["closed_trades"] != closed:
        raise ValueError("Trade arrays differ from checked journal entries")
    expected_pending = [
        {"trade_id": key, "quantity": p["quantity"], "reserved_cash": float(p["cost"])}
        for key, p in sorted(pending.items())
    ]
    expected_held = [
        {
            "trade_id": key,
            "quantity": p["quantity"],
            "event": rows[key]["event"],
            "cluster": rows[key]["cluster"],
            "entry_ts": p["entry_ts"],
            "principal": float(p["principal"]),
            "entry_cost": float(p["cost"]),
            "conservative_mark": 0.0,
        }
        for key, p in sorted(held.items())
    ]
    if account["pending_orders"] != expected_pending or account["unresolved_holdings"] != expected_held:
        raise ValueError("Pending or held inventory differs from independent reconstruction")
    if schedule is not None:
        metadata = account["schedule_metadata"]
        if metadata["decision_regimes"] != regimes or metadata["cash_initializations"] != 1:
            raise ValueError("Account schedule metadata differs from decision-time choices")
        expected_ids = {
            key: trade["policy_id"] for key, trade in rows.items() if start <= trade["decision_ts"] < end
        }
        if metadata["trade_policy_ids"] != expected_ids:
            raise ValueError("Schedule intent-policy identities differ")
    return {
        "journal_rows": operations,
        "daily_rows": days,
        "entries": len(accepted),
        "releases": len(closed),
        "ending_cash": float(cash),
        "accounting_reproduced": True,
        "historical_fills_verified": False,
    }
