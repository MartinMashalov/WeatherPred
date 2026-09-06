import json

import numpy as np
import pytest

from weatherpred.bankroll_selection import simultaneous_growth_bounds


def test_shared_bootstrap_matches_materialized_reference_and_preserves_cross_candidate_dependence():
    rng = np.random.default_rng(41)
    base = rng.normal(0.001, 0.005, 31)
    x = np.column_stack((base, 2 * base, -base, rng.normal(-0.0005, 0.003, 31)))
    count, block, seed = 311, 7, 813
    result = simultaneous_growth_bounds(x, block, count, seed)
    # Independent direct resampling, without the implementation's weight helper.
    starts = np.random.default_rng(seed).integers(0, len(x), size=(count, (len(x) + block - 1) // block))
    indices = ((starts[..., None] + np.arange(block)) % len(x)).reshape(count, -1)[:, : len(x)]
    boot = x[indices].mean(axis=1)
    means, se = x.mean(axis=0), boot.std(axis=0, ddof=1)
    critical = np.quantile(((boot - means) / se).max(axis=1), 0.95, method="higher")
    np.testing.assert_allclose(result["standard_errors"], se, rtol=1e-12, atol=1e-15)
    assert result["critical_value"] == pytest.approx(critical, rel=1e-12)
    np.testing.assert_allclose(result["lower_bounds"], means - critical * se, rtol=1e-12, atol=1e-15)
    assert result["lower_bounds"][1] == pytest.approx(2 * result["lower_bounds"][0])
    # Repeated columns cross the internal candidate-chunk boundary without
    # inventing additional independent weather samples or another critical value.
    repeated = simultaneous_growth_bounds(np.tile(x, (1, 65)), block, count, seed)
    assert repeated["critical_value"] == pytest.approx(result["critical_value"], rel=1e-12)
    np.testing.assert_allclose(repeated["lower_bounds"], np.tile(result["lower_bounds"], 65), atol=1e-14)
    assert simultaneous_growth_bounds(x, block, count, seed) == result


def test_zero_and_constant_positive_columns_never_receive_eligible_bounds():
    x = np.column_stack((np.zeros(21), np.full(21, 0.01)))
    result = simultaneous_growth_bounds(x, resamples=51)
    assert result["eligible"] == [False, False]
    assert result["standard_errors"] == [0, 0]
    assert result["lower_bounds"] == [None, None]
    assert result["critical_value"] is None
    assert result["active_candidates"] == 0
    assert result["degenerate_candidates"] == 2
    json.dumps(result, allow_nan=False)
    # A full-length circular block has no resampled mean variance, even though
    # the original observations vary. It cannot establish positive evidence.
    varying = simultaneous_growth_bounds(np.arange(7, dtype=float)[:, None], block_days=7, resamples=51)
    assert varying["eligible"] == [False]
    assert varying["lower_bounds"] == [None]


def test_invalid_shapes_nonfinite_inputs_and_resampling_parameters_are_rejected():
    for x in (
        np.zeros(10),
        np.zeros((1, 2)),
        np.zeros((10, 0)),
        [[1, np.nan], [2, 3]],
        [[1, np.inf], [2, 3]],
    ):
        with pytest.raises(ValueError):
            simultaneous_growth_bounds(x)
    x = np.zeros((10, 2))
    for options in (
        {"block_days": 0},
        {"block_days": 11},
        {"block_days": True},
        {"resamples": 1},
        {"alpha": 0},
        {"alpha": 1},
        {"alpha": np.nan},
        {"seed": -1},
    ):
        with pytest.raises(ValueError):
            simultaneous_growth_bounds(x, **options)
