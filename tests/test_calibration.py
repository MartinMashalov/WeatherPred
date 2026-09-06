from datetime import date, timedelta

import numpy as np
import pytest
from scipy.optimize import approx_fprime

from weatherpred.calibration import (
    binary_metrics,
    block_mean_interval,
    fit_logistic,
    holm_adjust,
    logistic_objective,
    require_training_rows,
)


def test_logistic_gradient_against_numerical_derivative():
    x = np.asarray([-4.0, -1, 0, 2, 4])
    y = np.asarray([0.0, 1, 0, 1, 1])
    w = np.asarray([0.25, 0.5, 1, 0.75, 0.25])
    theta = np.asarray([0.3, 0.8])
    _, analytic = logistic_objective(theta, x, y, w, 1)
    numerical = approx_fprime(theta, lambda t: logistic_objective(t, x, y, w, 1)[0], 1e-7)
    np.testing.assert_allclose(analytic, numerical, atol=1e-6)


def test_known_calibrated_frequency_table_recovers_identity_without_penalty():
    # Three independent frequency groups with exact observed fractions.
    p = np.repeat([0.1, 0.5, 0.9], 100)
    y = np.concatenate([np.r_[np.ones(n), np.zeros(100 - n)] for n in (10, 50, 90)])
    fitted = fit_logistic(p, y, np.ones(300), penalty=0)
    assert fitted.intercept == pytest.approx(0, abs=1e-7)
    assert fitted.slope == pytest.approx(1, abs=1e-7)


def test_fit_guard_rejects_late_settlement_and_validation_data():
    row = {"split": "train", "day": "2025-06-30", "settled_ts": 100}
    require_training_rows([row], "2025-01-01", "2025-07-01", 101)
    with pytest.raises(ValueError, match="not settled"):
        require_training_rows([row], "2025-01-01", "2025-07-01", 100)
    with pytest.raises(ValueError, match="Nontraining"):
        require_training_rows([dict(row, split="validation")], "2025-01-01", "2025-07-01", 101)
    with pytest.raises(ValueError, match="Nontraining"):
        require_training_rows([dict(row, day="2025-10-01")], "2025-01-01", "2025-07-01", 101)


def test_day_block_interval_preserves_constant_paired_difference():
    days = [(date(2025, 7, 1) + timedelta(days=i)).isoformat() for i in range(28)]
    for block in (1, 7, 14):
        result = block_mean_interval(days, [-0.01] * 28, block, resamples=1000)
        assert result["mean_difference"] == pytest.approx(-0.01)
        assert result["lower"] == pytest.approx(-0.01)
        assert result["upper"] == pytest.approx(-0.01)
        assert result["days"] == 28
    with pytest.raises(ValueError, match="consecutive"):
        block_mean_interval(days[::2], [-0.01] * 14, 7)


def test_serial_blocks_widen_interval_for_long_dependent_runs():
    days = [(date(2025, 1, 1) + timedelta(days=i)).isoformat() for i in range(140)]
    values = np.repeat([-1.0, 1], 70)
    independent = block_mean_interval(days, values, 1)
    clustered = block_mean_interval(days, values, 14)
    assert clustered["upper"] - clustered["lower"] > 2 * (independent["upper"] - independent["lower"])


def test_scores_and_holm_known_examples():
    scores = binary_metrics([0, 0.5, 1], [0, 1, 1])
    np.testing.assert_allclose(scores["brier"], [0, 0.25, 0])
    assert np.isfinite(scores["log_loss"]).all()
    np.testing.assert_allclose(holm_adjust([0.01, 0.04, 0.03]), [0.03, 0.06, 0.06])
