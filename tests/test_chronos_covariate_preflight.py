"""Base-environment NumPy gates; package checks have an explicit isolated runner."""

import unittest

import numpy as np

from research.probes.chronos_covariate_preflight import HOUR, KEY, SyntheticCase, make_synthetic_input


class SyntheticInputGateTests(unittest.TestCase):
    def setUp(self):
        self.case = SyntheticCase("KMIA", 1_000_000 * HOUR, 3)
        self.history = list(range(168))
        self.source = {
            "synthetic_only": True,
            "station_id": "KMIA",
            "run_ms": self.case.decision_ms - 2 * HOUR,
            "eligible_ms": self.case.decision_ms - HOUR,
        }

    def test_hour_grid_target_and_post_target_mask(self):
        for horizon in (1, 3, 6):
            case = SyntheticCase("KMIA", self.case.decision_ms, horizon)
            item, info = make_synthetic_input(case, self.history, self.source, lambda lead: lead)
            self.assertEqual(info["selected_step"], horizon + 1)
            self.assertEqual(info["future_valid_ms"][horizon], case.target_ms)
            self.assertEqual(item["future_covariates"][KEY][horizon], horizon + 2)
            self.assertTrue(np.isnan(item["future_covariates"][KEY][horizon + 1 :]).all())

    def test_metadata_gate_precedes_value_access(self):
        def forbidden(_):
            self.fail("A rejected source caused value access")

        for change in [
            {"station_id": "KOPF"},
            {"run_ms": self.case.decision_ms - HOUR},
            {"eligible_ms": self.case.decision_ms + 1},
            {"synthetic_only": False},
        ]:
            with self.assertRaises(ValueError):
                make_synthetic_input(self.case, self.history, {**self.source, **change}, forbidden)

    def test_missing_trajectory_retains_exact_baseline(self):
        item, info = make_synthetic_input(self.case, self.history, self.source, lambda _: None)
        self.assertEqual(set(item), {"target"})
        np.testing.assert_array_equal(item["target"], self.history)
        self.assertEqual(info["fallback"], "required_guidance_missing")

    def test_missing_last_slot_keeps_original_forecast_origin(self):
        item, info = make_synthetic_input(
            self.case, [*self.history[:-1], None], self.source, lambda lead: lead
        )
        self.assertTrue(np.isnan(item["target"][-1]))
        self.assertEqual(info["context_end_ms"], self.case.decision_ms - HOUR)
        self.assertEqual(info["last_finite_observation_ms"], self.case.decision_ms - 2 * HOUR)
        self.assertEqual(info["selected_step"], 4)


if __name__ == "__main__":
    unittest.main()
