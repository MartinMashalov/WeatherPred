import json
from pathlib import Path

import numpy as np
import pytest
from scipy.optimize import check_grad

from weatherpred.daily_forecasts import DailyDistribution, daily_features, fit_daily_models, spread_objective


def test_daily_integer_probabilities_are_coherent_including_both_tails():
    markets = [{"strike_type": "less", "cap_strike": 78}]
    markets += [
        {"strike_type": "between", "floor_strike": lo, "cap_strike": lo + 1} for lo in (78, 80, 82, 84)
    ]
    markets.append({"strike_type": "greater", "floor_strike": 85})
    for dist in (DailyDistribution(81, 2), DailyDistribution(81, 2, (77, 78, 80, 86), (1, 2, 1, 1))):
        probabilities = [dist.probability(m) for m in markets]
        assert sum(probabilities) == pytest.approx(1, abs=1e-12)
        assert all(0 < p < 1 for p in probabilities)
        assert dist.probability({"strike_type": "between", "floor_strike": 80.1, "cap_strike": 80.9}) == 0
        lo, hi = dist.interval(0.8)
        assert dist.cdf(lo - 1e-8) <= 0.1 + 1e-8
        assert dist.cdf(hi) >= 0.9 - 1e-8


def test_nbm_daily_grid_uses_standard_time_and_rejects_unavailable_object():
    from datetime import UTC, datetime, timedelta

    start = datetime(2025, 7, 1, 6, tzinfo=UTC)
    rows = [
        {"valid_at": (start + timedelta(hours=i * 3)).isoformat(), "tmp": 70 + i, "txn": 88, "xnd": 3}
        for i in range(24)
    ]
    card = {"runtime": "2025-07-01T01:00:00Z", "version": "4.2", "rows": rows}
    features = daily_features("2025-07-01", -5, card, "2025-07-01T02:10:17Z")
    assert features["source_period_start"] == "2025-07-01T00:00:00-05:00"
    assert features["grid_max"] == 77
    assert len(features["grid_valid_times"]) == 8
    with pytest.raises(ValueError, match="not stored"):
        daily_features("2025-07-01", -5, card, "2025-07-01T05:00:01Z")
    with pytest.raises(ValueError, match="Incomplete"):
        daily_features("2025-07-01", -5, {**card, "rows": rows[1:]}, "2025-07-01T02:10:17Z")


def test_spread_regression_gradient_matches_numerical_reference():
    rng = np.random.default_rng(17)
    design = rng.normal(size=(40, 3))
    base = rng.normal(70, 4, size=40)
    spread = rng.uniform(1, 4, size=40)
    observed = base + design @ np.array([1.0, -0.3, 0.2]) + rng.normal(size=40)
    weights = rng.uniform(0.2, 1, size=40)
    theta = np.array([0.2, -0.1, 0.3, -0.4, 0.1])
    f = lambda t: spread_objective(t, design, base, spread, observed, weights)[0]
    g = lambda t: spread_objective(t, design, base, spread, observed, weights)[1]
    assert check_grad(f, g, theta) < 1e-5


def test_daily_fit_rejects_validation_or_late_source_before_fitting():
    protocol = json.loads(Path("config/e003_daily_models.json").read_text())
    row = {"day": "2025-07-01", "split": "validation", "settled_ts": 0}
    with pytest.raises(ValueError, match="Nontraining"):
        fit_daily_models([row], protocol)
    row = {"day": "2025-06-30", "split": "train", "settled_ts": 0, "nws_issue_ts": 1751328000}
    with pytest.raises(ValueError, match="NWS training label"):
        fit_daily_models([row], protocol)
