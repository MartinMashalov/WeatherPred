"""Small conditional hourly distributions, fitted only on earlier labeled rows.

Ridge means condition on recent trend and fixed daily harmonics. A fixed Student
t alternative allows heavier tails. No historical timestamp is a public receipt.
"""

import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize_scalar
from scipy.stats import norm, t

from weatherpred.forecasts import lattice_boundary

MINUTE = 60_000
DAY = 86_400_000


def design(row, harmonics):
    if harmonics not in (1, 2):
        raise ValueError("Unregistered harmonic count")
    f = row["features"]
    decision, target = row["decision_ms"], row["settlement_ms"]
    if not (
        0 < target - decision <= 30 * MINUTE
        and 5 * MINUTE <= decision - f["last_point_ms"] <= 10 * MINUTE
        and f["first_point_ms"] < f["last_point_ms"]
        and f["points"] >= 2
        and row["horizon_minutes"] in (5, 15, 30)
    ):
        raise ValueError("Future, stale or incompatible conditional features")
    local = datetime.fromtimestamp(target / 1000, UTC).astimezone(ZoneInfo("America/New_York"))
    phase = 2 * math.pi * (local.hour + local.minute / 60) / 24
    result = [f["trend"] - f["persistence"]]
    for harmonic in range(1, harmonics + 1):
        result.extend((math.sin(harmonic * phase), math.cos(harmonic * phase)))
    if not all(math.isfinite(x) for x in [*result, f["persistence"]]):
        raise ValueError("Nonfinite conditional features")
    return result


def fit_scale(residuals, kind):
    errors = np.asarray(residuals, dtype=float)
    if len(errors) < 20 or not np.isfinite(errors).all():
        raise ValueError("Insufficient finite residuals")
    if kind == "gaussian":
        return max(0.05, float(np.sqrt(np.mean(errors**2))))
    if kind != "student_t5":
        raise ValueError("Unregistered distribution")
    result = minimize_scalar(
        lambda log_scale: float(-t.logpdf(errors, df=5, scale=np.exp(log_scale)).mean()),
        bounds=(math.log(0.05), math.log(20)),
        method="bounded",
        options={"xatol": 1e-10},
    )
    if not result.success or not math.isfinite(result.fun):
        raise ValueError("Student scale fitting failed")
    return float(np.exp(result.x))


def fit_model(rows, horizon, candidate, cutoff_ms):
    # Validate all supplied rows before choosing a horizon. A caller cannot hide
    # a future training label by placing it in another horizon group.
    for row in rows:
        if (
            row["settlement_ms"] + 5 * MINUTE >= cutoff_ms
            or not row["settlement_ms"] - 60 * MINUTE <= row["label_point_ms"] <= row["settlement_ms"]
            or not math.isfinite(row["observed"])
        ):
            raise ValueError("Training label unavailable before cutoff")
    selected = [row for row in rows if row["horizon_minutes"] == horizon]
    if len(selected) < 48 or candidate["ridge"] <= 0:
        raise ValueError("Insufficient training or invalid ridge penalty")
    x = np.asarray([design(row, candidate["harmonics"]) for row in selected])
    y = np.asarray([row["observed"] - row["features"]["persistence"] for row in selected])
    days = Counter(row["settlement_ms"] // DAY for row in selected)
    weights = np.asarray([1 / days[row["settlement_ms"] // DAY] for row in selected])
    weights *= len(weights) / weights.sum()
    center = np.average(x, axis=0, weights=weights)
    scale = np.sqrt(np.average((x - center) ** 2, axis=0, weights=weights))
    scale[scale < 1e-12] = 1
    z = (x - center) / scale
    intercept = float(np.average(y, weights=weights))
    coefficients = np.linalg.solve(
        z.T @ (weights[:, None] * z) + candidate["ridge"] * np.eye(z.shape[1]),
        z.T @ (weights * (y - intercept)),
    )
    residuals = y - intercept - z @ coefficients
    return {
        "candidate": dict(candidate),
        "horizon_minutes": horizon,
        "cutoff_ms": cutoff_ms,
        "latest_label_deadline_ms": max(row["settlement_ms"] + 5 * MINUTE for row in selected),
        "training_rows": len(selected),
        "training_days": len(days),
        "center": center.tolist(),
        "feature_scale": scale.tolist(),
        "intercept": intercept,
        "coefficients": coefficients.tolist(),
        "distribution_scale": fit_scale(residuals, candidate["distribution"]),
        "scale_fit": "within-fit residuals; selection folds evaluate complete prediction",
    }


def location(model, row):
    if row["horizon_minutes"] != model["horizon_minutes"] or row["decision_ms"] < model["cutoff_ms"]:
        raise ValueError("Wrong horizon or model fitted after decision")
    x = np.asarray(design(row, model["candidate"]["harmonics"]))
    z = (x - np.asarray(model["center"])) / np.asarray(model["feature_scale"])
    return float(row["features"]["persistence"] + model["intercept"] + z @ model["coefficients"])


def probability(model, row, strike):
    z = (lattice_boundary(strike, True) - location(model, row)) / model["distribution_scale"]
    return float(norm.sf(z) if model["candidate"]["distribution"] == "gaussian" else t.sf(z, df=5))


def log_density(model, row, observed):
    mu, sigma = location(model, row), model["distribution_scale"]
    return float(
        norm.logpdf(observed, loc=mu, scale=sigma)
        if model["candidate"]["distribution"] == "gaussian"
        else t.logpdf(observed, df=5, loc=mu, scale=sigma)
    )


def select_and_fit(rows, protocol):
    """Twelve registered candidates; expanding earlier-day fits, no September data."""
    start, end = protocol["training_start_ms"], protocol["training_end_ms"]
    if any(not start <= row["settlement_ms"] < end for row in rows):
        raise ValueError("Rows outside registered August training period")
    scores, predictions = [], []
    for candidate in protocol["candidates"]:
        daily = defaultdict(list)
        records = []
        for offset in protocol["fold_start_day_offsets"]:
            cutoff = start + offset * DAY
            stop = min(cutoff + protocol["fold_days"] * DAY, end)
            training = [row for row in rows if row["settlement_ms"] + 5 * MINUTE < cutoff]
            models = {h: fit_model(training, h, candidate, cutoff) for h in protocol["horizons_minutes"]}
            for row in rows:
                if cutoff <= row["decision_ms"] and row["settlement_ms"] < stop:
                    model = models[row["horizon_minutes"]]
                    mu = location(model, row)
                    score = -log_density(model, row, row["observed"])
                    if not math.isfinite(score):
                        raise ValueError("Nonfinite selection score")
                    daily[row["settlement_ms"] // DAY].append(score)
                    records.append(
                        {
                            "candidate": candidate["id"],
                            "fold_cutoff_ms": cutoff,
                            "decision_ms": row["decision_ms"],
                            "settlement_ms": row["settlement_ms"],
                            "horizon_minutes": row["horizon_minutes"],
                            "mean": mu,
                            "observed": row["observed"],
                            "negative_log_density": score,
                            "residual": row["observed"] - mu,
                        }
                    )
        if len(daily) != 8:
            raise ValueError("The registered eight selection days are incomplete")
        scores.append(
            {
                **candidate,
                "mean_day_negative_log_density": float(np.mean([np.mean(v) for v in daily.values()])),
                "daily_scores": {str(d): float(np.mean(v)) for d, v in sorted(daily.items())},
                "rows": len(records),
                "days": len(daily),
                "rmse": float(np.sqrt(np.mean([r["residual"] ** 2 for r in records]))),
            }
        )
        predictions.extend(records)
    scores.sort(key=lambda s: (s["mean_day_negative_log_density"], s["id"]))
    chosen = next(c for c in protocol["candidates"] if c["id"] == scores[0]["id"])
    models = {}
    for h in protocol["horizons_minutes"]:
        model = fit_model(rows, h, chosen, end)
        errors = [
            r["residual"] for r in predictions if r["candidate"] == chosen["id"] and r["horizon_minutes"] == h
        ]
        model["distribution_scale"] = fit_scale(errors, chosen["distribution"])
        model["scale_fit"] = "earlier-fold prediction errors within August model-selection data"
        model["scale_training_rows"] = len(errors)
        models[str(h)] = model
    return {
        "chosen_candidate": chosen,
        "candidate_scores": scores,
        "models": models,
        "predictions": predictions,
    }
