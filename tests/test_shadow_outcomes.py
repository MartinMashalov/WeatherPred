import pytest

from weatherpred.shadow_outcomes import finalized_labels


def test_outcome_scorer_never_treats_closed_but_unfinalized_result_as_final():
    forecast = {"markets": [{"ticker": "x", "floor_strike": 79.99}]}
    market = {
        "ticker": "x",
        "floor_strike": 79.99,
        "strike_type": "greater",
        "result": "yes",
        "status": "closed",
    }
    assert finalized_labels(forecast, {"markets": [market]}) is None
    assert finalized_labels(forecast, {"markets": [dict(market, status="finalized")]}) == {"x": 1}
    with pytest.raises(ValueError, match="predicate changed"):
        finalized_labels(forecast, {"markets": [dict(market, status="finalized", floor_strike=80.99)]})
    with pytest.raises(ValueError, match="Nonbinary"):
        finalized_labels(forecast, {"markets": [dict(market, status="finalized", result="scalar")]})
