"""Independent audit must reject plausible but incorrect account histories."""

from copy import deepcopy

import pytest

from research.experiments.e024_account_audit import audit_account
from weatherpred.bankroll_replay import replay
from weatherpred.scheduled_bankroll import replay_scheduled


def settings(**changes):
    return {
        "initial_cash": "200",
        "risk_fraction": ".05",
        "max_event_fraction": ".05",
        "max_cluster_fraction": ".10",
        "max_total_fraction": ".25",
        "drawdown_kill_fraction": ".20",
        "entry_coefficient": ".07",
        "exit_coefficient": ".07",
        "mode": "verified",
        "conditional_depth_cap": 100,
        "allowed_policy_ids": ["A", "B"],
        "allowed_risk_fractions": [".005", ".01", ".025", ".05"],
        **changes,
    }


def source(when):
    return {"available_ts": when, "availability_verified": True, "source_record_id": 1}


def intent(identifier, at, **changes):
    return {
        "trade_id": identifier,
        "event": identifier,
        "cluster": "weather",
        "policy_id": "A",
        "side": "yes",
        "decision_ts": at,
        "entry_ts": at + 1,
        "limit_price": ".4019",
        "entry_price": ".4019",
        "available_quantity": 100,
        "signal_provenance": source(at),
        "entry_price_provenance": source(at + 1),
        "exit_ts": 50,
        "terminal_cash_per_contract": "1",
        "terminal_kind": "settlement",
        "outcome_available_ts": 50,
        "terminal_provenance": source(50),
        **changes,
    }


def regime(at, policy="A", risk=".05"):
    return {"effective_ts": at, "policy_id": policy, "risk_fraction": risk}


@pytest.mark.parametrize("mode", ["verified", "hypothetical"])
def test_independent_reconstruction_handles_fees_partial_entries_and_unresolved_sales(mode):
    config = settings(mode=mode)
    trades = [
        intent("settles", 2, available_quantity=3),
        intent("missing", 5, entry_price=None),
        intent(
            "sale", 8, terminal_kind="sale", terminal_cash_per_contract=".7", terminal_available_quantity=100
        ),
        intent("late", 80, exit_ts=120, outcome_available_ts=120, terminal_provenance=source(120)),
    ]
    account = replay(trades, config, 0, 100)
    result = audit_account(account, trades, config, start_ts=0, end_ts=100)
    assert result["entries"] == 3 and result["releases"] == 2
    assert result["accounting_reproduced"] is True
    assert account["accepted_trades"][0]["entry_cost"] == 1.26
    assert account["accepted_trades"][0]["entry_fee"] == pytest.approx(0.0543)
    assert account["unresolved_holdings"][0]["trade_id"] == "late"


def test_rejects_consistently_omitted_release_even_when_cash_and_daily_rows_agree():
    config = settings()
    truth = [intent("winner", 2)]
    omitted = [intent("winner", 2, terminal_cash_per_contract=None)]
    internally_consistent = replay(omitted, config, 0, 100)
    assert internally_consistent["closed_trades"] == []
    with pytest.raises(ValueError, match="Missing expected release"):
        audit_account(internally_consistent, truth, config, start_ts=0, end_ts=100)


def test_rejects_early_release_and_wrong_quantity_without_tolerance_weakening():
    config = settings()
    trades = [intent("later", 2, outcome_available_ts=60, terminal_provenance=source(70))]
    account = replay(trades, config, 0, 100)
    assert account["closed_trades"][0]["cash_release_ts"] == 70
    assert audit_account(account, trades, config, start_ts=0, end_ts=100)["releases"] == 1
    altered = deepcopy(account)
    altered["orders"][-1]["timestamp"] = 50
    with pytest.raises(ValueError, match="Incorrect release timestamp"):
        audit_account(altered, trades, config, start_ts=0, end_ts=100)
    altered = deepcopy(account)
    altered["orders"][0]["intended_quantity"] += 1
    with pytest.raises(ValueError, match="Incorrect decision intended_quantity"):
        audit_account(altered, trades, config, start_ts=0, end_ts=100)


def test_schedule_switches_and_cash_keep_pending_orders_and_earlier_loss_state():
    config = settings(
        entry_coefficient="0", exit_coefficient="0", drawdown_kill_fraction=".05", max_cluster_fraction=".25"
    )
    schedule = [regime(0), regime(20, None, None), regime(70, "B", ".005")]
    trades = [
        intent("loser", 2, limit_price=".5", entry_price=".5", terminal_cash_per_contract="0"),
        intent("pending", 5, entry_ts=60, entry_price_provenance=source(60)),
        intent("cash", 25),
        intent("halted", 80, policy_id="B"),
    ]
    account = replay_scheduled(trades, config, schedule, 0, 100)
    result = audit_account(account, trades, config, schedule, start_ts=0, end_ts=100)
    assert result["ending_cash"] == 190
    assert account["drawdown_killed"] is True
    assert account["no_trade_reasons"] == {
        "drawdown_cancelled_pending_order": 1,
        "drawdown_kill_active": 1,
        "scheduled_cash": 1,
    }
    with pytest.raises(ValueError, match="Incorrect decision"):
        audit_account(
            account, trades, config, [regime(0, "A", ".005"), *schedule[1:]], start_ts=0, end_ts=100
        )


def test_unverified_source_refuses_verified_execution_and_is_explicitly_hypothetical():
    trade = intent("retrospective", 2)
    for key, at in (("signal_provenance", 2), ("entry_price_provenance", 3), ("terminal_provenance", 50)):
        trade[key] = {"available_ts": 1000, "availability_verified": False, "assumed_available_ts": at}
    verified = settings()
    account = replay([trade], verified, 0, 100)
    assert audit_account(account, [trade], verified, start_ts=0, end_ts=100)["entries"] == 0
    assert account["no_trade_reasons"] == {"signal_unverified_availability": 1}
    assumed = settings(mode="hypothetical")
    result = audit_account(replay([trade], assumed, 0, 100), [trade], assumed, start_ts=0, end_ts=100)
    assert result["entries"] == 1 and result["historical_fills_verified"] is False


def test_daily_boundaries_and_missing_snapshot_are_not_silently_collapsed():
    config = settings()
    trades = [
        intent(
            "boundary", 86400, exit_ts=172800, outcome_available_ts=172800, terminal_provenance=source(172800)
        )
    ]
    account = replay(trades, config, 0, 3 * 86400)
    assert audit_account(account, trades, config, start_ts=0, end_ts=3 * 86400)["daily_rows"] == 3
    assert account["daily"][0]["cash"] == 200
    assert account["daily"][1]["released_trades"] == 0
    assert account["daily"][2]["released_trades"] == 1
    altered = deepcopy(account)
    del altered["daily"][1]
    with pytest.raises(ValueError, match="Missing or shifted daily"):
        audit_account(altered, trades, config, start_ts=0, end_ts=3 * 86400)


def test_consistent_shortened_calendar_cannot_replace_the_registered_period():
    config = settings()
    short_account = replay([], config, 0, 86400)
    with pytest.raises(ValueError, match="calendar differs"):
        audit_account(short_account, [], config, start_ts=0, end_ts=365 * 86400)
