import numpy as np
import pytest

from weatherpred.station_forecasts import (
    HOUR,
    calibrated_quantiles,
    describe_case,
    fit_ridge,
    predict_ridge,
    quantile_offsets,
)


def test_hourly_features_ignore_future_values_and_keep_known_target_clock():
    case = {"decision_ms": 200 * HOUR, "target_ms": 203 * HOUR, "horizon_hours": 3}
    history = {hour * HOUR: 60 + hour / 100 for hour in range(30, 220)}
    first = describe_case(history, case, "America/New_York")
    for hour in range(200, 220):
        history[hour * HOUR] = -9000
    assert describe_case(history, case, "America/New_York") == first
    assert first["last_input_ms"] == 199 * HOUR
    assert first["seasonal_f"] == history[179 * HOUR]
    with pytest.raises(ValueError, match="context"):
        describe_case({199 * HOUR: 70}, case, "UTC")


def test_ridge_learns_earlier_residuals_and_rejects_future_training_labels():
    cases = [
        {
            "station_id": "KAAA",
            "target_ms": i * HOUR,
            "last_f": 70,
            "features": [i / 20],
            "observed_f": 72 + i / 10,
        }
        for i in range(100)
    ]
    model = fit_ridge(cases, 1, 100 * HOUR)
    future = [
        {"station_id": "KAAA", "target_ms": 110 * HOUR, "last_f": 70, "features": [5.5], "observed_f": -100}
    ]
    assert abs(predict_ridge(model, future)[0] - 83) < 0.1
    future[0]["observed_f"] = 10000
    assert abs(predict_ridge(model, future)[0] - 83) < 0.1
    with pytest.raises(ValueError, match="cutoff"):
        fit_ridge(cases, 1, 90 * HOUR)


def test_quantile_correction_uses_calibration_errors_and_preserves_order():
    levels = [0.1, 0.5, 0.9]
    offsets = quantile_offsets(np.zeros((5, 3)), np.arange(5), levels)
    assert np.allclose(offsets, [0.4, 2, 3.6])
    assert np.allclose(calibrated_quantiles([[3, 1, 2]], [-1, 0, 0]), [[1, 2, 2]])
