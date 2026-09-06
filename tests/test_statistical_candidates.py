"""Synthetic model/chronology checks, with no archive or real weather labels."""

import copy
import json

import numpy as np
import pytest
from scipy.special import expit

from weatherpred.statistical_candidates import (
    FEATURE_NAMES,
    _logistic_objective,
    event_balanced_weights,
    fit_candidate,
    predict_candidate,
)


def sample(n=80):
    rng = np.random.default_rng(32001)
    X = rng.normal(size=(n, len(FEATURE_NAMES)))
    X[:, 1] = 0.04
    X[:, 5] = 1
    labels = (X[:, 0] + 1.5 * X[:, 2] > 0).astype(float)
    return X, labels


def fit(name, X, y, *, mask=None, weights=None, times=None):
    if name == "ridge_net_return" and mask is None:
        mask = np.ones(np.asarray(y).shape, dtype=bool)
    return fit_candidate(
        name,
        X,
        y,
        weights=np.ones(len(X)) if weights is None else weights,
        label_known_ts=np.full(np.asarray(y).shape, 99.0) if times is None else times,
        fit_cutoff_ts=100,
        known_mask=mask,
    )


def test_day_event_contract_weights_and_unknown_heads():
    days = ["2026-01-01"] * 4 + ["2026-01-02"]
    events = ["a", "a", "a", "b", "c"]
    np.testing.assert_array_equal(event_balanced_weights(days, events), [1 / 6, 1 / 6, 1 / 6, 1 / 2, 1])
    mask = np.array([[True, True], [True, False], [False, False], [False, True], [True, True]])
    w = event_balanced_weights(days, events, mask)
    np.testing.assert_array_equal(w, [[0.125, 0.125], [0.25, 0], [0, 0], [0, 0.5], [0.5, 0.5]])
    assert w[:4].sum() == 1
    assert w[4:].sum() == 1


def test_weight_identity_and_empty_masks_fail():
    with pytest.raises(ValueError, match="conflicting source days"):
        event_balanced_weights(["day1", "day2"], ["same", "same"])
    with pytest.raises(ValueError, match="No known rows"):
        event_balanced_weights(["day1"], ["a"], np.array([False]))
    with pytest.raises(ValueError, match="Weight mask"):
        event_balanced_weights(["day1"], ["a"], np.array([1]))


@pytest.mark.parametrize("name", ["cash", "midpoint"])
def test_stateless_baselines_never_accept_outcomes(name):
    X, y = sample()
    model = fit_candidate(name, X)
    prediction = predict_candidate(model, X)
    assert model["fitted"] is False
    expected = np.zeros(len(X)) if name == "cash" else expit(X[:, 0])
    np.testing.assert_array_equal(prediction["values"], expected)
    with pytest.raises(ValueError, match="must not receive training outcomes"):
        fit_candidate(name, X, y)


def test_logistic_actual_fit_and_analytic_gradient():
    X, y = sample()
    model = fit("logistic_offset", X, y)
    prediction = predict_candidate(model, X)
    assert prediction["kind"] == "probability"
    assert model["head_diagnostics"][0]["converged"] is True
    assert np.isfinite(prediction["values"]).all()
    assert ((prediction["values"] >= 0) & (prediction["values"] <= 1)).all()
    # A real solver and its derivative, not a mocked estimator.
    z = np.column_stack((np.ones(8), X[:8, :3]))
    beta = np.array([0.2, -0.3, 0.1, 0.4])
    args = (z, X[:8, 0], y[:8], np.arange(1.0, 9), np.array([0, 1, 1, 1]))
    _, gradient = _logistic_objective(beta, *args)
    step = 1e-6
    numeric = []
    for i in range(len(beta)):
        plus, minus = beta.copy(), beta.copy()
        plus[i] += step
        minus[i] -= step
        numeric.append(
            (_logistic_objective(plus, *args)[0] - _logistic_objective(minus, *args)[0]) / (2 * step)
        )
    np.testing.assert_allclose(gradient, numeric, rtol=1e-7, atol=1e-7)


def test_return_ridge_includes_known_reject_zero_and_independent_heads():
    X = np.zeros((6, len(FEATURE_NAMES)))
    y = np.array([[0, 0], [0.4, -0.5], [0, 0], [0.8, -0.5], [999, 0.2], [0.3, 999]])
    mask = np.array([[True, True]] * 4 + [[False, True], [True, False]])
    w = event_balanced_weights(["day"] * 6, ["event"] * 6, mask)
    model = fit("ridge_net_return", X, y, mask=mask, weights=w)
    result = predict_candidate(model, X)
    expected = [np.average(y[mask[:, h], h], weights=w[mask[:, h], h]) for h in range(2)]
    np.testing.assert_allclose(result["values"], np.tile(expected, (6, 1)), rtol=0, atol=1e-14)
    assert result["kind"] == "net_return"
    assert model["head_order"] == ["YES", "NO"]
    assert [h["known_positive_weight_rows"] for h in model["head_diagnostics"]] == [5, 5]
    # Constant columns remain defined; zero labels are not dropped as missing.
    assert model["scale"] == [1.0] * len(FEATURE_NAMES)


@pytest.mark.parametrize("name", ["logistic_offset", "ridge_net_return"])
def test_masked_future_rows_cannot_change_scaler_fit_or_prediction(name):
    X, y = sample()
    if name == "ridge_net_return":
        y = np.column_stack((y - 0.6, 0.3 - y))
    original = fit(name, X, y)
    future_X = np.full((1, X.shape[1]), 1e100)
    extra_y = np.zeros((1,) + y.shape[1:])
    appended_y = np.concatenate((y, extra_y))
    mask = np.ones(appended_y.shape, dtype=bool)
    mask[-1] = False
    times = np.full(appended_y.shape, 99.0)
    times[-1] = 1000
    appended = fit(name, np.vstack((X, future_X)), appended_y, mask=mask, times=times)
    assert original == appended
    np.testing.assert_array_equal(
        predict_candidate(original, X)["values"], predict_candidate(appended, X)["values"]
    )


@pytest.mark.parametrize("name", ["logistic_offset", "ridge_net_return"])
def test_known_future_and_cutoff_labels_rejected_before_fitting(name):
    X, y = sample()
    if name == "ridge_net_return":
        y = np.column_stack((y, -y))
    for late_time in (100, 101):
        times = np.full(y.shape, 99.0)
        times[-1] = late_time
        with pytest.raises(ValueError, match="not strictly before fit cutoff"):
            fit(name, X, y, times=times)


@pytest.mark.parametrize("name", ["logistic_offset", "ridge_net_return"])
def test_repeated_fit_json_roundtrip_and_input_arrays_unchanged(name):
    X, y = sample()
    if name == "ridge_net_return":
        y = np.column_stack((y - 0.6, 0.4 - y))
    before_X, before_y = X.copy(), y.copy()
    model = fit(name, X, y)
    repeated = fit(name, X, y)
    assert model == repeated
    restored = json.loads(json.dumps(model, allow_nan=False))
    np.testing.assert_array_equal(
        predict_candidate(model, X)["values"], predict_candidate(restored, X)["values"]
    )
    np.testing.assert_array_equal(X, before_X)
    np.testing.assert_array_equal(y, before_y)


def test_prediction_cannot_rescale_from_future_cases():
    X, y = sample()
    model = fit("logistic_offset", X, y)
    before = copy.deepcopy(model)
    normal = predict_candidate(model, X[:5])["values"]
    appended = predict_candidate(model, np.vstack((X[:5], np.full((1, X.shape[1]), 1e5))))["values"]
    np.testing.assert_allclose(normal, appended[:5], rtol=0, atol=1e-14)
    assert model == before


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_nonfinite_features_labels_weights_fail(bad):
    X, y = sample()
    broken_X = X.copy()
    broken_X[0, 0] = bad
    with pytest.raises(ValueError, match="Nonfinite input feature"):
        fit("logistic_offset", broken_X, y)
    broken_y = y.copy()
    broken_y[0] = bad
    with pytest.raises(ValueError, match="Labels must be finite"):
        fit("logistic_offset", X, broken_y)
    weights = np.ones(len(X))
    weights[0] = bad
    with pytest.raises(ValueError, match="Weights must be finite"):
        fit("logistic_offset", X, y, weights=weights)


def test_degenerate_training_and_feature_schema_fail():
    X, y = sample()
    with pytest.raises(ValueError, match="at least two training rows"):
        fit("logistic_offset", X[:1], y[:1])
    with pytest.raises(ValueError, match="both binary classes"):
        fit("logistic_offset", X, np.zeros(len(X)))
    with pytest.raises(ValueError, match="known positive-weight"):
        fit("logistic_offset", X, y, weights=np.zeros(len(X)))
    with pytest.raises(ValueError, match="explicit known mask"):
        fit_candidate(
            "ridge_net_return",
            X,
            np.column_stack((y, -y)),
            weights=np.ones(len(X)),
            label_known_ts=np.full((len(X), 2), 99),
            fit_cutoff_ts=100,
        )
    with pytest.raises(ValueError, match="names/order"):
        fit_candidate("midpoint", X, feature_names=tuple(reversed(FEATURE_NAMES)))


def test_invalid_artifact_precision_side_order_and_output_units_fail():
    X, y = sample()
    model = fit("ridge_net_return", X, np.column_stack((y, -y)))
    for key, value in (
        ("scale", [0] * len(FEATURE_NAMES)),
        ("head_order", ["NO", "YES"]),
        ("prediction_kind", "probability"),
    ):
        bad = copy.deepcopy(model)
        bad[key] = value
        with pytest.raises(ValueError):
            predict_candidate(bad, X)


def test_constant_zero_return_is_valid_cashlike_prediction():
    X, _ = sample()
    model = fit("ridge_net_return", X, np.zeros((len(X), 2)))
    np.testing.assert_array_equal(predict_candidate(model, X)["values"], np.zeros((len(X), 2)))
