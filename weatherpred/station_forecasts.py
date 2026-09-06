"""Small hourly station baselines for retrospective forecast development.

The caller preserves actual source receipts separately. Historical observation
ordering here does not establish historical publication or executable trading.
"""

import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np

HOUR = 3_600_000


def describe_case(history, case, timezone, context_hours=168, minimum_finite=120, lag_minutes=15):
    """Use only eligible past hour values, plus the known target's local clock."""
    decision, target = case["decision_ms"], case["target_ms"]
    horizon = case["horizon_hours"]
    if horizon not in (1, 3, 6) or target - decision != horizon * HOUR:
        raise ValueError("Invalid forecast horizon")
    end = ((decision - lag_minutes * 60_000) // HOUR) * HOUR
    times = range(end - (context_hours - 1) * HOUR, end + HOUR, HOUR)
    eligible = [(t, history[t]) for t in times if t in history and math.isfinite(history[t])]
    if len(eligible) < minimum_finite:
        raise ValueError("Insufficient finite historical context")
    last_at, last = eligible[-1]
    if decision - last_at > 2 * HOUR:
        raise ValueError("Last input exceeds the two-hour age limit")

    def past(t):
        if t > end:
            raise ValueError("A baseline feature requested future information")
        value = history.get(t)
        return value if value is not None and math.isfinite(value) else None

    one, three, day = (past(last_at - h * HOUR) for h in (1, 3, 24))
    seasonal = past(target - 24 * HOUR)
    local = datetime.fromtimestamp(target / 1000, UTC).astimezone(ZoneInfo(timezone))
    phase = 2 * math.pi * (local.hour + local.minute / 60) / 24
    features = [
        last,
        last - one if one is not None else 0,
        last - three if three is not None else 0,
        last - day if day is not None else 0,
        seasonal - last if seasonal is not None else 0,
        math.sin(phase),
        math.cos(phase),
        math.sin(2 * phase),
        math.cos(2 * phase),
        (target - last_at) / HOUR,
        *[float(v is None) for v in (one, three, day, seasonal)],
    ]
    return {
        "last_f": last,
        "last_input_ms": last_at,
        "context_end_ms": end,
        "finite_context": len(eligible),
        "seasonal_f": seasonal if seasonal is not None else last,
        "features": features,
    }


def design(cases, stations):
    return np.asarray(
        [c["features"] + [float(c["station_id"] == station) for station in stations] for c in cases],
        dtype=float,
    )


def fit_ridge(cases, penalty, cutoff_ms):
    """Fit residuals on earlier targets with equal aggregate weight per UTC day."""
    if not cases or any(c["target_ms"] >= cutoff_ms for c in cases):
        raise ValueError("A training label reaches or exceeds the fitting cutoff")
    if penalty <= 0 or not math.isfinite(penalty):
        raise ValueError("Ridge penalty must be positive")
    stations = sorted({c["station_id"] for c in cases})
    x = design(cases, stations)
    y = np.asarray([c["observed_f"] - c["last_f"] for c in cases])
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("Nonfinite training feature or target")
    days = np.asarray([c["target_ms"] // (24 * HOUR) for c in cases])
    _, inverse, count = np.unique(days, return_inverse=True, return_counts=True)
    weights = 1 / count[inverse]
    weights *= len(weights) / weights.sum()
    mean = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - mean) ** 2, axis=0, weights=weights))
    scale = np.where(scale > 1e-8, scale, 1.0)
    z = (x - mean) / scale
    intercept = float(np.average(y, weights=weights))
    coefficients = np.linalg.solve(
        z.T @ (weights[:, None] * z) + penalty * np.eye(x.shape[1]), z.T @ (weights * (y - intercept))
    )
    return {
        "stations": stations,
        "mean": mean.tolist(),
        "scale": scale.tolist(),
        "intercept": intercept,
        "coefficients": coefficients.tolist(),
        "penalty": penalty,
        "fit_cutoff_ms": cutoff_ms,
        "training_rows": len(cases),
        "training_days": len(count),
        "last_training_target_ms": max(c["target_ms"] for c in cases),
    }


def predict_ridge(model, cases):
    x = design(cases, model["stations"])
    z = (x - np.asarray(model["mean"])) / np.asarray(model["scale"])
    return (
        np.asarray([c["last_f"] for c in cases]) + model["intercept"] + z @ np.asarray(model["coefficients"])
    )


def quantile_offsets(predicted, observed, levels):
    """Calibrate each probability level using a separate, earlier calibration panel."""
    predicted, observed, levels = np.asarray(predicted), np.asarray(observed), np.asarray(levels)
    if predicted.shape != (len(observed), len(levels)) or not 0 < levels.min() < levels.max() < 1:
        raise ValueError("Invalid calibration panel shape or levels")
    if not np.isfinite(predicted).all() or not np.isfinite(observed).all():
        raise ValueError("Nonfinite calibration values")
    return np.asarray([np.quantile(observed - predicted[:, j], level) for j, level in enumerate(levels)])


def calibrated_quantiles(predicted, offsets):
    """Rearrange corrected levels monotonically; never alter outcomes to calibrate."""
    return np.sort(np.asarray(predicted) + np.asarray(offsets), axis=-1)
