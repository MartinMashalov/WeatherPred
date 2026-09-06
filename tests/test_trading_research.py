from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.experiments.e013_autoresearch import load_inputs
from weatherpred.archive import Archive, canonical
from weatherpred.trading_research import conditional_trade, portfolio, select_trade

SCENARIO = {"entry_delay_hours": 1, "slippage": "0", "fee_multiplier": "1"}
MARKET = {"open_ts": 0, "close_ts": 20000, "settled_ts": 30000, "outcome": 1, "candle_source_record_id": 7}


def test_signal_uses_past_change_and_rejects_outcome_or_future_fields():
    signal = [{"ticker": "a", "bid": 0.38, "ask": 0.42, "past_bid": 0.28, "past_ask": 0.32}]
    policy = {"family": "momentum", "maximum_spread": 0.08, "threshold": 0.08}
    assert select_trade(signal, policy)["side"] == "yes"
    assert select_trade(signal, {**policy, "family": "reversal"})["side"] == "no"
    with pytest.raises(ValueError, match="information boundary"):
        select_trade([{**signal[0], "outcome": 1}], policy)
    with pytest.raises(ValueError, match="information boundary"):
        select_trade([{**signal[0], "future_bid": 0.99}], policy)


def test_delayed_buy_and_later_sale_have_two_independent_fees():
    selection = {"ticker": "a", "side": "yes", "signal_ask": 0.45}
    quotes = {3600: {"bid": 0.38, "ask": 0.40}, 7200: {"bid": 0.60, "ask": 0.62}}
    t = conditional_trade(selection, MARKET, quotes, 0, {"exit_hours": 1}, SCENARIO)
    assert t["status"] == "conditional_trade"
    assert t["entry_ts"] == 3600 and t["exit_ts"] == 7200
    # One contract costs40c+2c; sale returns60c-2c, hence16c profit.
    assert t["entry_cost"] == 0.42
    assert t["exit_proceeds"] == 0.58
    assert t["entry_fee"] == 0.02 and t["exit_fee"] == 0.02
    assert t["pnl"] == pytest.approx(0.16)
    quotes[3600]["ask"] = 0.46
    assert (
        conditional_trade(selection, MARKET, quotes, 0, {"exit_hours": 1}, SCENARIO)["status"]
        == "entry_limit_not_met"
    )


def test_no_side_complements_and_missing_exit_falls_back_to_losing_settlement():
    selection = {"ticker": "a", "side": "no", "signal_ask": 0.45}
    quotes = {3600: {"bid": 0.60, "ask": 0.62}, 7200: {"bid": 0.38, "ask": 0.40}}
    t = conditional_trade(selection, MARKET, quotes, 0, {"exit_hours": 1}, SCENARIO)
    assert t["entry_cost"] == 0.42 and t["exit_proceeds"] == 0.58
    del quotes[7200]
    loss = conditional_trade(selection, MARKET, quotes, 0, {"exit_hours": 1}, SCENARIO)
    assert loss["exit_kind"] == "settlement_fallback"
    assert loss["exit_ts"] == 30000 and loss["pnl"] == -0.42
    # An earlier or later candle is not an exact entry quote.
    assert (
        conditional_trade(selection, MARKET, {3599: quotes[3600]}, 0, {"exit_hours": 1}, SCENARIO)["status"]
        == "missing_entry_quote"
    )


def test_cash_remains_locked_and_future_settlement_does_not_enter_training():
    config = {
        "bankroll": 100,
        "max_event_fraction": 0.05,
        "max_cluster_fraction": 0.01,
        "max_total_fraction": 0.25,
    }
    trades = [
        {
            "event": str(i),
            "entry_ts": 3600,
            "exit_ts": 30000,
            "entry_cost": 0.42,
            "exit_proceeds": 1,
            "pnl": 0.58,
        }
        for i in range(3)
    ]
    before = portfolio(trades, config, 0, 7200)
    assert before["cash"] == pytest.approx(99.16)
    assert before["locked_cost"] == pytest.approx(0.84)
    assert before["pending_trades"] == 2 and before["risk_rejections"] == 1
    assert before["released_trades"] == [] and before["daily_realized_pnl"] == {}
    after = portfolio(trades, config, 0, 31000)
    assert after["cash"] == pytest.approx(101.16)
    assert after["locked_cost"] == pytest.approx(0)
    assert len(after["released_trades"]) == 2


def test_resumed_research_cannot_read_quotes_acquired_after_registration(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = Archive()
    try:
        before = datetime(2026, 9, 6, 10, tzinfo=UTC)
        frozen = datetime(2026, 9, 6, 11, tzinfo=UTC)
        after = datetime(2026, 9, 6, 12, tzinfo=UTC)
        original = {
            "markets": [
                {
                    "ticker": "a",
                    "event_ticker": "event",
                    "result": "yes",
                    "settlement_ts": "2025-01-02T12:00:00Z",
                }
            ]
        }
        source = a.append("market", "a", before, {}, canonical(original).encode())
        row = {"day": "2025-01-01", "ticker": "a", "event": "event", "outcome": 1, "source_record_id": source}
        body = canonical(row).encode() + b"\n"
        a.append("experiment_dataset", "E002_development_markets", before, {}, body)
        Path("reports").mkdir()
        Path("reports/E002_development_markets.jsonl").write_bytes(body)
        old = {
            "ticker": "a",
            "candlesticks": [
                {"end_period_ts": 3600, "yes_bid": {"close": "0.2000"}, "yes_ask": {"close": "0.2200"}}
            ],
        }
        a.append("e002_candles", "a", before, {}, canonical(old).encode())
        # A later response is deliberately unusable. Frozen reads must never access it.
        a.append("e002_candles", "a", after, {}, canonical({"ticker": "wrong"}).encode())
        rows, _, _, _ = load_inputs(
            a, {"development_start": "2025-01-01", "validation_end_exclusive": "2025-10-01"}, frozen
        )
        assert rows["a"]["quotes"][3600] == {"bid": 0.20, "ask": 0.22}
    finally:
        a.close()
