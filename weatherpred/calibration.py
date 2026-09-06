"""Probability calibration and paired day-block diagnostics; no fill assumptions."""

from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit, logit


def logistic_objective(theta, x, y, weights, penalty):
    z = theta[0] + theta[1] * x
    value = np.dot(weights, np.logaddexp(0, z) - y * z) + penalty * theta[1] ** 2 / 2
    error = weights * (expit(z) - y)
    gradient = np.asarray([error.sum(), np.dot(error, x) + penalty * theta[1]])
    return float(value), gradient


@dataclass(frozen=True)
class LogisticCalibration:
    intercept: float
    slope: float
    clip: float = 1e-6

    def predict(self, probabilities):
        p = np.asarray(probabilities, dtype=float)
        if np.any(~np.isfinite(p)) or np.any((p < 0) | (p > 1)):
            raise ValueError("Invalid market probability")
        return expit(self.intercept + self.slope * logit(np.clip(p, self.clip, 1 - self.clip)))


def fit_logistic(probabilities, outcomes, weights, penalty=1.0, clip=1e-6):
    p, y, w = (np.asarray(x, dtype=float) for x in (probabilities, outcomes, weights))
    if p.ndim != 1 or p.shape != y.shape or p.shape != w.shape or len(p) < 2:
        raise ValueError("Mismatched or insufficient calibration observations")
    if not np.isfinite([*p, *y, *w, penalty, clip]).all():
        raise ValueError("Nonfinite calibration input")
    if np.any((p < 0) | (p > 1)) or np.any((y != 0) & (y != 1)) or np.any(w <= 0):
        raise ValueError("Invalid probabilities, outcomes or weights")
    if penalty < 0 or not 0 < clip < 0.5 or len(np.unique(y)) != 2:
        raise ValueError("Invalid calibration regularization or missing outcome class")
    x = logit(np.clip(p, clip, 1 - clip))
    fit = minimize(
        logistic_objective,
        np.asarray([0.0, 1.0]),
        args=(x, y, w, penalty),
        method="L-BFGS-B",
        jac=True,
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Calibration optimization failed: " + str(fit.message))
    return LogisticCalibration(float(fit.x[0]), float(fit.x[1]), clip)


def binary_metrics(probabilities, outcomes, clip=1e-6):
    p, y = np.asarray(probabilities, dtype=float), np.asarray(outcomes, dtype=float)
    if p.shape != y.shape or np.any(~np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("Invalid probability score input")
    if np.any((y != 0) & (y != 1)):
        raise ValueError("Binary scores require binary outcomes")
    bounded = np.clip(p, clip, 1 - clip)
    return {"brier": (p - y) ** 2, "log_loss": -y * np.log(bounded) - (1 - y) * np.log1p(-bounded)}


def require_training_rows(rows, start_day, end_day, fit_cutoff_ts):
    """Reject validation rows and labels that settled at/after model fitting."""
    if not rows:
        raise ValueError("No training observations")
    for r in rows:
        if r["split"] != "train" or not start_day <= r["day"] < end_day:
            raise ValueError("Nontraining date reached the calibration fit")
        if r.get("settled_ts") is None or r["settled_ts"] >= fit_cutoff_ts:
            raise ValueError("Training outcome was not settled before fitting")


def block_mean_interval(days, values, block_days, resamples=10_000, seed=20260906, alpha=0.05):
    """Paired circular day-block interval; an approximate dependence diagnostic.

    Caller supplies ONE candidate-minus-baseline mean per calendar day, keeping
    cities and contracts together. Blocks wrap across the endpoints; stationarity
    is an assumption, and seasonal/regime coverage must be reported separately.
    """
    x = np.asarray(values, dtype=float)
    dates = [date.fromisoformat(d) for d in days]
    if len(x) != len(dates) or len(x) < 2 or np.any(~np.isfinite(x)):
        raise ValueError("Invalid daily score series")
    if any(b != a + timedelta(days=1) for a, b in pairwise(dates)):
        raise ValueError("Block inference requires consecutive calendar days")
    if not 1 <= block_days <= len(x) or resamples < 100 or not 0 < alpha < 1:
        raise ValueError("Invalid bootstrap parameters")
    rng = np.random.default_rng(seed)
    blocks = (len(x) + block_days - 1) // block_days
    starts = rng.integers(0, len(x), size=(resamples, blocks))
    indices = (starts[..., None] + np.arange(block_days)) % len(x)
    sampled = x[indices.reshape(resamples, -1)[:, : len(x)]].mean(axis=1)
    lower, upper = np.quantile(sampled, [alpha / 2, 1 - alpha / 2])
    # Centered-null one-sided bootstrap p-value for candidate improvement (<0).
    centered = sampled - x.mean()
    pvalue = (1 + np.count_nonzero(centered <= x.mean())) / (resamples + 1)
    return {
        "days": len(x),
        "block_days": block_days,
        "resamples": resamples,
        "seed": seed,
        "mean_difference": float(x.mean()),
        "lower": float(lower),
        "upper": float(upper),
        "one_sided_improvement_pvalue_approximate": float(pvalue),
    }


def holm_adjust(pvalues):
    p = np.asarray(pvalues, dtype=float)
    if p.ndim != 1 or np.any(~np.isfinite(p)) or np.any((p < 0) | (p > 1)):
        raise ValueError("Invalid multiple-comparison p-values")
    order = np.argsort(p)
    adjusted = np.maximum.accumulate((len(p) - np.arange(len(p))) * p[order])
    result = np.empty_like(adjusted)
    result[order] = np.minimum(1, adjusted)
    return result.tolist()
