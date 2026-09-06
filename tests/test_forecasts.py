import numpy as np
import pytest
from scipy.integrate import quad
from scipy.stats import norm

from weatherpred.forecasts import IndexSeries, ResidualDistribution, candle_quote, fit_residuals


def test_forecasts_ignore_future_pending_and_historical_backfill():
    points = [{"t": i * 60_000, "v": 70 + i, "status": "normal"} for i in range(40)]
    points += [
        {"t": 39 * 60_000, "v": 999, "status": "incomplete"},
        {"t": 30 * 60_000, "v": 999, "status": "normal", "receipt_basis": "backfill"},
    ]
    args = {
        "decision_ms": 35 * 60_000,
        "settlement_ms": 60 * 60_000,
        "lag_ms": 10 * 60_000,
        "max_age_ms": 15 * 60_000,
        "lookback_ms": 30 * 60_000,
    }
    features = IndexSeries(points).features(**args)
    assert features["last_point_ms"] == 25 * 60_000
    assert features["persistence"] == 95
    assert features["trend"] == pytest.approx(130)
    assert features == IndexSeries(points[:26]).features(**args)
    assert IndexSeries(points[:10]).features(**args) is None


def test_index_conflicting_revisions_fail_closed():
    with pytest.raises(ValueError, match="revision audit"):
        IndexSeries([{"t": 1, "v": 1, "status": "normal"}, {"t": 1, "v": 2, "status": "normal"}])


def test_training_labels_cannot_cross_fit_cutoff():
    rows = [{"horizon_minutes": 5, "settlement_ms": 10, "observed": 75, "features": {"persistence": 74}}]
    with pytest.raises(ValueError, match="unavailable"):
        fit_residuals(rows, "persistence", 5, 300010)
    assert fit_residuals(rows, "persistence", 5, 300011) == (1,)


@pytest.mark.parametrize("kind", ["empirical", "gaussian"])
def test_distribution_probabilities_are_monotone_and_intervals_nested(kind):
    d = ResidualDistribution((-2, -1, 0, 1, 2), kind)
    probabilities = [
        d.probability(80, {"strike_type": "greater", "floor_strike": t})
        for t in [75.99, 76.99, 79.99, 80.99, 82.99]
    ]
    assert all(0 <= p <= 1 for p in probabilities)
    assert probabilities == sorted(probabilities, reverse=True)
    lo80, hi80 = d.interval(80, 0.8)
    lo95, hi95 = d.interval(80, 0.95)
    assert lo95 < lo80 < hi80 < hi95


def test_rounded_strict_threshold_and_empirical_crps_reference():
    d = ResidualDistribution((-1, 0, 1), "empirical")
    assert d.probability(80, {"strike_type": "greater", "floor_strike": 79.99}) == 2.5 / 4
    assert d.probability(80, {"strike_type": "greater", "floor_strike": 80}) == 1.5 / 4
    x = np.asarray([79, 80, 81])
    expected = np.mean(np.abs(x - 80.3)) - np.mean(np.abs(x[:, None] - x[None, :])) / 2
    assert d.crps(80, 80.3) == pytest.approx(expected)


def test_gaussian_crps_matches_integrated_cdf_error():
    d = ResidualDistribution((-2, 0, 2), "gaussian")
    cdf = lambda x: norm.cdf(x, loc=80, scale=2)
    reference = quad(lambda x: cdf(x) ** 2, -np.inf, 81)[0]
    reference += quad(lambda x: (1 - cdf(x)) ** 2, 81, np.inf)[0]
    assert d.crps(80, 81) == pytest.approx(reference)


def test_quote_benchmark_never_uses_future_close_or_last_trade():
    def candle(t, bid, ask):
        return {
            "end_period_ts": t,
            "yes_bid": {"close_dollars": bid},
            "yes_ask": {"close_dollars": ask},
            "price": {"close_dollars": "0.9999"},
        }

    candles = [candle(120, ".3", ".5"), candle(180, ".9", ".95")]
    assert candle_quote(candles, 150)["midpoint"] == 0.4
    assert candle_quote(candles, 119) is None
    assert candle_quote(candles, 250) is None
    assert candle_quote([candle(120, "0", ".5")], 120) is None
    assert candle_quote([candle(120, ".5", ".5")], 120) is None
    assert candle_quote([candle(120, ".6", ".5")], 120) is None
