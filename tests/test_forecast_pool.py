import numpy as np
import pytest
from scipy.optimize import check_grad

from weatherpred.forecast_pool import (
    ForecastPool,
    chronological_prefix,
    fit_pool,
    log_features,
    pool_objective,
)


def test_partition_and_zero_weather_tail_keep_finite_probabilities():
    market = [[0.1, 0.2, 0.7], [0.3, 0.4, 0.3]]
    weather = [[0.0, 0.9, 0.1], [0.2, 0.6, 0.2]]
    probabilities = ForecastPool(0.8, 0.4).predict(market, weather)
    np.testing.assert_allclose(probabilities.sum(axis=1), 1, atol=1e-14)
    assert np.all(probabilities > 0)
    np.testing.assert_allclose(ForecastPool(1, 0).predict(market, weather), market, atol=1e-14)
    with pytest.raises(ValueError, match="partition"):
        ForecastPool(1, 1).predict([[0.1, 0.2]], [[0.4, 0.6]])


def test_multiclass_objective_gradient_and_informative_forecast():
    market = np.full((60, 3), 1 / 3)
    winners = np.arange(60) % 3
    weather = np.full((60, 3), 0.1)
    weather[np.arange(60), winners] = 0.8
    weights = np.ones(60)
    features = log_features(market, weather)
    theta = np.asarray([1.1, 0.7])
    assert (
        check_grad(
            lambda x: pool_objective(x, features, winners, weights)[0],
            lambda x: pool_objective(x, features, winners, weights)[1],
            theta,
        )
        < 1e-5
    )
    model, _ = fit_pool(market, weather, winners, weights)
    baseline, _ = fit_pool(market, weather, winners, weights, market_only=True)
    assert model.weather_power > 0
    assert baseline.weather_power == 0
    assert np.all(model.predict(market, weather)[np.arange(60), winners] > 1 / 3)
    # Permuting category labels must only permute predicted categories.
    perm = [2, 0, 1]
    np.testing.assert_allclose(
        model.predict(market[:, perm], weather[:, perm]), model.predict(market, weather)[:, perm]
    )


def test_chronological_folds_exclude_future_and_delayed_labels():
    base = {"split": "train", "day": "2025-01-30", "settled_ts": 100, "nws_issue_ts": 99}
    rows = [
        base,
        dict(base, day="2025-02-01"),
        dict(base, settled_ts=101),
        dict(base, nws_issue_ts=101),
    ]
    assert chronological_prefix(rows, "2025-02-01", 101) == [base]
    with pytest.raises(ValueError, match="Nontraining"):
        chronological_prefix([dict(base, split="validation")], "2025-02-01", 101)


def test_pool_rejects_invalid_labels_weights_and_nonfinite_inputs():
    for winners, weights in (([0, 2], [1, 1]), ([0, 1], [0, 1]), ([0.5, 1], [1, 1])):
        with pytest.raises(ValueError):
            fit_pool([[0.5, 0.5]] * 2, [[0.6, 0.4]] * 2, winners, weights)
    with pytest.raises(ValueError):
        ForecastPool(1, float("nan"))
