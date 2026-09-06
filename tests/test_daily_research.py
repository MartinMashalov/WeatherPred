import json
from pathlib import Path

import pytest

from research.experiments.e002_acquire import development_market
from weatherpred.contracts import historical_predicate, yes_at


@pytest.mark.parametrize(
    "clause,inside,outside",
    [
        ("between 70-71°", 70, 72),
        ("between -5--4°", -4, -6),
        ("greater than -1°", 0, -1),
        ("less than 80°", 79, 80),
    ],
)
def test_missing_historical_winner_strikes_recovered_without_using_outcome(clause, inside, outside):
    market = historical_predicate({"rules_primary": "If temperature is " + clause + ", then Yes."})
    assert yes_at(market, inside)
    assert not yes_at(market, outside)
    assert market["predicate_provenance"] == "explicit_numeric_primary_rule"


def test_unresolved_historical_predicate_is_not_guessed():
    with pytest.raises(ValueError, match="unambiguous"):
        historical_predicate({"ticker": "ANY-B80.5", "result": "yes", "expiration_value": "80"})


def test_sealed_holdout_is_rejected_before_any_label_access():
    protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
    # These intentionally contain NO label fields. Reading one would fail.
    for day in ("25OCT01", "25NOV01", "25DEC31", "26JAN01"):
        assert development_market({"event_ticker": "KXHIGHNY-" + day}, protocol, {}) is None


def test_source_window_and_exchange_close_are_independent_gates():
    protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
    windows = json.loads(Path("config/e002_source_windows.json").read_text())
    m = {
        "event_ticker": "KXHIGHNY-25JUL15",
        "ticker": "KXHIGHNY-25JUL15-T90",
        "result": "yes",
        "rules_primary": "NWS's Daily Climate Report",
        "rules_secondary": "",
        "expiration_value": "92",
        "floor_strike": 90,
        "strike_type": "greater",
        "open_time": "2025-07-14T14:00:00Z",
        "close_time": "2025-07-16T03:59:00Z",
    }
    row = development_market(m, protocol, windows)
    assert row["source_period_end"] == "2025-07-16T05:00:00.000000+00:00"
    assert row["decision_ts"]["3"] < row["close_ts"]
    assert row["decision_ts"]["3"] + 3 * 3600 - row["close_ts"] == 3660
    with pytest.raises(ValueError, match="Unverified daily source"):
        development_market(dict(m, rules_primary="The Weather Company"), protocol, windows)
