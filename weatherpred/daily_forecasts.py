"""Coherent daily integer distributions and small NBM calibration benchmarks."""

import math
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
from scipy.optimize import minimize
from scipy.stats import norm

from weatherpred.calibration import require_training_rows
from weatherpred.contracts import nws_standard_day
from weatherpred.timeutil import parse_time


def daily_features(day, offset, card, object_last_modified):
    start, end = nws_standard_day(date.fromisoformat(day), offset)
    available = parse_time(object_last_modified)
    if available > start or parse_time(card["runtime"]) > available:
        raise ValueError("NBM object was not stored before the source day began")
    expected = list(range(math.ceil(start.timestamp() / 10800) * 10800, int(end.timestamp()), 10800))
    by_time = {int(parse_time(r["valid_at"]).timestamp()): r for r in card["rows"]}
    if len(by_time) != len(card["rows"]) or len(expected) != 8:
        raise ValueError("Duplicate forecasts or unexpected source-day grid")
    rows = [by_time.get(t) for t in expected]
    if any(r is None or r["tmp"] is None or not math.isfinite(r["tmp"]) for r in rows):
        raise ValueError("Incomplete daily temperature forecast grid")
    proxy_time = datetime.combine(
        date.fromisoformat(day) + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    )
    proxy = by_time.get(int(proxy_time.timestamp()))
    if (
        proxy is None
        or any(proxy.get(k) is None or not math.isfinite(proxy[k]) for k in ("txn", "xnd"))
        or proxy["xnd"] < 0
    ):
        raise ValueError("Missing or invalid extrema proxy mean/spread")
    return {
        "grid_max": float(max(r["tmp"] for r in rows)),
        "txn_18h": float(proxy["txn"]),
        "xnd_18h": float(proxy["xnd"]),
        "grid_valid_times": expected,
        "object_last_modified": object_last_modified,
        "source_period_start": start.isoformat(),
        "source_period_end": end.isoformat(),
        "runtime": card["runtime"],
        "version": card["version"],
    }


def integer_cell_bounds(market):
    kind = market["strike_type"]
    if kind == "greater":
        return math.floor(float(market["floor_strike"])) + 0.5, math.inf
    if kind == "less":
        return -math.inf, math.ceil(float(market["cap_strike"])) - 0.5
    if kind == "between":
        lower, upper = float(market["floor_strike"]), float(market["cap_strike"])
        if lower > upper:
            raise ValueError("Inverted interval")
        return math.ceil(lower) - 0.5, math.floor(upper) + 0.5
    raise ValueError("Unsupported integer predicate")


@dataclass(frozen=True)
class DailyDistribution:
    mean: float
    sigma: float
    samples: tuple[float, ...] = ()
    weights: tuple[float, ...] = ()

    def __post_init__(self):
        if not math.isfinite(self.mean) or not math.isfinite(self.sigma) or self.sigma <= 0:
            raise ValueError("Invalid Gaussian parameters")
        if len(self.samples) != len(self.weights) or any(not math.isfinite(x) for x in self.samples):
            raise ValueError("Invalid empirical samples")
        if any(not math.isfinite(w) or w <= 0 for w in self.weights):
            raise ValueError("Invalid empirical weights")

    def probability(self, market):
        lo, hi = integer_cell_bounds(market)
        if hi <= lo:
            return 0.0
        # Survival-function subtraction avoids cancellation in upper tails.
        if lo >= self.mean:
            normal = norm.sf((lo - self.mean) / self.sigma) - norm.sf((hi - self.mean) / self.sigma)
        else:
            normal = norm.cdf((hi - self.mean) / self.sigma) - norm.cdf((lo - self.mean) / self.sigma)
        if not self.samples:
            return float(normal)
        mass = sum(w for x, w in zip(self.samples, self.weights, strict=True) if lo <= x < hi)
        return float((mass + normal) / (sum(self.weights) + 1))

    def cdf(self, value):
        normal = norm.cdf((value - self.mean) / self.sigma)
        if not self.samples:
            return float(normal)
        mass = sum(w for x, w in zip(self.samples, self.weights, strict=True) if x <= value)
        return float((mass + normal) / (sum(self.weights) + 1))

    def interval(self, coverage):
        if not 0 < coverage < 1:
            raise ValueError("Invalid interval coverage")
        if not self.samples:
            return tuple(
                float(x)
                for x in norm.ppf([(1 - coverage) / 2, (1 + coverage) / 2], loc=self.mean, scale=self.sigma)
            )
        limits = [
            min(min(self.samples), self.mean - 12 * self.sigma),
            max(max(self.samples), self.mean + 12 * self.sigma),
        ]
        quantiles = []
        for target in ((1 - coverage) / 2, (1 + coverage) / 2):
            lo, hi = limits
            for _ in range(80):
                mid = (lo + hi) / 2
                if self.cdf(mid) >= target:
                    hi = mid
                else:
                    lo = mid
            quantiles.append(hi)
        return tuple(quantiles)


def residual_fit(rows, feature, weights):
    errors = np.asarray([r["observed_f"] - r["features"][feature] for r in rows])
    w = np.asarray(weights, dtype=float)
    if (
        len(errors) < 2
        or errors.shape != w.shape
        or np.any(~np.isfinite(errors))
        or np.any(~np.isfinite(w))
        or np.any(w <= 0)
    ):
        raise ValueError("Insufficient or malformed training residuals")
    mean = float(np.average(errors, weights=w))
    denominator = w.sum() - np.dot(w, w) / w.sum()
    sigma = max(0.5, float(np.sqrt(np.dot(w, (errors - mean) ** 2) / denominator)))
    return {
        "feature": feature,
        "bias": mean,
        "sigma": sigma,
        "residuals": errors.tolist(),
        "weights": w.tolist(),
        "n": len(errors),
    }


def spread_objective(theta, design, base, spread, observed, weights):
    mean = base + design @ theta[:-2]
    c0, c1 = np.exp(theta[-2:])
    variance = 0.25 + c0 + c1 * spread**2
    residual = observed - mean
    value = 0.5 * np.dot(weights, np.log(2 * np.pi * variance) + residual**2 / variance) + 0.5 * np.dot(
        theta[:-2], theta[:-2]
    )
    gradient_mean = design.T @ (-weights * residual / variance) + theta[:-2]
    dvar = 0.5 * weights * (1 / variance - residual**2 / variance**2)
    gradient = np.r_[gradient_mean, c0 * dvar.sum(), c1 * np.dot(dvar, spread**2)]
    return float(value), gradient


def fit_spread(rows, weights):
    stations = sorted({r["series"] for r in rows})
    design = np.asarray(
        [
            [float(r["series"] == station) for station in stations]
            + [r["features"]["txn_18h"] - r["features"]["grid_max"]]
            for r in rows
        ]
    )
    base = np.asarray([r["features"]["grid_max"] for r in rows])
    spread = np.asarray([r["features"]["xnd_18h"] for r in rows])
    y, w = np.asarray([r["observed_f"] for r in rows]), np.asarray(weights)
    if (
        len(rows) < 30
        or np.any(~np.isfinite(design))
        or np.any(~np.isfinite([*base, *spread, *y, *w]))
        or np.any(w <= 0)
        or np.any(spread < 0)
    ):
        raise ValueError("Insufficient or invalid spread-regression training data")
    variance = max(0.1, float(np.average((y - base) ** 2, weights=w)))
    initial = np.r_[np.zeros(len(stations) + 1), np.log(variance / 2), np.log(0.1)]
    fit = minimize(
        spread_objective,
        initial,
        args=(design, base, spread, y, w),
        method="L-BFGS-B",
        jac=True,
        bounds=[(None, None)] * (len(stations) + 1) + [(-9, 6), (-9, 6)],
        options={"maxiter": 1000, "ftol": 1e-12, "gtol": 1e-8},
    )
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError("Spread regression failed: " + str(fit.message))
    return {"stations": stations, "coefficients": fit.x.tolist(), "n": len(rows), "objective": float(fit.fun)}


def predict_model(models, name, row):
    feature = row["features"]
    if name == "nbm_native_18h_gaussian_proxy":
        return DailyDistribution(feature["txn_18h"], max(0.5, feature["xnd_18h"]))
    if name == "nbm_spread_regression":
        model = models[name]
        if row["series"] not in model["stations"]:
            raise ValueError("Unseen station in spread regression")
        x = np.asarray(
            [float(row["series"] == s) for s in model["stations"]]
            + [feature["txn_18h"] - feature["grid_max"]]
        )
        theta = np.asarray(model["coefficients"])
        mean = feature["grid_max"] + x @ theta[:-2]
        variance = 0.25 + np.exp(theta[-2]) + np.exp(theta[-1]) * feature["xnd_18h"] ** 2
        return DailyDistribution(float(mean), float(np.sqrt(variance)))
    fit = models[name].get(row["series"], models[name]["global"])
    base = feature[fit["feature"]]
    samples = tuple(base + x for x in fit["residuals"]) if name.endswith("empirical") else ()
    return DailyDistribution(
        base + fit["bias"], fit["sigma"], samples, tuple(fit["weights"]) if samples else ()
    )


def fit_daily_models(rows, protocol):
    cutoff = int(parse_time(protocol["fit_cutoff"]).timestamp())
    require_training_rows(rows, protocol["training_start"], protocol["training_end_exclusive"], cutoff)
    if any(r["nws_issue_ts"] >= cutoff for r in rows):
        raise ValueError("NWS training label was not issued before the fit cutoff")
    per_day = Counter(r["day"] for r in rows)
    weights = [1 / per_day[r["day"]] for r in rows]
    fits = {feature: residual_fit(rows, feature, weights) for feature in ("grid_max", "txn_18h")}
    models, audit = {}, []
    for name in protocol["models"]:
        if name == "nbm_native_18h_gaussian_proxy":
            models[name] = {"fit": "none; native guidance proxy"}
        elif name == "nbm_spread_regression":
            models[name] = fit_spread(rows, weights)
        else:
            feature = "txn_18h" if name.startswith("txn_") else "grid_max"
            models[name] = {"global": fits[feature]}
            if "station" in name:
                for station in sorted({r["series"] for r in rows}):
                    group = [r for r in rows if r["series"] == station]
                    fallback = len(group) < protocol["station_minimum_training_days"]
                    if not fallback:
                        models[name][station] = residual_fit(group, feature, [1.0] * len(group))
                    audit.append(
                        {
                            "model": name,
                            "station": station,
                            "training_days": len(group),
                            "global_fallback": fallback,
                        }
                    )
    return models, audit
