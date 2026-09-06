"""Synthetic-only Chronos covariate construction. No archive, weights or forecast calls.

This is a preflight fixture builder, not the future real-data adapter. Package
preprocessing checks are in tests/test_chronos_covariate_preflight.py.
"""

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

HOUR = 3_600_000
KEY = "observed_then_guided_temperature_f"


@dataclass(frozen=True)
class SyntheticCase:
    station_id: str
    decision_ms: int
    horizon_hours: int

    @property
    def target_ms(self):
        return self.decision_ms + self.horizon_hours * HOUR

    @property
    def context_end_ms(self):
        return self.decision_ms - HOUR

    @property
    def grid(self):
        return [self.context_end_ms - (167 - i) * HOUR for i in range(168)]

    @property
    def selected_step(self):
        return self.horizon_hours + 1


def make_synthetic_input(
    case: SyntheticCase,
    history: list[float | None],
    source: dict,
    read_guidance: Callable[[int], float | None],
    *,
    semantics: str = "observed_then_guided",
):
    """Construct fixed seven-step input after synthetic station/time gates.

    `read_guidance` allows tests to prove rejected source metadata is checked
    before any forecast value is accessed. Real evidence is intentionally not
    accepted: production must first register the new raw-card extraction.
    """
    if source.get("synthetic_only") is not True:
        raise ValueError("Only synthetic source metadata is allowed in this preflight")
    if case.decision_ms % HOUR or case.horizon_hours not in (1, 3, 6):
        raise ValueError("Case must use the original hourly grid and horizons")
    if semantics not in ("current_cycle_only", "observed_then_guided"):
        raise ValueError("Unknown covariate semantics")
    if source["station_id"] != case.station_id:
        raise ValueError("Guidance station differs from target station")
    if source["run_ms"] != case.decision_ms - 2 * HOUR:
        raise ValueError("Guidance cycle differs from the fixed decision-minus-two rule")
    if source["eligible_ms"] > case.decision_ms:
        raise ValueError("Guidance was not eligible at this decision")
    if len(history) != 168:
        raise ValueError("Context must contain exactly 168 hourly slots")
    values = np.asarray([np.nan if v is None else v for v in history], dtype=np.float32)
    if np.isinf(values).any():
        raise ValueError("Infinite target value")
    finite = np.flatnonzero(np.isfinite(values))
    if len(finite) < 120 or case.decision_ms - case.grid[int(finite[-1])] > 2 * HOUR:
        raise ValueError("History violates original support/age gates")
    baseline = {"target": values.copy()}
    future = np.full(7, np.nan, dtype=np.float32)
    needed = list(range(2, case.horizon_hours + 3))
    if semantics == "current_cycle_only":
        needed = [1, *needed]
    parsed = {}
    for lead in needed:
        value = read_guidance(lead)
        if value is None:
            return baseline, {
                "fallback": "required_guidance_missing",
                "missing_lead": lead,
                "selected_step": case.selected_step,
            }
        parsed[lead] = float(value)
        if not np.isfinite(parsed[lead]):
            raise ValueError("Non-finite guidance")
    for lead in range(2, case.horizon_hours + 3):
        future[lead - 2] = parsed[lead]
    if semantics == "current_cycle_only":
        past = np.full(168, np.nan, dtype=np.float32)
        past[-1] = parsed[1]
        key = "nbh_current_cycle_tmp_f"
    else:
        past = values.copy()
        key = KEY
    return {
        **baseline,
        "past_covariates": {key: past},
        "future_covariates": {key: future},
    }, {
        "fallback": None,
        "semantics": semantics,
        "context_end_ms": case.context_end_ms,
        "last_finite_observation_ms": case.grid[int(finite[-1])],
        "selected_step": case.selected_step,
        "selected_index": case.selected_step - 1,
        "future_valid_ms": [case.decision_ms + i * HOUR for i in range(7)],
        "read_leads": needed,
    }
