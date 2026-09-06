"""Describe 16 predeclared E023 training accounts without rerunning policies.

The two policy IDs were selected after observing their training results. This is
an explanation of that selection, never an independent validation or promotion.
"""

import gzip
import hashlib
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from weatherpred.archive import Archive, canonical
from weatherpred.timeutil import iso, utcnow

DECLARATION_ID = 100153
KEY = "E023-fixed-winners-retrospective-diagnostics-v1"
CITIES = (
    "KXHIGHAUS",
    "KXHIGHCHI",
    "KXHIGHDEN",
    "KXHIGHHOU",
    "KXHIGHLAX",
    "KXHIGHMIA",
    "KXHIGHNY",
    "KXHIGHPHIL",
)
D = lambda value: Decimal(str(value))


def day(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).date().isoformat()


def amount(rows, key):
    return sum((D(row[key]) for row in rows), Decimal(0))


def describe_trades(rows):
    wins = [r for r in rows if D(r["pnl"]) > 0]
    losses = [r for r in rows if D(r["pnl"]) < 0]
    positive, negative = amount(wins, "pnl"), -amount(losses, "pnl")
    return {
        "trades": len(rows),
        "contracts": sum(r["quantity"] for r in rows),
        "events": len({r["event"] for r in rows}),
        "released_utc_days": len({day(r["cash_release_ts"]) for r in rows}),
        "net_profit": float(amount(rows, "pnl")),
        "entry_fees": float(amount(rows, "entry_fee")),
        "exit_fees": float(amount(rows, "exit_fee")),
        "wins": len(wins),
        "losses": len(losses),
        "flat": len(rows) - len(wins) - len(losses),
        "trade_win_fraction": len(wins) / len(rows) if rows else None,
        "positive_trade_profit": float(positive),
        "negative_trade_loss": float(negative),
        "mean_winning_trade_profit": float(positive / len(wins)) if wins else None,
        "mean_losing_trade_loss": float(negative / len(losses)) if losses else None,
        "gross_profit_to_gross_loss": float(positive / negative) if negative else None,
        "settlements": sum(r["terminal_kind"] == "settlement" for r in rows),
        "scheduled_sales": sum(r["terminal_kind"] == "sale" for r in rows),
    }


def trace_account(account):
    """Reconcile immutable ledger cash and locate the first 20% cost drawdown."""
    cash, peak = D(account["initial_cash"]), D(account["initial_cash"])
    pending, held, states = {}, {}, []
    first_kill = None
    for order in account["orders"]:
        identifier, status = order["trade_id"], order["status"]
        if status == "reserved":
            assert identifier not in pending and identifier not in held
            pending[identifier] = D(order["reserved_cash"])
            cash -= pending[identifier]
        elif status == "not_filled":
            cash += pending.pop(identifier, Decimal(0))
        elif status == "filled":
            cash += pending.pop(identifier) - D(order["entry_cost"])
            held[identifier] = D(order["entry_principal"])
        elif status == "closed":
            cash += D(order["exit_proceeds"])
            held.pop(identifier)
        else:
            raise ValueError("Unknown archived order status")
        reserved, principal = sum(pending.values(), Decimal(0)), sum(held.values(), Decimal(0))
        equity = cash + reserved + principal
        peak = max(peak, equity)
        drawdown = 1 - equity / peak
        state = {
            "timestamp": order["timestamp"],
            "trade_id": identifier,
            "operation": status,
            "cash": float(cash),
            "reserved": float(reserved),
            "principal": float(principal),
            "cost_equity": float(equity),
            "prior_peak": float(peak),
            "drawdown": float(drawdown),
            "pending_orders": len(pending),
            "held_positions": len(held),
        }
        states.append(state)
        if first_kill is None and drawdown >= Decimal("0.20"):
            first_kill = state
        assert cash >= 0
    assert cash == D(account["cash"]), "Independent ledger cash reconciliation failed"
    assert (first_kill is not None) == account["drawdown_killed"]
    if first_kill:
        assert first_kill["timestamp"] == account["drawdown_kill_ts"]
    return {
        "ledger_final_cash_matches_exactly": True,
        "first_twenty_percent_cost_drawdown": first_kill,
        "peak_cost_equity": float(peak),
        "maximum_reserved_cash": max((s["reserved"] for s in states), default=0),
        "maximum_held_principal": max((s["principal"] for s in states), default=0),
        "maximum_simultaneous_pending_orders": max((s["pending_orders"] for s in states), default=0),
        "maximum_simultaneous_positions": max((s["held_positions"] for s in states), default=0),
    }


def diagnose(row, card, source_id):
    account = row["account"]
    assert account["start_ts"] == datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    assert account["end_ts"] == datetime(2025, 9, 6, tzinfo=UTC).timestamp()
    accepted, closed, orders, daily = (
        account[k] for k in ("accepted_trades", "closed_trades", "orders", "daily")
    )
    assert all(t["decision_ts"] < datetime(2025, 9, 1, tzinfo=UTC).timestamp() for t in accepted)
    assert all(t["cash_release_ts"] < account["end_ts"] for t in closed)
    assert amount(closed, "pnl") == D(account["realized_pnl"])
    assert amount(accepted, "entry_fee") + amount(closed, "exit_fee") == D(account["total_fees"])
    if not account["unresolved_holdings"] and not account["pending_orders"]:
        assert D(account["cash"]) - D(account["initial_cash"]) == amount(closed, "pnl")
    by_day, by_month, by_city = defaultdict(list), defaultdict(list), {city: [] for city in CITIES}
    for trade in closed:
        release_day = day(trade["cash_release_ts"])
        by_day[release_day].append(trade)
        by_month[release_day[:7]].append(trade)
        by_city[trade["event"].split("-", 1)[0]].append(trade)
    months = sorted({d["date"][:7] for d in daily})
    daily_rows, previous_equity = [], D(account["initial_cash"])
    for d in daily:
        trades = by_day[d["date"]]
        assert amount(trades, "pnl") == D(d["daily_realized_pnl"])
        equity = D(d["cost_basis_equity"])
        daily_rows.append(
            {
                "date": d["date"],
                **describe_trades(trades),
                "cost_equity_change": float(equity - previous_equity),
                "cost_basis_equity": float(equity),
                "fees_charged_today": d["daily_fees"],
                "reserved_cash": d["reserved_cash"],
                "held_principal": d["open_principal"],
            }
        )
        previous_equity = equity
    weeks = [
        {
            "start": daily_rows[i]["date"],
            "end_inclusive": daily_rows[i + 6]["date"],
            "net_profit": float(amount(daily_rows[i : i + 7], "net_profit")),
            "cost_equity_change": float(amount(daily_rows[i : i + 7], "cost_equity_change")),
        }
        for i in range(len(daily_rows) - 6)
    ]
    ranked_days = sorted(daily_rows, key=lambda d: (-d["net_profit"], d["date"]))
    positive_days = [d for d in daily_rows if d["net_profit"] > 0]
    positive_day_total = amount(positive_days, "net_profit")
    reserved = [o for o in orders if o["status"] == "reserved"]
    kill = account["drawdown_kill_ts"]
    held_at_kill = [t for t in closed if kill is not None and t["entry_ts"] <= kill < t["cash_release_ts"]]
    after_kill = [t for t in closed if kill is not None and t["cash_release_ts"] > kill]
    assert len(held_at_kill) == len(after_kill), "Unexpected post-kill new position"
    settlement = [t for t in closed if t["terminal_kind"] == "settlement"]
    settled_qty = sum(t["quantity"] for t in settlement)
    successful_qty = sum(t["quantity"] for t in settlement if D(t["terminal_cash_per_contract"]) == 1)
    gross = amount(closed, "pnl") + amount(accepted, "entry_fee") + amount(closed, "exit_fee")
    summary = {
        "source_record_id": source_id,
        "policy": row["policy"],
        "scenario": row["scenario"],
        "risk_fraction": row["risk_fraction"],
        "parent_record_id": row["parent_record_id"],
        "parent_status_omissions": row["parent_status_omissions"],
        "training_statistics": card["training_statistics"],
        "selection_rejections": card.get("selection_rejections", []),
        "final_cash": account["cash"],
        "initial_cash": account["initial_cash"],
        "cash_profit": float(D(account["cash"]) - D(account["initial_cash"])),
        "total_fees": account["total_fees"],
        "gross_before_fees_profit": float(gross),
        "fees_as_fraction_of_gross_profit": float(D(account["total_fees"]) / gross) if gross > 0 else None,
        "fees_as_fraction_of_net_profit": float(D(account["total_fees"]) / amount(closed, "pnl"))
        if amount(closed, "pnl") > 0
        else None,
        "trade_summary": describe_trades(closed),
        "entry_price_quantity_weighted": float(
            sum((D(t["entry_price"]) * t["quantity"] for t in accepted), Decimal(0))
            / sum(t["quantity"] for t in accepted)
        )
        if accepted
        else None,
        "settled_contract_success_fraction": successful_qty / settled_qty if settled_qty else None,
        "settlement_cost_per_contract_including_fees": float(amount(settlement, "entry_cost") / settled_qty)
        if settled_qty
        else None,
        "monthly_contribution": [{"month": month, **describe_trades(by_month[month])} for month in months],
        "city_contribution": [{"series": city, **describe_trades(by_city[city])} for city in CITIES],
        "daily_contribution": daily_rows,
        "seven_day_windows": weeks,
        "worst_realized_day": min(daily_rows, key=lambda d: (d["net_profit"], d["date"])),
        "worst_cost_equity_day": min(daily_rows, key=lambda d: (d["cost_equity_change"], d["date"])),
        "worst_realized_week": min(weeks, key=lambda d: (d["net_profit"], d["start"])),
        "worst_cost_equity_week": min(weeks, key=lambda d: (d["cost_equity_change"], d["start"])),
        "top_positive_day_contribution": {
            str(n): {
                "net_profit": float(amount(ranked_days[:n], "net_profit")),
                "fraction_of_positive_day_total": float(
                    amount(ranked_days[:n], "net_profit") / positive_day_total
                )
                if positive_day_total
                else None,
                "fraction_of_final_net_profit": float(
                    amount(ranked_days[:n], "net_profit") / amount(closed, "pnl")
                )
                if amount(closed, "pnl") > 0
                else None,
            }
            for n in (1, 5, 10)
        },
        "max_cost_basis_drawdown": account["max_cost_basis_drawdown"],
        "max_zero_mark_drawdown": account["max_zero_mark_drawdown"],
        "drawdown_killed": account["drawdown_killed"],
        "drawdown_kill_timestamp": kill,
        "drawdown_kill_utc": iso(datetime.fromtimestamp(kill, UTC)) if kill is not None else None,
        "held_at_kill_later_releases": describe_trades(held_at_kill),
        "held_at_kill_later_release_rows": held_at_kill,
        "ledger_reconciliation": trace_account(account),
        "orders_reserved": len(reserved),
        "reserved_binding_limits": dict(Counter(o["binding_limit"] for o in reserved)),
        "reserved_quantity_distribution": dict(
            sorted(Counter(o["intended_quantity"] for o in reserved).items())
        ),
        "entry_quantity_distribution": dict(sorted(Counter(t["quantity"] for t in accepted).items())),
        "quantity_reduced_after_reservation": sum(t["quantity"] < t["intended_quantity"] for t in accepted),
        "unfilled_intended_contracts": sum(t["unfilled_quantity"] for t in accepted),
        "orders_at_arbitrary_100_contract_cap": sum(o["intended_quantity"] == 100 for o in reserved),
        "no_trade_reasons": account["no_trade_reasons"],
        "unresolved_holdings": len(account["unresolved_holdings"]),
        "pending_orders": len(account["pending_orders"]),
    }
    assert amount(summary["monthly_contribution"], "net_profit") == amount(closed, "pnl")
    assert amount(summary["city_contribution"], "net_profit") == amount(closed, "pnl")
    return summary


def main():
    archive = Archive()
    declaration = archive.db.execute("SELECT * FROM records WHERE id=?", (DECLARATION_ID,)).fetchone()
    if declaration is None or declaration["kind"] != "bankroll_diagnostic_protocol":
        raise ValueError("Missing retrospective declaration")
    plan = archive.json(declaration)
    assert plan["key"] == KEY
    report_body = Path("reports/E023_bankroll.json").read_bytes()
    assert hashlib.sha256(report_body).hexdigest() == plan["parent_report_sha256"]
    parent = json.loads(report_body)
    cards = {
        (r["policy"]["id"], r["scenario"], r["risk_fraction"]): r
        for r in parent["training_results"]
        if r["policy"]["id"] in plan["policies"]
    }
    summaries = []
    for source in plan["source_accounts"]:
        raw = archive.db.execute("SELECT * FROM records WHERE id=?", (source["id"],)).fetchone()
        assert raw["kind"] == "e023_training_account_gzip" and raw["key"] == source["key"]
        body = archive.body(raw)
        assert hashlib.sha256(body).hexdigest() == source["body_sha256"]
        row = json.loads(gzip.decompress(body))
        key = row["policy"]["id"], row["scenario"], row["risk_fraction"]
        summaries.append(diagnose(row, cards[key], source["id"]))
    result = {
        "declaration_record_id": DECLARATION_ID,
        "generated_at": iso(utcnow()),
        "source_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "accounts": summaries,
        "parent_bootstrap": parent["bootstrap"],
        "parent_selection": parent["selection"],
        "new_strategy_search": False,
        "new_2026_analysis": False,
        "promotion_eligible": False,
        "account_cash_and_grouping_checks": "All16 exact ledger cash, closed PnL, entry/exit fees, month/city/day sums and kill timestamps reconciled",
    }
    identifier = archive.append("bankroll_diagnostics", KEY, utcnow(), {}, canonical(result).encode())
    result["report_record_id"] = identifier
    Path("reports/bankroll_diagnostics.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report_record_id": identifier,
                "accounts": len(summaries),
                "checks": result["account_cash_and_grouping_checks"],
            }
        )
    )
    for row in summaries:
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "source_record_id",
                        "policy",
                        "scenario",
                        "risk_fraction",
                        "final_cash",
                        "total_fees",
                        "drawdown_killed",
                    )
                }
            )
        )
    archive.close()


if __name__ == "__main__":
    main()
