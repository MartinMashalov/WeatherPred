"""Deterministic trading-policy screens; conditional quotes never establish fills."""

import hashlib
import itertools
import math
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from weatherpred.archive import canonical
from weatherpred.fees import FeeAccumulator, FeeSchedule


def candidates(config):
    rows = []
    for family in config["families"]:
        if family in ("momentum", "reversal"):
            variants = itertools.product(config["lookback_hours"], config["move_thresholds"])
        else:
            thresholds = config[
                "favorite_thresholds" if family.startswith("favorite") else "longshot_thresholds"
            ]
            variants = [(0, value) for value in thresholds]
        for lookback, threshold in variants:
            for horizon, spread, exit_hours in itertools.product(
                config["signal_hours_before_end"],
                config["maximum_spreads"],
                config["exit_hours_after_entry"],
            ):
                row = {
                    "family": family,
                    "lookback_hours": lookback,
                    "threshold": threshold,
                    "horizon_hours": horizon,
                    "maximum_spread": spread,
                    "exit_hours": exit_hours,
                }
                row["id"] = hashlib.sha256(canonical(row).encode()).hexdigest()[:16]
                rows.append(row)
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("Candidate identity collision")
    return rows


def valid_quote(row):
    return (
        row is not None
        and math.isfinite(row["bid"])
        and math.isfinite(row["ask"])
        and 0 < row["bid"] < row["ask"] < 1
    )


def select_trade(signal_rows, policy):
    """Only current and earlier quotes enter this function; never outcomes."""
    selections = []
    for row in signal_rows:
        if set(row) - {"ticker", "bid", "ask", "past_bid", "past_ask"}:
            raise ValueError("Signal contains fields outside the information boundary")
        if not valid_quote(row) or row["ask"] - row["bid"] > policy["maximum_spread"] + 1e-12:
            continue
        mid = (row["bid"] + row["ask"]) / 2
        family = policy["family"]
        if family in ("momentum", "reversal"):
            past = {"bid": row.get("past_bid"), "ask": row.get("past_ask")}
            if past["bid"] is None or past["ask"] is None or not valid_quote(past):
                continue
            change = mid - (past["bid"] + past["ask"]) / 2
            if not 0.05 <= mid <= 0.95 or abs(change) + 1e-12 < policy["threshold"]:
                continue
            side = "yes" if (change > 0) == (family == "momentum") else "no"
            rank = abs(change)
        elif family.startswith("favorite_"):
            if mid + 1e-12 < policy["threshold"]:
                continue
            side, rank = family.split("_")[1], mid
        elif family.startswith("longshot_"):
            if mid > policy["threshold"] + 1e-12:
                continue
            side, rank = family.split("_")[1], mid
        else:
            raise ValueError("Unregistered trading family")
        selections.append(
            {
                "ticker": row["ticker"],
                "side": side,
                "rank": rank,
                "signal_ask": row["ask"] if side == "yes" else 1 - row["bid"],
            }
        )
    selections.sort(key=lambda r: (-r["rank"], r["ticker"], r["side"]))
    return selections[0] if selections else None


def order_cash(price, scenario, *, sell=False):
    schedule = FeeSchedule("quadratic", Decimal(scenario["fee_multiplier"]), Decimal("0.01"))
    result = FeeAccumulator(schedule).fill(Decimal(str(price)), 1, sell=sell)
    return float(result["balance_change"]), float(result["net_fee"])


def conditional_trade(selection, market, quotes, signal_ts, policy, scenario):
    """Evaluate a preselected unit order against later exact endpoints."""
    entry_ts = signal_ts + scenario["entry_delay_hours"] * 3600
    row = dict(selection, signal_ts=signal_ts, entry_ts=entry_ts, status="missing_entry_quote")
    if not market["open_ts"] <= signal_ts < entry_ts < market["close_ts"]:
        return dict(row, status="outside_market_hours")
    entry = quotes.get(entry_ts)
    if not valid_quote(entry):
        return row
    side, slip = selection["side"], float(scenario["slippage"])
    price = entry["ask"] if side == "yes" else 1 - entry["bid"]
    price = round(price + slip, 8)
    limit = min(0.99, round(selection["signal_ask"] + slip, 8))
    row.update(entry_limit=limit, entry_price=price)
    if price > limit + 1e-12 or not 0 < price < 1:
        return dict(row, status="entry_limit_not_met")
    change, fee = order_cash(price, scenario)
    cost = -change
    exit_ts = entry_ts + policy["exit_hours"] * 3600 if policy["exit_hours"] else None
    exit_quote = quotes.get(exit_ts) if exit_ts is not None else None
    if exit_ts is not None and exit_ts < market["close_ts"] and valid_quote(exit_quote):
        exit_price = exit_quote["bid"] if side == "yes" else 1 - exit_quote["ask"]
        exit_price = round(exit_price - slip, 8)
        if exit_price > 0:
            proceeds, exit_fee = order_cash(exit_price, scenario, sell=True)
            return dict(
                row,
                status="conditional_trade",
                entry_cost=cost,
                entry_fee=fee,
                exit_ts=exit_ts,
                exit_price=exit_price,
                exit_proceeds=proceeds,
                exit_fee=exit_fee,
                exit_kind="scheduled_quote",
                pnl=proceeds - cost,
                entry_source_record_id=market["candle_source_record_id"],
            )
    settled_ts = market.get("settled_ts")
    if settled_ts is None or settled_ts <= entry_ts:
        return dict(row, status="missing_or_invalid_settlement_time")
    payout = market["outcome"] if side == "yes" else 1 - market["outcome"]
    return dict(
        row,
        status="conditional_trade",
        entry_cost=cost,
        entry_fee=fee,
        exit_ts=settled_ts,
        exit_price=payout,
        exit_proceeds=payout,
        exit_fee=0,
        exit_kind="settlement" if exit_ts is None else "settlement_fallback",
        pnl=payout - cost,
        entry_source_record_id=market["candle_source_record_id"],
    )


def portfolio(trades, config, start_ts, end_ts):
    """Fixed unit allocation, locked cash and realized accounting on release days.

    Unreleased positions are carried at cost, explicitly not liquidation equity.
    All stations share one conservative risk cluster. No future outcome sizes trades.
    """
    initial = float(config["bankroll"])
    cash, locked = initial, 0.0
    actions, pending, booked, rejected, daily = [], {}, [], [], defaultdict(float)
    for i, trade in enumerate(trades):
        if start_ts <= trade["entry_ts"] < end_ts:
            actions.extend([(trade["entry_ts"], 1, i), (trade["exit_ts"], 0, i)])
    for timestamp, operation, i in sorted(actions):
        if timestamp >= end_ts:
            break
        t = trades[i]
        if operation == 0:
            if i not in pending:
                continue
            cost = pending.pop(i)
            cash += t["exit_proceeds"]
            locked -= cost
            day = datetime.fromtimestamp(timestamp, UTC).date().isoformat()
            daily[day] += t["pnl"]
            booked.append(t)
            continue
        equity, cost = cash + locked, t["entry_cost"]
        risk = min(config["max_cluster_fraction"], config["max_total_fraction"]) * equity
        if cost > cash + 1e-9 or cost > config["max_event_fraction"] * equity or locked + cost > risk:
            rejected.append(t)
            continue
        if any(trades[j]["event"] == t["event"] for j in pending):
            rejected.append(t)
            continue
        cash -= cost
        locked += cost
        pending[i] = cost
        if cash < -1e-8:
            raise ValueError("Conditional portfolio overdraft")
    return {
        "cash": cash,
        "locked_cost": locked,
        "cost_basis_equity": cash + locked,
        "released_trades": booked,
        "pending_trades": len(pending),
        "risk_rejections": len(rejected),
        "daily_realized_pnl": dict(daily),
    }
