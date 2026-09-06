"""Causal accounting and capacity checks without market-data or policy searches."""

import math
from copy import deepcopy
from decimal import Decimal

import pytest

from weatherpred.bankroll_replay import aggregate_cash, replay
from weatherpred.fees import FeeAccumulator, FeeSchedule


def config(**changes):
    return {
        "initial_cash": "200",
        "risk_fraction": ".05",
        "max_event_fraction": ".5",
        "max_cluster_fraction": ".5",
        "max_total_fraction": ".5",
        "drawdown_kill_fraction": ".20",
        "entry_coefficient": ".07",
        "exit_coefficient": ".07",
        "mode": "verified",
        **changes,
    }


def provenance(when):
    return {"source_record_id": 1, "available_ts": when, "availability_verified": True}


def trade(identifier="a", decision=10, entry=20, **changes):
    return {
        "trade_id": identifier,
        "event": identifier,
        "cluster": "all_weather",
        "side": "yes",
        "decision_ts": decision,
        "entry_ts": entry,
        "limit_price": ".4",
        "entry_price": ".4",
        "available_quantity": 100,
        "signal_provenance": provenance(decision),
        "entry_price_provenance": provenance(entry),
        "exit_ts": None,
        "terminal_cash_per_contract": None,
        "outcome_available_ts": None,
        "terminal_kind": None,
        "terminal_provenance": None,
        **changes,
    }


def full_risk(**changes):
    return config(
        initial_cash="10",
        risk_fraction="1",
        max_event_fraction="1",
        max_cluster_fraction="1",
        max_total_fraction="1",
        drawdown_kill_fraction="1",
        entry_coefficient="0",
        exit_coefficient="0",
        **changes,
    )


def test_integer_quantity_aggregate_rounding_matches_existing_account_arithmetic():
    result = replay([trade()], config(), 0, 50)
    entry = result["accepted_trades"][0]
    assert entry["quantity"] == 23
    assert entry["entry_cost"] == 9.59
    assert result["cash"] == 190.41
    assert result["open_principal"] == 9.2
    assert result["cost_basis_equity"] == 199.61
    for price, quantity, sell in [(".4019", 3, False), (".4019", 3, True), (".4", 10, False)]:
        observed = aggregate_cash(price, quantity, ".07", sell=sell)
        expected = FeeAccumulator(FeeSchedule("quadratic", Decimal(1), Decimal(".01"))).fill(
            Decimal(price), quantity, sell=sell
        )
        assert observed["cash_change"] == expected["balance_change"]
        assert observed["net_fee"] == expected["net_fee"]
    assert aggregate_cash(".4019", 3, ".07")["cash_change"] == Decimal("-1.26")
    assert aggregate_cash(".4019", 3, ".07", sell=True)["cash_change"] == Decimal("1.15")
    assert aggregate_cash(".4", 10, ".07")["cash_change"] == Decimal("-4.17")


def test_future_outcomes_and_cheaper_entry_do_not_inflate_prior_intended_quantity():
    row = trade(
        limit_price=".5",
        entry_price=".2",
        exit_ts=100,
        terminal_cash_per_contract="1",
        terminal_kind="settlement",
        outcome_available_ts=100,
        terminal_provenance=provenance(100),
    )
    first = replay([row], full_risk(), 0, 50)
    altered = deepcopy(row)
    altered["terminal_cash_per_contract"] = "0"
    second = replay([altered], full_risk(), 0, 50)
    assert first == second
    assert first["accepted_trades"][0]["intended_quantity"] == 20
    assert first["accepted_trades"][0]["quantity"] == 20
    assert first["cash"] == 6 and first["open_principal"] == 4
    assert first["zero_mark_equity"] == 6
    assert first["closed_trades"] == []


def test_cash_waits_for_outcome_availability_and_release_precedes_same_time_decision():
    a = trade(
        "a",
        limit_price=".5",
        entry_price=".5",
        exit_ts=30,
        terminal_cash_per_contract="1",
        terminal_kind="settlement",
        outcome_available_ts=100,
        terminal_provenance=provenance(80),
    )
    b = trade("b", decision=40, entry=45, limit_price=".5", entry_price=".5")
    c = trade("c", decision=100, entry=100, limit_price=".5", entry_price=".5")
    result = replay([a, b, c], full_risk(), 0, 101)
    assert result["no_trade_reasons"] == {"cash_limit": 1}
    assert result["closed_trades"][0]["cash_release_ts"] == 100
    assert [p["quantity"] for p in result["accepted_trades"]] == [20, 40]
    assert result["cost_basis_equity"] == 20
    assert result["cash"] == 0


def test_failed_future_entry_still_reserves_cash_until_the_failure():
    a = trade("a", decision=1, entry=20, limit_price=".5", entry_price=None)
    b = trade("b", decision=2, entry=3, limit_price=".5", entry_price=".5")
    c = trade("c", decision=21, entry=21, limit_price=".5", entry_price=".5")
    result = replay([a, b, c], full_risk(), 0, 30)
    assert result["no_trade_reasons"] == {"cash_limit": 1, "missing_entry_quote": 1}
    assert [p["trade_id"] for p in result["accepted_trades"]] == ["c"]
    assert result["orders"][0]["status"] == "reserved"
    assert result["pending_orders"] == []
    over_limit = trade("d", decision=1, entry=2, limit_price=".4", entry_price=".41")
    rejected = replay([over_limit], config(), 0, 10)
    assert rejected["no_trade_reasons"] == {"entry_limit_not_met": 1}
    assert rejected["cash"] == 200


def test_verified_unknown_depth_refusal_and_explicit_hypothetical_integer_cap():
    unknown = trade(available_quantity=None)
    verified = replay([unknown], config(), 0, 50)
    assert verified["no_trade_reasons"] == {"unknown_entry_depth": 1}
    assert verified["cash"] == 200
    hypothetical = deepcopy(unknown)
    for key, when in [("signal_provenance", 10), ("entry_price_provenance", 20)]:
        hypothetical[key] = {
            "source_record_id": 1,
            "available_ts": 99999,
            "availability_verified": False,
            "assumed_available_ts": when,
        }
    assumed = replay([hypothetical], config(mode="hypothetical", conditional_depth_cap=3), 0, 50)
    assert assumed["accepted_trades"][0]["quantity"] == 3
    assert assumed["accepted_trades"][0]["intended_quantity"] == 3
    assert assumed["orders"][0]["reserved_cash"] == 1.26
    refused = replay([hypothetical], config(), 0, 50)
    assert refused["no_trade_reasons"] == {"signal_unverified_availability": 1}
    fraction = replay([trade(available_quantity="2.9")], config(), 0, 50)
    assert fraction["accepted_trades"][0]["quantity"] == 2


def test_event_cluster_total_caps_and_zero_mark_do_not_trigger_cost_mark_kill():
    cfg = config(
        initial_cash="100",
        risk_fraction=".2",
        max_event_fraction=".2",
        max_cluster_fraction=".3",
        max_total_fraction=".5",
        entry_coefficient="0",
        exit_coefficient="0",
    )
    rows = [
        trade("a", 1, 1, event="same", cluster="coast", limit_price=".5", entry_price=".5"),
        trade("b", 2, 2, event="same", cluster="coast", limit_price=".5", entry_price=".5"),
        trade("c", 3, 3, cluster="coast", limit_price=".5", entry_price=".5"),
        trade("d", 4, 4, cluster="other", limit_price=".5", entry_price=".5"),
        trade("e", 5, 5, cluster="third", limit_price=".5", entry_price=".5"),
    ]
    result = replay(rows, cfg, 0, 10)
    assert result["no_trade_reasons"] == {"event_cap": 1, "total_cap": 1}
    assert [p["quantity"] for p in result["accepted_trades"]] == [40, 20, 40]
    assert result["cash"] == 50 and result["open_principal"] == 50
    assert result["max_zero_mark_drawdown"] == 0.5
    assert result["max_cost_basis_drawdown"] == 0 and not result["drawdown_killed"]


def test_drawdown_kill_cancels_pending_entries_but_releases_existing_holdings():
    cfg = config(
        initial_cash="100",
        risk_fraction=".2",
        max_event_fraction="1",
        max_cluster_fraction="1",
        max_total_fraction="1",
        entry_coefficient="0",
        exit_coefficient="0",
    )
    a = trade(
        "a",
        1,
        1,
        limit_price=".5",
        entry_price=".5",
        exit_ts=30,
        terminal_cash_per_contract="0",
        terminal_kind="settlement",
        outcome_available_ts=30,
        terminal_provenance=provenance(30),
    )
    b = trade("b", 5, 40, limit_price=".5", entry_price=".5")
    c = trade(
        "c",
        6,
        6,
        limit_price=".5",
        entry_price=".5",
        exit_ts=70,
        terminal_cash_per_contract="1",
        terminal_kind="settlement",
        outcome_available_ts=70,
        terminal_provenance=provenance(70),
    )
    d = trade("d", 35, 36)
    result = replay([a, b, c, d], cfg, 0, 80)
    assert result["drawdown_killed"] and result["drawdown_kill_ts"] == 30
    assert result["no_trade_reasons"] == {"drawdown_cancelled_pending_order": 1, "drawdown_kill_active": 1}
    assert [p["trade_id"] for p in result["accepted_trades"]] == ["a", "c"]
    assert len(result["closed_trades"]) == 2 and result["cash"] == 100
    assert result["pending_orders"] == [] and result["max_cost_basis_drawdown"] == 0.2


def test_verified_sale_requires_terminal_depth_and_two_aggregate_fees():
    row = trade(
        exit_ts=40,
        terminal_cash_per_contract=".6",
        terminal_kind="sale",
        outcome_available_ts=40,
        terminal_provenance=provenance(40),
    )
    missing = replay([row], config(), 0, 50)
    assert missing["closed_trades"] == [] and len(missing["unresolved_holdings"]) == 1
    assert missing["terminal_notes"] == [{"trade_id": "a", "reason": "unknown_terminal_depth"}]
    insufficient = replay([{**row, "terminal_available_quantity": 22}], config(), 0, 50)
    assert insufficient["closed_trades"] == []
    complete = replay([{**row, "terminal_available_quantity": 23}], config(), 0, 50)
    closed = complete["closed_trades"][0]
    assert closed["quantity"] == 23 and closed["entry_cost"] == 9.59
    assert closed["exit_proceeds"] == 13.41 and closed["pnl"] == 3.82
    assert closed["entry_fee"] == 0.39 and closed["exit_fee"] == 0.39
    assert complete["cash"] == 203.82


def test_daily_cost_log_returns_terminal_unresolved_and_flat_start():
    row = trade(
        "a",
        decision=10,
        entry=20,
        limit_price=".5",
        entry_price=".5",
        exit_ts=90000,
        terminal_cash_per_contract="1",
        terminal_kind="settlement",
        outcome_available_ts=90000,
        terminal_provenance=provenance(90000),
    )
    result = replay([row], full_risk(), 0, 2 * 86400)
    assert len(result["daily"]) == 2
    assert result["daily"][0]["cost_basis_log_return"] == 0
    assert result["daily"][1]["cost_basis_log_return"] == pytest.approx(math.log(2))
    assert result["daily"][0]["zero_mark_log_return"] is None
    assert result["daily"][1]["released_trades"] == 1
    before = replay([row], full_risk(), 0, 90000)
    assert before["closed_trades"] == []
    assert before["open_principal"] == 10 and before["zero_mark_equity"] == 0
    no_inheritance = replay([row], full_risk(), 15, 100)
    assert no_inheritance["no_trade_reasons"] == {"decision_outside_window": 1}
    assert no_inheritance["cash"] == 10 and no_inheritance["open_principal"] == 0
