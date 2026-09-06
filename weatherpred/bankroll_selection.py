"""Simultaneous development bounds from shared circular day-block resampling."""

from numbers import Integral

import numpy as np


def _weight_batches(n_days, block_days, resamples, seed):
    """Replaying the seed reproduces the same draws without retaining all draws."""
    rng = np.random.default_rng(seed)
    blocks = (n_days + block_days - 1) // block_days
    # Bound the temporary resampling arrays as well as the candidate matrices.
    batch_size = min(256, max(1, 1_000_000 // (3 * n_days)))
    offsets = np.arange(block_days)
    for first in range(0, resamples, batch_size):
        count = min(batch_size, resamples - first)
        starts = rng.integers(0, n_days, size=(count, blocks))
        indices = ((starts[..., None] + offsets) % n_days).reshape(count, -1)[:, :n_days]
        flat = (indices + np.arange(count)[:, None] * n_days).ravel()
        weights = np.bincount(flat, minlength=count * n_days).reshape(count, n_days) / n_days
        yield first, weights


def simultaneous_growth_bounds(
    daily_log_growth,
    block_days=7,
    resamples=10000,
    seed=6202301,
    alpha=0.05,
):
    """Return one-sided mean-growth bounds for the entire supplied candidate family.

    Each bootstrap row uses identical circular day blocks across every column.
    Candidate standard errors are sample standard deviations of bootstrap means.
    The family critical value is the conservative empirical 1-alpha quantile of
    max_j((bootstrap_mean_j - observed_mean_j) / standard_error_j). A common
    critical value is then subtracted from every eligible candidate mean.

    This fixed-standard-error bootstrap is a development diagnostic, not proof
    of trading profitability. Constant columns and standard errors <=1e-14 have
    no eligible lower bound, including constant positive or all-zero columns.
    No performance-dependent candidate filtering or selection occurs here.

    Two passes avoid a resamples-by-candidates allocation. Working matrices use
    at most 256 candidate columns and adapt their row count to the day count;
    irreducible storage comprises the input and vectors over days/candidates.
    """
    x = np.asarray(daily_log_growth, dtype=np.float64)
    if x.ndim != 2 or x.shape[0] < 2 or x.shape[1] < 1:
        raise ValueError("Expected at least two days and one candidate in a two-dimensional matrix")
    if not np.isfinite(x).all():
        raise ValueError("Daily log growth must contain only finite values")
    for name, value, minimum in (
        ("block_days", block_days, 1),
        ("resamples", resamples, 2),
        ("seed", seed, 0),
    ):
        if isinstance(value, bool) or not isinstance(value, Integral) or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if block_days > x.shape[0]:
        raise ValueError("block_days cannot exceed the number of days")
    if not np.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("alpha must lie strictly between zero and one")
    n_days, n_candidates = x.shape
    means = x.mean(axis=0)
    varying = np.any(x != x[0], axis=0)
    centered_sum = np.zeros(n_candidates)
    centered_squares = np.zeros(n_candidates)
    column_chunks = [slice(i, min(i + 256, n_candidates)) for i in range(0, n_candidates, 256)]
    if varying.any():
        for _, weights in _weight_batches(n_days, block_days, resamples, seed):
            for columns in column_chunks:
                centered = weights @ x[:, columns] - means[columns]
                centered_sum[columns] += centered.sum(axis=0)
                centered_squares[columns] += np.einsum("ij,ij->j", centered, centered)
    variance = (centered_squares - centered_sum**2 / resamples) / (resamples - 1)
    standard_errors = np.sqrt(np.maximum(variance, 0))
    standard_errors[~varying] = 0
    if not np.isfinite(means).all() or not np.isfinite(standard_errors).all():
        raise ValueError("Numerical overflow while estimating bootstrap means or variance")
    eligible = varying & (standard_errors > 1e-14)
    critical = None
    lower = [None] * n_candidates
    if eligible.any():
        maxima = np.full(resamples, -np.inf)
        for first, weights in _weight_batches(n_days, block_days, resamples, seed):
            current = np.full(len(weights), -np.inf)
            for columns in column_chunks:
                active = eligible[columns]
                if active.any():
                    centered = weights @ x[:, columns] - means[columns]
                    standardized = centered[:, active] / standard_errors[columns][active]
                    current = np.maximum(current, standardized.max(axis=1))
            maxima[first : first + len(weights)] = current
        critical = max(0.0, float(np.quantile(maxima, 1 - alpha, method="higher")))
        bounds = means - critical * standard_errors
        lower = [float(value) if active else None for value, active in zip(bounds, eligible, strict=True)]
    return {
        "means": means.tolist(),
        "standard_errors": standard_errors.tolist(),
        "lower_bounds": lower,
        "critical_value": critical,
        "eligible": eligible.tolist(),
        "n_days": n_days,
        "n_candidates": n_candidates,
        "active_candidates": int(eligible.sum()),
        "degenerate_candidates": int((~eligible).sum()),
        "block_days": int(block_days),
        "resamples": int(resamples),
        "seed": int(seed),
        "alpha": float(alpha),
        "standard_error_floor": 1e-14,
        "quantile_method": "higher",
    }
