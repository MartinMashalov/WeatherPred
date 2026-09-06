"""A metadata correction cannot silently change the scientific experiment."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from research.experiments.e024_run_audit_v2 import independent_catalog, validate_protocol


def original_protocol():
    config = json.loads(Path("config/e024_annual_replay.json").read_bytes())
    config["experiment"] = "E024-restricted-annual-walk-forward-v2"
    parent = json.loads(Path(config["parent_policy_config"]).read_bytes())
    return {
        "config": config,
        "policies": independent_catalog(parent),
        "training_accounts_per_look": 4608,
        "source_cutoff_id": 123234,
        "source_cutoff_at": "2026-09-06T19:48:05.160651+00:00",
    }


def test_metadata_revision_does_not_authorize_cost_cash_or_selection_changes():
    protocol = original_protocol()
    validate_protocol(protocol)
    for key, value in (
        ("initial_cash", "1000"),
        ("max_event_fraction", "0.20"),
        ("risk_fractions", ["0.05"]),
        ("familywise_alpha", 0.50),
        ("mandatory_cash_days", 0),
    ):
        changed = deepcopy(protocol)
        changed["config"][key] = value
        with pytest.raises(ValueError, match="original scientific"):
            validate_protocol(changed)
    changed = deepcopy(protocol)
    changed["config"]["scenarios"][0]["entry_coefficient"] = "0"
    with pytest.raises(ValueError, match="original scientific"):
        validate_protocol(changed)


@pytest.mark.parametrize(
    ("key", "value"),
    [("source_cutoff_id", 123235), ("source_cutoff_at", "2026-09-07T00:00:00+00:00")],
)
def test_metadata_revision_cannot_admit_later_sources(key, value):
    protocol = original_protocol()
    protocol[key] = value
    with pytest.raises(ValueError, match="original source cutoff"):
        validate_protocol(protocol)


def test_old_experiment_and_changed_catalog_are_rejected():
    protocol = original_protocol()
    protocol["config"]["experiment"] = "E024-restricted-annual-walk-forward-v1"
    with pytest.raises(ValueError, match="only accepts"):
        validate_protocol(protocol)
    protocol = original_protocol()
    protocol["policies"].reverse()
    with pytest.raises(ValueError, match="576-policy catalog"):
        validate_protocol(protocol)
