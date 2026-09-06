"""Two fixed Chronos input adapters; numeric labels never enter model dictionaries."""

from __future__ import annotations

import math
import re
from collections import defaultdict

import numpy as np

from research.probes.station_transformer import (
    BENCHMARK_END,
    DATA_START,
    HOUR,
    digest,
    index_observations,
    prepare_case,
    receipt_timestamp_ms,
    timestamp_ms,
)

UNIVARIATE = "chronos_pretrained_univariate_matched"
GUIDED = "chronos_observed_then_guided"
VARIANTS = [UNIVARIATE, GUIDED]
KEY = "observed_then_guided_temperature_f"
LEVELS = [0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99]
HEX = re.compile(r"[0-9a-f]{64}")


class HistoryStore:
    """Index identity/time only; normalize values on a causally gated history lookup."""

    def __init__(self, rows):
        self.rows = defaultdict(list)
        self.cache = {}
        for row in rows:
            when = timestamp_ms(row["observed_at"])
            if not DATA_START <= when < BENCHMARK_END:
                continue
            if when % HOUR:
                raise ValueError("Observation metadata left the exact hourly grid")
            self.rows[row["station_id"], when].append(row)

    def prepare(self, case, parent_config):
        outer = self
        end = ((case["decision_ms"] - 900_000) // HOUR) * HOUR

        class CausalLookup:
            def get(self, key):
                station, when = key
                if station != case["station_id"] or when > end or when + 900_000 > case["decision_ms"]:
                    raise ValueError("A target/future observation was requested as a past input")
                if key not in outer.cache:
                    # The frozen validator sees only the already-gated past hour's versions.
                    values, _ = index_observations(outer.rows.get(key, []))
                    outer.cache[key] = values.get(key)
                return outer.cache[key]

        prepared = prepare_case(case, CausalLookup(), parent_config)
        if not prepared["eligible"]:
            raise ValueError("An original common case lost eligible history: " + case["case_id"])
        if prepared["context_end_ms"] != case["decision_ms"] - HOUR:
            raise ValueError("Do not shift the fixed context grid to its last finite point")
        return prepared


def validate_trajectory(case, trajectory):
    """Validate all station/time/provenance metadata before consulting TMP values."""
    for key in ("case_id", "station_id", "split", "decision_ms", "target_ms", "horizon_hours"):
        if trajectory.get(key) != case[key]:
            raise ValueError("Trajectory differs from its original case binding")
    decision, horizon = case["decision_ms"], case["horizon_hours"]
    leads = list(range(2, horizon + 3))
    run = decision - 2 * HOUR
    if trajectory.get("reason") == "original_object_failed":
        if (
            trajectory["run_ms"] != run
            or trajectory["required_leads"] != leads
            or trajectory["available"] is not False
            or trajectory["cells"] != []
            or trajectory["historical_public_availability_verified"] is not False
            or trajectory["future_tmp_f"] != [None] * 7
        ):
            raise ValueError("Failed source object contains guidance or changed identity")
        return trajectory
    if (
        trajectory["run_ms"] != run
        or trajectory["required_leads"] != leads
        or len(trajectory["cells"]) != len(leads)
        or trajectory["historical_public_availability_verified"] is not False
        or type(trajectory["available"]) is not bool
    ):
        raise ValueError("Wrong trajectory grid, availability or run")
    # An extraction-wide absent object may retain null provenance for missing cells.
    for cell, lead in zip(trajectory["cells"], leads, strict=True):
        if (
            cell["station_id"] != case["station_id"]
            or cell["run_ms"] != run
            or cell["forecast_hour"] != lead
            or cell["valid_ms"] != run + lead * HOUR
            or cell["historical_public_availability_verified"] is not False
        ):
            raise ValueError("Trajectory cell station/run/valid-time metadata differs")
        if cell["response_id"] is None:
            if trajectory["available"] or cell["missing_reason"] is None:
                raise ValueError("A usable trajectory cell lacks source provenance")
            continue
        if (
            type(cell["response_id"]) is not int
            or cell["response_id"] <= 0
            or any(
                not isinstance(cell[k], str) or HEX.fullmatch(cell[k]) is None
                for k in ("response_sha256", "response_record_sha256", "card_sha256")
            )
            or not run <= cell["conditional_eligible_at_ms"] <= decision
            or timestamp_ms(cell["conditional_eligible_at"]) != cell["conditional_eligible_at_ms"]
            or timestamp_ms(cell["object_last_modified"]) != cell["object_last_modified_ms"]
            or cell["object_last_modified_ms"] != cell["conditional_eligible_at_ms"]
            or receipt_timestamp_ms(cell["actual_received_at"]) < cell["conditional_eligible_at_ms"]
            or any(type(cell[k]) is not int or cell[k] < 0 for k in ("card_global_offset", "utc_cell_offset"))
        ):
            raise ValueError("Trajectory source/publication metadata is invalid")
        if int(cell["utc_raw_lexeme"].strip()) != (cell["valid_ms"] // HOUR) % 24:
            raise ValueError("Raw trajectory UTC column differs from its exact valid time")
        for key in ("field_line_offset", "cell_offset"):
            if cell[key] is not None and (
                type(cell[key]) is not int or cell[key] < cell["card_global_offset"]
            ):
                raise ValueError("Trajectory TMP offset precedes its archived card")
    future = trajectory["future_tmp_f"]
    if len(future) != 7 or any(value is not None for value in future[horizon + 1 :]):
        raise ValueError("Future covariates must stop at the target on the fixed seven-step grid")
    missing = False
    for index, cell in enumerate(trajectory["cells"]):
        value, token = cell["tmp_f"], cell["raw_lexeme"]
        if value is None:
            missing = True
            if cell["missing_reason"] is None or future[index] is not None:
                raise ValueError("Missing guidance cell has inconsistent value/reason")
            if token is not None and token.strip() not in ("", "-99"):
                raise ValueError("Missing guidance has an unexplained raw lexeme")
        else:
            if (
                cell["response_id"] is None
                or cell["missing_reason"] is not None
                or isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not isinstance(token, str)
                or re.fullmatch(r" *-?\d{1,3}", token) is None
                or int(token) == -99
                or value != int(token)
                or future[index] != value
                or cell["field_line_offset"] is None
                or cell["cell_offset"] is None
            ):
                raise ValueError("Numeric guidance differs from the retained TMP cell")
    if trajectory["available"] != (not missing) or ((trajectory["reason"] is None) != (not missing)):
        raise ValueError("Whole-trajectory availability differs from its required cells")
    return trajectory


def build_input(prepared, trajectory, variant):
    if variant not in VARIANTS:
        raise ValueError("Unregistered model variant")
    values = np.asarray([np.nan if v is None else v for v in prepared["history_values_f"]], dtype=np.float32)
    if len(values) != 168 or np.isinf(values).any():
        raise ValueError("Invalid fixed history array")
    guided = variant == GUIDED and trajectory["available"]
    item = {"target": values.copy()}
    if guided:
        future = np.asarray(
            [np.nan if v is None else v for v in trajectory["future_tmp_f"]], dtype=np.float32
        )
        item["past_covariates"] = {KEY: values.copy()}
        item["future_covariates"] = {KEY: future}
    metadata = {
        "case_id": prepared["case_id"],
        "station_id": prepared["station_id"],
        "decision_ms": prepared["decision_ms"],
        "target_ms": prepared["target_ms"],
        "horizon_hours": prepared["horizon_hours"],
        "context_end_ms": prepared["context_end_ms"],
        "selected_step": prepared["forecast_steps_from_grid_end"],
        "context_sha256": prepared["input_sha256"],
        "trajectory_sha256": digest(trajectory),
        "uses_guidance": guided,
        "fallback_reason": trajectory["reason"] if variant == GUIDED and not guided else None,
        "finite_context_points": prepared["finite_context_points"],
    }
    if metadata["selected_step"] != prepared["horizon_hours"] + 1:
        raise ValueError("Incorrect step selection from the fixed context endpoint")
    return item, metadata


def output_rows(outputs, metadata, levels):
    if levels != LEVELS or len(outputs) != len(metadata):
        raise ValueError("Output quantiles/count differ from the original checkpoint")
    rows = []
    for tensor, meta in zip(outputs, metadata, strict=True):
        array = tensor.detach().cpu().numpy()
        if array.shape != (1, 13, 7) or not np.isfinite(array).all():
            raise ValueError("Model returned nonfinite or incorrectly shaped predictions")
        selected = array[0, :, meta["selected_step"] - 1].astype(float).tolist()
        rows.append(
            {
                "case_id": meta["case_id"],
                "point_f": selected[LEVELS.index(0.5)],
                "quantiles_f": selected,
                "all_quantiles_f": array[0].astype(float).tolist(),
            }
        )
    return rows


def call_model(pipeline, inputs, metadata):
    if pipeline.model.training or any(module.training for module in pipeline.model.modules()):
        raise ValueError("Evaluation mode/dropout-off is required for every model module")
    if len(inputs) != len(metadata) or len({m["case_id"] for m in metadata}) != len(metadata):
        raise ValueError("Input batch has duplicate or mismatched case identities")
    if inputs and any(set(item) != set(inputs[0]) for item in inputs):
        raise ValueError("Univariate and covariate schemas must be batched separately")
    expected_keys = (
        {"target", "past_covariates", "future_covariates"}
        if inputs and "past_covariates" in inputs[0]
        else {"target"}
    )
    if any(set(item) != expected_keys for item in inputs):
        raise ValueError("Unknown feature keys or incomplete covariate schema")
    series_per_case = 2 if "past_covariates" in expected_keys else 1
    if not inputs or len(inputs) * series_per_case > 64:
        raise ValueError("The fixed batch cap counts target and covariate series")
    outputs = pipeline.predict(
        inputs, prediction_length=7, context_length=168, batch_size=64, cross_learning=False
    )
    return output_rows(outputs, metadata, [float(q) for q in pipeline.quantiles])


def integrity_selection(cases, available_ids):
    eligible = sorted(
        (case for case in cases if case["case_id"] in available_ids),
        key=lambda case: (case["decision_ms"], case["case_id"]),
    )
    first = eligible[:4]
    if len(first) != 4:
        raise ValueError("Too few eligible cases for fixed output-integrity checks")
    later = [case for case in eligible if case["decision_ms"] > max(c["decision_ms"] for c in first)][-4:]
    if len(later) != 4:
        raise ValueError("Too few strictly later origins for fixed output-integrity checks")
    return first, later


def compare_outputs(left, right, ids, tolerance):
    a, b = ({r["case_id"]: np.asarray(r["all_quantiles_f"]) for r in rows} for rows in (left, right))
    if not set(ids) <= a.keys() or not set(ids) <= b.keys():
        raise ValueError("Integrity replay lost a case ID")
    maximum = max(float(np.max(np.abs(a[key] - b[key]))) for key in ids)
    if not math.isfinite(maximum) or maximum > tolerance:
        raise ValueError(f"Origin/batch output invariance failed: {maximum} > {tolerance}")
    return maximum
