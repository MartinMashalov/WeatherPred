"""Independent diagnostic accounting retains fees and post-stop held losses."""

import pytest

from research.probes.bankroll_diagnostics import trace_account


def ledger_example():
    return {
        "initial_cash": 200,
        "cash": 157.4,
        "drawdown_killed": True,
        "drawdown_kill_ts": 5,
        "orders": [
            {"trade_id": "a", "timestamp": 1, "status": "reserved", "reserved_cash": 40},
            {"trade_id": "a", "timestamp": 2, "status": "filled", "entry_cost": 40, "entry_principal": 40},
            {"trade_id": "a", "timestamp": 3, "status": "closed", "exit_proceeds": 0.1},
            {"trade_id": "b", "timestamp": 4, "status": "reserved", "reserved_cash": 10.2},
            {"trade_id": "b", "timestamp": 5, "status": "filled", "entry_cost": 10.2, "entry_principal": 10},
            {"trade_id": "b", "timestamp": 6, "status": "closed", "exit_proceeds": 7.5},
        ],
    }


def test_fee_crosses_kill_threshold_before_later_held_loss():
    result = trace_account(ledger_example())
    kill = result["first_twenty_percent_cost_drawdown"]
    assert result["ledger_final_cash_matches_exactly"] is True
    assert kill["timestamp"] == 5
    assert kill["cost_equity"] == 159.9
    assert kill["principal"] == 10
    assert kill["held_positions"] == 1
    assert kill["drawdown"] == 0.2005


def test_one_cent_cash_discrepancy_fails_exact_reconciliation():
    account = ledger_example()
    account["cash"] = 157.41
    with pytest.raises(AssertionError, match="cash reconciliation"):
        trace_account(account)
