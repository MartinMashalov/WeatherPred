"""Twelve actual-package preprocessing checks in the isolated research environment."""

import unittest

import numpy as np
import torch
from chronos.chronos2.dataset import Chronos2Dataset, DatasetMode
from chronos.chronos2.model import Chronos2Encoder
from chronos.chronos_bolt import InstanceNorm

from research.probes.chronos_covariate_preflight import HOUR, KEY, SyntheticCase, make_synthetic_input


class CovariatePreflightTests(unittest.TestCase):
    def setUp(self):
        # Synthetic epoch integers; no real weather series, label or archive.
        self.case = SyntheticCase("KMIA", 1_000_000 * HOUR, 3)
        self.history = [70 + 4 * np.sin(i / 8) for i in range(168)]
        self.source = {
            "synthetic_only": True,
            "station_id": "KMIA",
            "run_ms": self.case.decision_ms - 2 * HOUR,
            "eligible_ms": self.case.decision_ms - HOUR,
        }

    def build(self, case=None, history=None, source=None, semantics="observed_then_guided"):
        return make_synthetic_input(
            case or self.case,
            self.history if history is None else history,
            source or self.source,
            lambda lead: 69 + lead,
            semantics=semantics,
        )

    @staticmethod
    def dataset(inputs):
        return Chronos2Dataset(inputs, 168, 7, 64, 16, mode=DatasetMode.TEST)

    def test_original_steps_and_leads(self):
        for horizon, step in [(1, 2), (3, 4), (6, 7)]:
            case = SyntheticCase("KMIA", self.case.decision_ms, horizon)
            item, info = self.build(case)
            self.assertEqual(info["selected_step"], step)
            self.assertEqual(info["future_valid_ms"][step - 1], case.target_ms)
            self.assertEqual(item["future_covariates"][KEY][step - 1], 71 + horizon)
            self.assertTrue(np.isnan(item["future_covariates"][KEY][step:]).all())

    def test_missing_last_observation_does_not_shift_grid(self):
        item, info = self.build(history=[*self.history[:-1], None])
        self.assertTrue(np.isnan(item["target"][-1]))
        self.assertEqual(info["last_finite_observation_ms"], self.case.decision_ms - 2 * HOUR)
        self.assertEqual(info["context_end_ms"], self.case.decision_ms - HOUR)
        self.assertEqual(info["selected_step"], 4)

    def test_station_cycle_and_publication_gates_precede_value_reads(self):
        def forbidden(_):
            self.fail("Rejected metadata caused a forecast-value read")

        for change in [
            {"station_id": "KOPF"},
            {"run_ms": self.case.decision_ms - HOUR},
            {"eligible_ms": self.case.decision_ms + 1},
            {"synthetic_only": False},
        ]:
            with self.assertRaises(ValueError):
                make_synthetic_input(self.case, self.history, {**self.source, **change}, forbidden)

    def test_missing_required_guidance_retains_baseline(self):
        item, info = make_synthetic_input(self.case, self.history, self.source, lambda _: None)
        self.assertEqual(set(item), {"target"})
        np.testing.assert_array_equal(item["target"], np.asarray(self.history, dtype=np.float32))
        self.assertEqual(info["fallback"], "required_guidance_missing")

    def test_no_label_key_accepted_by_installed_preprocessor(self):
        item, _ = self.build()
        item["settlement_label"] = 999
        with self.assertRaises(ValueError):
            self.dataset([item])

    def test_known_future_key_requires_past_key(self):
        item, _ = self.build()
        item.pop("past_covariates")
        with self.assertRaises(ValueError):
            self.dataset([item])

    def test_dataset_has_no_future_target_and_masks_target_row(self):
        item, _ = self.build()
        batch = next(iter(self.dataset([item])))
        self.assertIsNone(batch["future_target"])
        self.assertEqual(tuple(batch["context"].shape), (2, 168))
        self.assertEqual(batch["group_ids"].tolist(), [0, 0])
        self.assertEqual(batch["target_idx_ranges"], [(0, 1)])
        self.assertTrue(torch.isnan(batch["future_covariates"][0]).all())
        self.assertTrue(torch.isnan(batch["future_covariates"][1, 4:]).all())

    def test_original_sparse_input_accepted_but_scale_degenerate(self):
        item, _ = self.build(semantics="current_cycle_only")
        batch = next(iter(self.dataset([item])))
        norm = InstanceNorm(use_arcsinh=True)
        self.assertEqual(sum(p.numel() for p in norm.parameters()), 0)
        _, (loc, scale) = norm(batch["context"])
        transformed, _ = norm(batch["future_covariates"], (loc, scale))
        self.assertEqual(int(torch.isfinite(batch["context"][1]).sum()), 1)
        self.assertEqual(scale[1].item(), torch.tensor(1e-5).item())
        self.assertGreater(transformed[1, 0].item(), 12)

    def test_observed_then_guided_retains_target_scale(self):
        item, _ = self.build()
        batch = next(iter(self.dataset([item])))
        norm = InstanceNorm(use_arcsinh=True)
        _, (loc, scale) = norm(batch["context"])
        self.assertEqual(loc[0].item(), loc[1].item())
        self.assertEqual(scale[0].item(), scale[1].item())
        self.assertGreater(scale[1].item(), 1)

    def test_append_future_origin_preserves_earlier_arrays(self):
        item, _ = self.build()
        later_case = SyntheticCase("KMIA", self.case.decision_ms + 10 * HOUR, 3)
        later_source = {
            **self.source,
            "run_ms": later_case.decision_ms - 2 * HOUR,
            "eligible_ms": later_case.decision_ms - HOUR,
        }
        later, _ = self.build(later_case, [900 + i for i in range(168)], later_source)
        single = next(iter(self.dataset([item])))
        combined = next(iter(self.dataset([item, later])))
        torch.testing.assert_close(single["context"], combined["context"][:2], equal_nan=True)
        torch.testing.assert_close(
            single["future_covariates"], combined["future_covariates"][:2], equal_nan=True
        )
        self.assertEqual(combined["group_ids"].tolist(), [0, 0, 1, 1])
        permuted = next(iter(self.dataset([later, item])))
        torch.testing.assert_close(single["context"], permuted["context"][2:], equal_nan=True)

    def test_exact_installed_group_mask_blocks_other_origin(self):
        ids = torch.tensor([0, 0, 1, 1])
        mask = Chronos2Encoder._construct_and_invert_group_time_mask(ids, torch.ones(4, 2), torch.float32)
        self.assertTrue((mask[:, :, :2, 2:] == torch.finfo(torch.float32).min).all())
        self.assertTrue((mask[:, :, :2, :2] == 0).all())
        # Deliberate unsafe control demonstrates why cross_learning must stay false.
        unsafe = Chronos2Encoder._construct_and_invert_group_time_mask(
            torch.zeros_like(ids), torch.ones(4, 2), torch.float32
        )
        self.assertTrue((unsafe[:, :, :2, 2:] == 0).all())

    def test_batch_schema_cannot_mix_fallback_and_covariate_task(self):
        item, _ = self.build()
        with self.assertRaises(ValueError):
            self.dataset([item, {"target": item["target"].copy()}])


if __name__ == "__main__":
    unittest.main()
