"""Small coherent probability pools fitted on chronological out-of-fit forecasts."""

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.special import logsumexp, softmax


def log_features(market, weather):
    q, p = np.asarray(market, dtype=float), np.asarray(weather, dtype=float)
    if q.ndim != 2 or q.shape != p.shape or q.shape[1] < 2 or q.shape[0] < 1:
        raise ValueError("Mismatched or incomplete forecast partitions")
    if np.any(~np.isfinite(q)) or np.any(~np.isfinite(p)) or np.any(q <= 0) or np.any(p < 0):
        raise ValueError("Invalid market/weather probabilities")
    if not np.allclose(q.sum(axis=1), 1, atol=1e-10, rtol=0) or not np.allclose(
        p.sum(axis=1), 1, atol=1e-10, rtol=0
    ):
        raise ValueError("Forecasts must cover a full probability partition")
    return np.stack((np.log(q), np.log(np.maximum(p, 1e-6))), axis=-1)


def pool_objective(theta, features, winners, weights):
    scores = features @ theta
    probabilities = softmax(scores, axis=1)
    value = np.dot(weights, logsumexp(scores, axis=1) - scores[np.arange(len(winners)), winners])
    prior_delta = theta - np.asarray([1.0, 0.0])
    value += 0.5 * np.dot(prior_delta, prior_delta)
    error = probabilities.copy()
    error[np.arange(len(winners)), winners] -= 1
    gradient = np.einsum("ij,ijk,i->k", error, features, weights) + prior_delta
    return float(value), gradient


@dataclass(frozen=True)
class ForecastPool:
    market_power: float
    weather_power: float

    def __post_init__(self):
        if not np.isfinite([self.market_power, self.weather_power]).all() or not (
            0 <= self.market_power <= 3 and 0 <= self.weather_power <= 3
        ):
            raise ValueError("Pool parameters outside registered bounds")

    def predict(self, market, weather):
        features = log_features(market, weather)
        return softmax(features @ np.asarray([self.market_power, self.weather_power]), axis=1)


def fit_pool(market, weather, winners, weights, market_only=False):
    features = log_features(market, weather)
    y, w = np.asarray(winners), np.asarray(weights, dtype=float)
    if y.ndim != 1 or y.shape != w.shape or len(y) != len(features) or len(y) < 2:
        raise ValueError("Mismatched or insufficient pool training observations")
    if (
        not np.issubdtype(y.dtype, np.integer)
        or np.any(y < 0)
        or np.any(y >= features.shape[1])
        or np.any(~np.isfinite(w))
        or np.any(w <= 0)
    ):
        raise ValueError("Invalid winning categories or training weights")
    fit = minimize(
        pool_objective,
        np.asarray([1.0, 0.0]),
        args=(features, y, w),
        method="L-BFGS-B",
        jac=True,
        bounds=[(0, 3), (0, 0) if market_only else (0, 3)],
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Pool fitting failed: " + str(fit.message))
    return ForecastPool(*map(float, fit.x)), float(fit.fun)


def chronological_prefix(rows, month_start, cutoff_ts):
    """Only labels already issued and settled before a fold may train it."""
    if any(r["split"] != "train" for r in rows):
        raise ValueError("Nontraining rows reached chronological cross-fitting")
    selected = [
        r for r in rows if r["day"] < month_start and max(r["settled_ts"], r["nws_issue_ts"]) < cutoff_ts
    ]
    if not selected:
        raise ValueError("No labels available before cross-fitting boundary")
    return selected
