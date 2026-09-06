"""Small distributional benchmarks with explicit feature-time gates.

Historical lag assumptions belong only in research. A forward caller must supply
only records actually received before its decision. Neither mode implies a fill.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
from scipy.stats import norm

from weatherpred.index import canonical_point


class IndexSeries:
    def __init__(self, points):
        unique = {}
        for point in points:
            if not canonical_point(point):
                continue
            if not math.isfinite(float(point["v"])):
                raise ValueError("Nonfinite index value")
            if point["t"] in unique and unique[point["t"]]["v"] != point["v"]:
                raise ValueError("Conflicting canonical values require a revision audit")
            unique[point["t"]] = point
        self.points = [unique[t] for t in sorted(unique)]
        self.times = [p["t"] for p in self.points]

    def label(self, settlement_ms):
        end = bisect_right(self.times, settlement_ms)
        if end and self.times[end - 1] >= settlement_ms - 3_600_000:
            return self.points[end - 1]
        return None

    def features(self, decision_ms, settlement_ms, lag_ms, max_age_ms, lookback_ms):
        if settlement_ms <= decision_ms or lag_ms < 0:
            raise ValueError("Invalid forecast decision or availability lag")
        cutoff = decision_ms - lag_ms
        end = bisect_right(self.times, cutoff)
        if not end:
            return None
        last = self.points[end - 1]
        if decision_ms - last["t"] > max_age_ms:
            return None
        start = bisect_right(self.times, last["t"] - lookback_ms - 1)
        window = self.points[start:end]
        if len(window) < 2:
            return None
        x = np.asarray([(p["t"] - last["t"]) / 60_000 for p in window])
        y = np.asarray([p["v"] for p in window], dtype=float)
        slope = float(np.dot(x - x.mean(), y - y.mean()) / np.dot(x - x.mean(), x - x.mean()))
        return {
            "persistence": float(last["v"]),
            "trend": float(last["v"]) + slope * (settlement_ms - last["t"]) / 60_000,
            "slope_f_per_minute": slope,
            "last_point_ms": last["t"],
            "first_point_ms": window[0]["t"],
            "points": len(window),
            "last_status": last["status"],
        }


def lattice_boundary(strike, greater):
    """Boundary in a latent distribution rounded to the published 0.01 F grid."""
    scaled = Decimal(str(strike)) * 100
    if greater:
        return float((scaled.to_integral_value(rounding=ROUND_FLOOR) + Decimal("0.5")) / 100)
    return float((scaled.to_integral_value(rounding=ROUND_CEILING) - Decimal("0.5")) / 100)


@dataclass(frozen=True)
class ResidualDistribution:
    residuals: tuple[float, ...]
    kind: str

    def __post_init__(self):
        if self.kind not in ("gaussian", "empirical"):
            raise ValueError("Unknown distribution")
        if len(self.residuals) < 2 or not all(math.isfinite(x) for x in self.residuals):
            raise ValueError("At least two finite training residuals are required")

    @property
    def bias(self):
        return float(np.mean(self.residuals))

    @property
    def sigma(self):
        return max(0.05, float(np.std(self.residuals, ddof=1)))

    def probability(self, base, market):
        """Score the numeric strike, never the possibly stale display subtitle."""
        if market["strike_type"] != "greater":
            raise ValueError("This Miami benchmark supports only strict greater strikes")
        boundary = lattice_boundary(market["floor_strike"], True)
        if self.kind == "gaussian":
            return float(norm.sf((boundary - base - self.bias) / self.sigma))
        # Same rounding-cell boundary as Gaussian, with Jeffreys smoothing of
        # the training exceedance count. CRPS/intervals use the empirical law.
        samples = np.asarray(self.residuals) + base
        return float((np.count_nonzero(samples >= boundary) + 0.5) / (len(samples) + 1))

    def crps(self, base, observed):
        """Continuous ranked probability score in degrees F; smaller is better."""
        if self.kind == "gaussian":
            z = (observed - base - self.bias) / self.sigma
            return float(self.sigma * (z * (2 * norm.cdf(z) - 1) + 2 * norm.pdf(z) - 1 / np.sqrt(np.pi)))
        sample = np.sort(np.asarray(self.residuals) + base)
        n = len(sample)
        # E|X-y| - 1/2 E|X-X'|, evaluated without an n-by-n matrix.
        pair_term = np.dot(2 * np.arange(1, n + 1) - n - 1, sample) / n**2
        return float(np.mean(np.abs(sample - observed)) - pair_term)

    def interval(self, base, coverage):
        tail = (1 - coverage) / 2
        if self.kind == "gaussian":
            return tuple(float(x) for x in norm.ppf([tail, 1 - tail], loc=base + self.bias, scale=self.sigma))
        return tuple(float(x) for x in np.quantile(np.asarray(self.residuals) + base, [tail, 1 - tail]))


def fit_residuals(examples, base_name, horizon, train_end_ms):
    rows = [r for r in examples if r["horizon_minutes"] == horizon]
    if any(r["settlement_ms"] + 300_000 >= train_end_ms for r in rows):
        raise ValueError("Training label is unavailable before the training cutoff")
    return tuple(r["observed"] - r["features"][base_name] for r in rows)


def candle_quote(candles, decision_ts, max_age_seconds=60):
    """A retrospective quote benchmark, never an executable depth/fill claim."""
    eligible = [c for c in candles if c["end_period_ts"] <= decision_ts]
    if not eligible:
        return None
    latest = max(eligible, key=lambda c: c["end_period_ts"])
    if decision_ts - latest["end_period_ts"] > max_age_seconds:
        return None
    bid = latest.get("yes_bid", {}).get("close_dollars")
    ask = latest.get("yes_ask", {}).get("close_dollars")
    if bid is None or ask is None:
        return None
    bid, ask = float(bid), float(ask)
    if not 0 < bid < ask < 1:
        return None
    return {"bid": bid, "ask": ask, "midpoint": (bid + ask) / 2, "end_period_ts": latest["end_period_ts"]}
