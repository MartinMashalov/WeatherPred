"""Causal integer-order bankroll replay with explicit execution assumptions.

Timestamps are Unix seconds. Entry/exit prices are for the purchased YES or NO
side already: this kernel never complements them. terminal_cash_per_contract
is the gross sale price or settlement payout, before aggregate exit fees.

A provenance object has actual available_ts, availability_verified, source IDs,
and (only for hypothetical replay) an explicit assumed_available_ts. Merely
knowing a candle's timestamp does not establish historical availability/depth.
"""

import heapq
import math
from collections import Counter
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

D = Decimal
ZERO = D(0)


def decimal(value):
    if isinstance(value, bool):
        raise TypeError("Boolean values are not money or quantities")
    result = D(str(value))
    if not result.is_finite():
        raise ValueError("Money and quantities must be finite")
    return result


def timestamp(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Timestamps must be finite Unix seconds")
    return value


def aggregate_cash(price, quantity, coefficient, *, sell=False, precision="0.01"):
    """One aggregate order, matching FeeAccumulator's rounding arithmetic.

    The model fee first rounds up to six decimals, then the signed aggregate
    balance change rounds down to account precision. It is not q times the
    one-contract rounded cost. This represents a single aggregate fill, not
    several independent orders or a reconstruction of unknown fill slices.
    """
    p, c, quantum = decimal(price), decimal(coefficient), decimal(precision)
    if not ZERO <= p <= 1 or c < 0 or quantum not in (D("0.01"), D("0.0001")):
        raise ValueError("Invalid price, fee coefficient, or account precision")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("Contract quantity must be a positive integer")
    gross = p * quantity
    fee = (c * quantity * p * (1 - p)).quantize(D("0.000001"), rounding=ROUND_CEILING)
    revenue = gross if sell else -gross
    change = (revenue - fee).quantize(quantum, rounding=ROUND_FLOOR)
    rounding = revenue - fee - change
    # A single aggregate fill starts with zero carried rounding. Retain the
    # documented rebate expression for exact agreement with FeeAccumulator.
    rebate = min(
        rounding.quantize(quantum, rounding=ROUND_FLOOR),
        (fee + rounding).quantize(quantum, rounding=ROUND_FLOOR),
    )
    change += rebate
    return {
        "cash_change": change,
        "gross": gross,
        "trade_fee": fee,
        "rounding_fee": rounding,
        "rebate": rebate,
        "net_fee": revenue - change,
    }


def _quantity(budget, price, coefficient, precision, maximum=None):
    if budget <= 0:
        return 0
    upper = int((budget / price).to_integral_value(rounding=ROUND_FLOOR))
    if maximum is not None:
        upper = min(upper, maximum)
    lower = 0
    while lower < upper:
        middle = (lower + upper + 1) // 2
        debit = -aggregate_cash(price, middle, coefficient, precision=precision)["cash_change"]
        if debit <= budget:
            lower = middle
        else:
            upper = middle - 1
    return lower


def _provenance_time(provenance, mode):
    if not isinstance(provenance, dict) or not provenance:
        return None, "missing_provenance"
    if provenance.get("availability_verified") is True:
        value = provenance.get("available_ts")
        return (timestamp(value), None) if value is not None else (None, "missing_available_time")
    if mode == "verified":
        return None, "unverified_availability"
    value = provenance.get("assumed_available_ts")
    return (timestamp(value), None) if value is not None else (None, "missing_assumed_available_time")


def _gate(provenance, at, mode, kind):
    available, reason = _provenance_time(provenance, mode)
    if reason:
        return kind + "_" + reason
    return kind + "_not_available" if available > at else None


def replay(trades, config, start_ts, end_ts):
    """Replay only decisions in [start,end), starting with cash and no inventory.

    Required config: risk_fraction, max_event_fraction, max_cluster_fraction,
    max_total_fraction, entry_coefficient, exit_coefficient, mode. Optional:
    initial_cash=200, balance_precision=.01, drawdown_kill_fraction=.20,
    conditional_depth_cap (required positive integer in hypothetical mode).

    Required order fields: trade_id,event,cluster,side,decision_ts,entry_ts,
    limit_price,signal_provenance. Entry fields are inspected at entry only:
    entry_price (may be None), available_quantity (None means unknown), and
    entry_price_provenance. Terminal fields may all be None: exit_ts,
    terminal_cash_per_contract, terminal_kind, outcome_available_ts,
    terminal_provenance; sales also need terminal_available_quantity in verified
    mode. The hypothetical per-order cap bounds intents and both execution legs.
    Missing future endpoints never suppress an earlier
    order or release cash from a valid but unresolved holding.
    """
    start, end = timestamp(start_ts), timestamp(end_ts)
    if end <= start:
        raise ValueError("Replay end must follow its start")
    initial = decimal(config.get("initial_cash", "200"))
    if initial <= 0:
        raise ValueError("Initial cash must be positive")
    mode = config["mode"]
    if mode not in ("verified", "hypothetical"):
        raise ValueError("Mode must be verified or hypothetical")
    fractions = {
        key: decimal(config[key])
        for key in ("risk_fraction", "max_event_fraction", "max_cluster_fraction", "max_total_fraction")
    }
    if any(not ZERO < value <= 1 for value in fractions.values()):
        raise ValueError("Risk and exposure fractions must lie in (0,1]")
    kill_fraction = decimal(config.get("drawdown_kill_fraction", ".20"))
    if not ZERO < kill_fraction <= 1:
        raise ValueError("Drawdown kill fraction must lie in (0,1]")
    precision = str(config.get("balance_precision", "0.01"))
    entry_coefficient, exit_coefficient = (
        decimal(config["entry_coefficient"]),
        decimal(config["exit_coefficient"]),
    )
    aggregate_cash(".5", 1, entry_coefficient, precision=precision)
    aggregate_cash(".5", 1, exit_coefficient, sell=True, precision=precision)
    depth_cap = config.get("conditional_depth_cap")
    if mode == "hypothetical" and (
        isinstance(depth_cap, bool) or not isinstance(depth_cap, int) or depth_cap <= 0
    ):
        raise ValueError("Hypothetical mode needs an explicit positive integer conditional_depth_cap")
    cash = initial
    pending, held, entries, closed, order_records = {}, {}, [], [], []
    no_trade, terminal_notes = Counter(), []
    queue, by_id, seen = [], {}, set()
    killed, kill_ts = False, None
    cost_peak, zero_peak = initial, initial
    max_cost_drawdown = max_zero_drawdown = ZERO
    total_fees = realized = ZERO
    for original in trades:
        trade = dict(original)
        identifier = trade["trade_id"]
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise ValueError("Trade IDs must be unique nonempty strings")
        seen.add(identifier)
        decision, entry = timestamp(trade["decision_ts"]), timestamp(trade["entry_ts"])
        if entry < decision:
            raise ValueError("Entry cannot precede its decision")
        if trade["side"] not in ("yes", "no") or not all(
            isinstance(trade.get(k), str) and trade[k] for k in ("event", "cluster")
        ):
            raise ValueError("Each trade needs a valid side, event, and risk cluster")
        if not start <= decision < end:
            no_trade["decision_outside_window"] += 1
            continue
        by_id[identifier] = trade
        heapq.heappush(queue, (decision, 1, identifier, "decision"))
        heapq.heappush(queue, (entry, 2, identifier, "entry"))

    def snapshot():
        reserved = sum((order["reserved"] for order in pending.values()), ZERO)
        principal = sum((position["principal"] for position in held.values()), ZERO)
        held_cost = sum((position["entry_cost"] for position in held.values()), ZERO)
        return {
            "cash": cash,
            "reserved_cash": reserved,
            "total_cash": cash + reserved,
            "open_principal": principal,
            "open_entry_cost": held_cost,
            "cost_basis_equity": cash + reserved + principal,
            "zero_mark_equity": cash + reserved,
        }

    def exposure(field=None, value=None):
        def matches(item):
            return field is None or item["trade"][field] == value

        return sum((p["reserved"] for p in pending.values() if matches(p)), ZERO) + sum(
            (p["entry_cost"] for p in held.values() if matches(p)), ZERO
        )

    def reject(identifier, when, reason, stage):
        nonlocal cash
        order = pending.pop(identifier, None)
        if order is not None:
            cash += order["reserved"]
        no_trade[reason] += 1
        order_records.append(
            {
                "trade_id": identifier,
                "timestamp": when,
                "stage": stage,
                "status": "not_filled",
                "reason": reason,
            }
        )

    def monitor(when):
        nonlocal killed, kill_ts, cost_peak, zero_peak, max_cost_drawdown, max_zero_drawdown, cash
        state = snapshot()
        cost_peak = max(cost_peak, state["cost_basis_equity"])
        zero_peak = max(zero_peak, state["zero_mark_equity"])
        cost_dd = 1 - state["cost_basis_equity"] / cost_peak
        zero_dd = 1 - state["zero_mark_equity"] / zero_peak
        max_cost_drawdown, max_zero_drawdown = (
            max(max_cost_drawdown, cost_dd),
            max(max_zero_drawdown, zero_dd),
        )
        if not killed and cost_dd >= kill_fraction:
            killed, kill_ts = True, when
            for identifier in sorted(pending):
                reject(identifier, when, "drawdown_cancelled_pending_order", "kill")
        if cash < 0:
            raise ValueError("Replay overdraft")

    def decide(identifier, when):
        nonlocal cash
        trade = by_id[identifier]
        if killed:
            reject(identifier, when, "drawdown_kill_active", "decision")
            return
        reason = _gate(trade.get("signal_provenance"), when, mode, "signal")
        if reason:
            reject(identifier, when, reason, "decision")
            return
        if trade.get("limit_price") is None:
            reject(identifier, when, "missing_decision_limit", "decision")
            return
        limit = decimal(trade["limit_price"])
        if not ZERO < limit < 1:
            reject(identifier, when, "invalid_decision_limit", "decision")
            return
        equity = snapshot()["cost_basis_equity"]
        bounds = {
            "cash_limit": cash,
            "risk_fraction_limit": fractions["risk_fraction"] * equity,
            "event_cap": fractions["max_event_fraction"] * equity - exposure("event", trade["event"]),
            "cluster_cap": fractions["max_cluster_fraction"] * equity - exposure("cluster", trade["cluster"]),
            "total_cap": fractions["max_total_fraction"] * equity - exposure(),
        }
        binding = min(bounds, key=bounds.get)
        budget = max(ZERO, bounds[binding])
        quantity = _quantity(
            budget, limit, entry_coefficient, precision, maximum=depth_cap if mode == "hypothetical" else None
        )
        if quantity == 0:
            reject(
                identifier,
                when,
                binding if binding != "risk_fraction_limit" else "below_one_contract_risk_budget",
                "decision",
            )
            return
        debit = -aggregate_cash(limit, quantity, entry_coefficient, precision=precision)["cash_change"]
        cash -= debit
        pending[identifier] = {"trade": trade, "quantity": quantity, "reserved": debit, "limit": limit}
        order_records.append(
            {
                "trade_id": identifier,
                "timestamp": when,
                "stage": "decision",
                "status": "reserved",
                "intended_quantity": quantity,
                "limit_price": float(limit),
                "reserved_cash": float(debit),
                "sizing_cost_basis_equity": float(equity),
                "binding_limit": binding,
                "signal_provenance": trade.get("signal_provenance"),
            }
        )

    def enter(identifier, when):
        nonlocal cash, total_fees
        if identifier not in pending:
            return
        order, trade = pending[identifier], by_id[identifier]
        if trade.get("entry_price") is None:
            reject(identifier, when, "missing_entry_quote", "entry")
            return
        reason = _gate(trade.get("entry_price_provenance"), when, mode, "entry_price")
        if reason:
            reject(identifier, when, reason, "entry")
            return
        price = decimal(trade["entry_price"])
        if not ZERO < price < 1 or price > order["limit"]:
            reject(identifier, when, "entry_limit_not_met", "entry")
            return
        available = trade.get("available_quantity")
        if available is None:
            if mode == "verified":
                reject(identifier, when, "unknown_entry_depth", "entry")
                return
            capacity = depth_cap
        else:
            capacity = int(decimal(available).to_integral_value(rounding=ROUND_FLOOR))
            if capacity < 0:
                raise ValueError("Available depth cannot be negative")
            if mode == "hypothetical":
                capacity = min(capacity, depth_cap)
        quantity = _quantity(
            order["reserved"], price, entry_coefficient, precision, maximum=min(capacity, order["quantity"])
        )
        if quantity == 0:
            reject(identifier, when, "no_integer_entry_capacity", "entry")
            return
        movement = aggregate_cash(price, quantity, entry_coefficient, precision=precision)
        cost = -movement["cash_change"]
        cash += order["reserved"] - cost
        pending.pop(identifier)
        total_fees += movement["net_fee"]
        position = {
            "trade": trade,
            "quantity": quantity,
            "entry_cost": cost,
            "principal": movement["gross"],
            "entry_fee": movement["net_fee"],
            "entry_price": price,
            "entry_ts": when,
        }
        held[identifier] = position
        record = {
            "trade_id": identifier,
            "event": trade["event"],
            "cluster": trade["cluster"],
            "side": trade["side"],
            "decision_ts": trade["decision_ts"],
            "entry_ts": when,
            "intended_quantity": order["quantity"],
            "quantity": quantity,
            "unfilled_quantity": order["quantity"] - quantity,
            "entry_price": float(price),
            "entry_cost": float(cost),
            "entry_principal": float(movement["gross"]),
            "entry_fee": float(movement["net_fee"]),
            "execution_mode": mode,
            "available_quantity": available,
            "entry_price_provenance": trade.get("entry_price_provenance"),
        }
        entries.append(record)
        order_records.append({**record, "timestamp": when, "stage": "entry", "status": "filled"})
        # Future endpoint fields are first inspected after the order has filled.
        if (
            trade.get("exit_ts") is None
            or trade.get("terminal_cash_per_contract") is None
            or trade.get("outcome_available_ts") is None
        ):
            terminal_notes.append({"trade_id": identifier, "reason": "missing_terminal_endpoint"})
            return
        exit_time, outcome_time = timestamp(trade["exit_ts"]), timestamp(trade["outcome_available_ts"])
        if exit_time <= when:
            terminal_notes.append({"trade_id": identifier, "reason": "terminal_time_not_after_entry"})
            return
        provenance_time, error = _provenance_time(trade.get("terminal_provenance"), mode)
        if error:
            terminal_notes.append({"trade_id": identifier, "reason": "terminal_" + error})
            return
        release = max(exit_time, outcome_time, provenance_time)
        heapq.heappush(queue, (release, 0, identifier, "release"))

    def release(identifier, when):
        nonlocal cash, total_fees, realized
        if identifier not in held:
            return
        position = held[identifier]
        trade, quantity = position["trade"], position["quantity"]
        kind = trade.get("terminal_kind")
        if kind not in ("sale", "settlement"):
            terminal_notes.append({"trade_id": identifier, "reason": "invalid_terminal_kind"})
            return
        if kind == "sale":
            capacity = trade.get("terminal_available_quantity")
            if capacity is None and mode == "verified":
                terminal_notes.append({"trade_id": identifier, "reason": "unknown_terminal_depth"})
                return
            capacity = (
                depth_cap
                if capacity is None
                else int(decimal(capacity).to_integral_value(rounding=ROUND_FLOOR))
            )
            if mode == "hypothetical":
                capacity = min(capacity, depth_cap)
            if capacity < quantity:
                terminal_notes.append({"trade_id": identifier, "reason": "insufficient_terminal_depth"})
                return
        value = decimal(trade["terminal_cash_per_contract"])
        if not ZERO <= value <= 1:
            raise ValueError("Terminal cash per contract must lie between zero and one")
        coefficient = exit_coefficient if kind == "sale" else ZERO
        movement = aggregate_cash(value, quantity, coefficient, sell=True, precision=precision)
        proceeds = movement["cash_change"]
        # Every position retains enough cash in its entry cost to cover its
        # maximum loss. A sale whose supplied fees exceed proceeds is refused.
        if proceeds < 0:
            terminal_notes.append({"trade_id": identifier, "reason": "negative_net_exit_proceeds"})
            return
        held.pop(identifier)
        cash += proceeds
        total_fees += movement["net_fee"]
        pnl = proceeds - position["entry_cost"]
        realized += pnl
        record = {
            "trade_id": identifier,
            "event": trade["event"],
            "cluster": trade["cluster"],
            "side": trade["side"],
            "entry_ts": position["entry_ts"],
            "exit_ts": trade["exit_ts"],
            "cash_release_ts": when,
            "quantity": quantity,
            "entry_cost": float(position["entry_cost"]),
            "entry_fee": float(position["entry_fee"]),
            "terminal_kind": kind,
            "terminal_cash_per_contract": float(value),
            "terminal_available_quantity": trade.get("terminal_available_quantity"),
            "conditional_depth_cap": depth_cap if mode == "hypothetical" else None,
            "exit_proceeds": float(proceeds),
            "exit_fee": float(movement["net_fee"]),
            "pnl": float(pnl),
            "terminal_provenance": trade.get("terminal_provenance"),
        }
        closed.append(record)
        order_records.append({**record, "timestamp": when, "stage": "release", "status": "closed"})

    daily = []
    previous_cost = previous_zero = initial
    prior_realized = prior_fees = ZERO
    prior_closed = 0
    boundary = min(end, (math.floor(start / 86400) + 1) * 86400)
    while True:
        while queue and queue[0][0] < boundary:
            when, _, identifier, operation = heapq.heappop(queue)
            {"decision": decide, "entry": enter, "release": release}[operation](identifier, when)
            monitor(when)
        state = snapshot()
        cost, zero = state["cost_basis_equity"], state["zero_mark_equity"]
        daily.append(
            {
                "date": datetime.fromtimestamp(boundary - 0.000001, UTC).date().isoformat(),
                "timestamp": boundary,
                **{key: float(value) for key, value in state.items()},
                "cost_basis_log_return": math.log(float(cost / previous_cost))
                if cost > 0 and previous_cost > 0
                else None,
                "zero_mark_log_return": math.log(float(zero / previous_zero))
                if zero > 0 and previous_zero > 0
                else None,
                "log_cost_basis_equity": math.log(float(cost)) if cost > 0 else None,
                "daily_realized_pnl": float(realized - prior_realized),
                "daily_fees": float(total_fees - prior_fees),
                "released_trades": len(closed) - prior_closed,
                "cost_basis_drawdown": float(1 - cost / cost_peak),
                "zero_mark_drawdown": float(1 - zero / zero_peak),
                "drawdown_killed": killed,
            }
        )
        previous_cost, previous_zero = cost, zero
        prior_realized, prior_fees, prior_closed = realized, total_fees, len(closed)
        if boundary == end:
            break
        boundary = min(end, boundary + 86400)
    state = snapshot()
    unresolved = [
        {
            "trade_id": identifier,
            "quantity": p["quantity"],
            "event": p["trade"]["event"],
            "cluster": p["trade"]["cluster"],
            "entry_ts": p["entry_ts"],
            "principal": float(p["principal"]),
            "entry_cost": float(p["entry_cost"]),
            "conservative_mark": 0.0,
        }
        for identifier, p in sorted(held.items())
    ]
    return {
        "initial_cash": float(initial),
        "start_ts": start,
        "end_ts": end,
        "execution_mode": mode,
        **{key: float(value) for key, value in state.items()},
        "realized_pnl": float(realized),
        "total_fees": float(total_fees),
        "accepted_trades": entries,
        "closed_trades": closed,
        "unresolved_holdings": unresolved,
        "pending_orders": [
            {"trade_id": identifier, "quantity": p["quantity"], "reserved_cash": float(p["reserved"])}
            for identifier, p in sorted(pending.items())
        ],
        "orders": order_records,
        "no_trade_reasons": dict(sorted(no_trade.items())),
        "terminal_notes": terminal_notes,
        "daily": daily,
        "max_cost_basis_drawdown": float(max_cost_drawdown),
        "max_zero_mark_drawdown": float(max_zero_drawdown),
        "drawdown_killed": killed,
        "drawdown_kill_ts": kill_ts,
        "cost_marking": "Cash plus cancellable order reservations plus open purchase principal; fees expensed immediately. This is bookkeeping at cost, not liquidation equity.",
        "conservative_marking": "Cash plus cancellable reservations; all unresolved held contracts marked zero, irrespective of known future labels.",
        "startup": "Starts flat; decisions before start are excluded, with no inherited positions or orders.",
        "same_timestamp_order": "Releases, then decisions ordered by trade_id, then entries ordered by trade_id.",
        "fee_assumption": "Supplied quadratic coefficients; one aggregate fill per entry/exit, account rounding applied to total debit/credit. No historical fee claim is inferred.",
        "profitability_proven": False,
    }
