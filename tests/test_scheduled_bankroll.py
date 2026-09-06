"""Continuous-account schedule behavior on synthetic orders only."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest

from weatherpred.bankroll_replay import replay
from weatherpred.scheduled_bankroll import PARENT_KERNEL_SHA256, replay_scheduled, validate_schedule


def settings(**changes):
    return {
        "initial_cash": "200",
        "risk_fraction": ".05",  # Used by the frozen reference, ignored by the scheduled kernel.
        "max_event_fraction": ".05",
        "max_cluster_fraction": ".10",
        "max_total_fraction": ".25",
        "drawdown_kill_fraction": ".20",
        "entry_coefficient": ".07",
        "exit_coefficient": ".07",
        "mode": "verified",
        "allowed_policy_ids": ["A", "B"],
        "allowed_risk_fractions": [".005", ".01", ".025", ".05"],
        **changes,
    }


def provenance(when):
    return {"source_record_id": 1, "available_ts": when, "availability_verified": True}


def order(identifier, decision, entry, policy="A", **changes):
    return {
        "trade_id": identifier,
        "policy_id": policy,
        "event": identifier,
        "cluster": "all_weather",
        "side": "yes",
        "decision_ts": decision,
        "entry_ts": entry,
        "limit_price": ".5",
        "entry_price": ".5",
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


def settled(identifier, decision, entry, exit_at, payout="1", policy="A"):
    return order(
        identifier,
        decision,
        entry,
        policy,
        exit_ts=exit_at,
        terminal_cash_per_contract=payout,
        outcome_available_ts=exit_at,
        terminal_kind="settlement",
        terminal_provenance=provenance(exit_at),
    )


def regime(when, policy="A", fraction=".05"):
    return {"effective_ts": when, "policy_id": policy, "risk_fraction": fraction}


def reference_fields(result):
    return {key: value for key, value in result.items() if key != "schedule_metadata"}


@pytest.mark.parametrize("mode", ["verified", "hypothetical"])
def test_constant_schedule_is_exactly_the_frozen_parent_account(mode):
    parent = Path(__file__).resolve().parents[1] / "weatherpred/bankroll_replay.py"
    assert hashlib.sha256(parent.read_bytes()).hexdigest() == PARENT_KERNEL_SHA256
    config = settings(mode=mode, conditional_depth_cap=100)
    rows = [
        order("outside", -10, 10),
        order(
            "sale",
            10,
            20,
            limit_price=".4",
            entry_price=".4",
            exit_ts=90000,
            terminal_cash_per_contract=".6",
            terminal_kind="sale",
            outcome_available_ts=90000,
            terminal_provenance=provenance(90000),
            terminal_available_quantity=100,
        ),
        order("missing", 30, 40, entry_price=None),
        order("partial-unresolved", 50, 60, available_quantity=2),
    ]
    schedule = [regime(0)]
    original = deepcopy((rows, config, schedule))
    expected = replay(rows, config, 0, 2 * 86400)
    actual = replay_scheduled(rows, config, schedule, 0, 2 * 86400)
    assert reference_fields(actual) == expected
    assert (rows, config, schedule) == original
    assert actual["cash"] > 200
    assert actual["closed_trades"][0]["quantity"] == 23
    assert actual["accepted_trades"][1]["quantity"] == 2
    assert actual["no_trade_reasons"] == {"decision_outside_window": 1, "missing_entry_quote": 1}
    assert actual["schedule_metadata"]["cash_initializations"] == 1


def test_cash_switch_preserves_pending_fills_held_releases_and_shared_exposure():
    config = settings(entry_coefficient="0", exit_coefficient="0")
    schedule = [regime(0), regime(15, None, None), regime(25, "B", ".01")]
    rows = [
        settled("old-held", 1, 2, 30),
        settled("old-pending", 10, 20, 40),
        order("cash-intent", 16, 17),
        order("new-but-cap-full", 26, 27, "B"),
        order("new-after-release", 31, 32, "B"),
    ]
    result = replay_scheduled(rows, config, schedule, 0, 50)
    assert result["no_trade_reasons"] == {"cluster_cap": 1, "scheduled_cash": 1}
    assert [row["trade_id"] for row in result["accepted_trades"]] == [
        "old-held",
        "old-pending",
        "new-after-release",
    ]
    assert [row["quantity"] for row in result["accepted_trades"]] == [20, 20, 4]
    assert result["accepted_trades"][1]["entry_ts"] == 20  # Fills during the cash regime.
    assert [row["cash_release_ts"] for row in result["closed_trades"]] == [30, 40]
    assert result["cash"] == 218 and result["open_principal"] == 2
    assert result["cost_basis_equity"] == 220
    assert result["pending_orders"] == []
    assert result["schedule_metadata"]["trade_policy_ids"]["old-pending"] == "A"


def test_lower_fraction_new_policy_and_cash_apply_at_the_exact_decision_boundary():
    config = settings(entry_coefficient="0", exit_coefficient="0", risk_fraction=".005")
    schedule = [regime(0), regime(20, "B", ".005"), regime(40, None, None)]
    rows = [
        order("before", 19, 21),
        order("after", 20, 22, "B"),
        order("wrong-policy", 23, 24),
        order("cash", 40, 41, "B"),
    ]
    result = replay_scheduled(rows, config, schedule, 0, 50)
    assert [row["quantity"] for row in result["accepted_trades"]] == [20, 2]
    assert result["no_trade_reasons"] == {"policy_not_selected_at_decision": 1, "scheduled_cash": 1}
    decisions = {row["trade_id"]: row for row in result["orders"] if row["status"] == "reserved"}
    assert decisions["before"]["reserved_cash"] == 10
    assert decisions["after"]["reserved_cash"] == 1
    assert result["cash"] == 189
    assert result["schedule_metadata"]["decision_regimes"][1]["effective_ts"] == 20


def test_future_schedule_changes_cannot_alter_earlier_sizing_or_account_path():
    config = settings(entry_coefficient="0", exit_coefficient="0")
    rows = [settled("first", 10, 20, 80)]
    first = replay_scheduled(rows, config, [regime(0), regime(40, "B", ".005")], 0, 100)
    second = replay_scheduled(rows, config, [regime(0), regime(40, None, None)], 0, 100)
    assert reference_fields(first) == reference_fields(second)
    assert first["accepted_trades"][0]["quantity"] == 20
    assert first["closed_trades"][0]["cash_release_ts"] == 80
    assert first["cash"] == 210


def test_drawdown_halt_persists_through_cash_new_policy_and_later_recovery():
    config = settings(
        entry_coefficient="0", exit_coefficient="0", drawdown_kill_fraction=".05", max_cluster_fraction=".25"
    )
    schedule = [regime(0), regime(35, None, None), regime(75, "B", ".01")]
    rows = [
        settled("loss", 1, 1, 30, "0"),
        order("cancelled-pending", 5, 40),
        settled("later-recovery", 6, 6, 70),
        order("after-switch", 80, 81, "B"),
    ]
    result = replay_scheduled(rows, config, schedule, 0, 90)
    assert result["drawdown_killed"] is True
    assert result["drawdown_kill_ts"] == 30
    assert result["max_cost_basis_drawdown"] == 0.05
    assert result["no_trade_reasons"] == {"drawdown_cancelled_pending_order": 1, "drawdown_kill_active": 1}
    assert [row["trade_id"] for row in result["accepted_trades"]] == ["loss", "later-recovery"]
    assert result["cash"] == 200  # Recovery does not reset the halt or create a fresh account.
    assert len(result["closed_trades"]) == 2
    assert result["pending_orders"] == result["unresolved_holdings"] == []


def test_schedule_rejects_ambiguous_times_unknown_policies_and_unregistered_risk():
    config = settings()
    for schedule in (
        [],
        [regime(1)],
        [regime(0), regime(0, "B")],
        [regime(0), regime(20), regime(10)],
        [regime(0), regime(float("nan"))],
        [regime(0), regime(True)],
        [regime(0), regime(100)],
        [regime(0, "unknown")],
        [regime(0, "A", None)],
        [regime(0, "A", "NaN")],
        [regime(0, "A", "Infinity")],
        [regime(0, "A", True)],
        [regime(0, "A", "0")],
        [regime(0, "A", ".1")],
        [regime(0, None, ".05")],
    ):
        with pytest.raises((ValueError, TypeError)):
            validate_schedule(schedule, config, 0, 100)
    for update in (
        {"allowed_policy_ids": ["A", "A"]},
        {"allowed_policy_ids": [""]},
        {"allowed_risk_fractions": []},
        {"allowed_risk_fractions": [".05", ".050"]},
        {"allowed_risk_fractions": [".1"]},
        {"allowed_risk_fractions": [float("nan")]},
    ):
        with pytest.raises((ValueError, TypeError)):
            validate_schedule([regime(0)], settings(**update), 0, 100)
    with pytest.raises(ValueError, match="allowed policy_id"):
        replay_scheduled([order("bad", 1, 2, "unknown")], config, [regime(0)], 0, 100)
