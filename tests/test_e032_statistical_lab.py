"""Synthetic end-to-end checks; no released market data or performance reads."""

import copy
import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research.experiments.e032_statistical_lab import (
    DEFAULT_CONFIG,
    choose_decisions,
    compare_accounts,
    digest,
    execute,
    fit_predict_candidate,
    register,
    replay_candidate,
    stamp,
    summarize_account,
)
from weatherpred.archive import Archive, canonical
from weatherpred.statistical_replay_data import build_opportunities, make_private_lookup, resolve_intents
from weatherpred.timeutil import utcnow


def synthetic_dataset():
    markets = []
    for day_index in range(225):
        day = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=day_index)
        decision = int((day + timedelta(hours=18)).timestamp())
        event = f"KXHIGHAUS-{day:%y%b%d}".upper()
        source = {
            "source_record_id": 1,
            "body_sha256": "a" * 64,
            "record_sha256": "b" * 64,
            "available_at": "2026-09-06T19:00:00Z",
            "source_row_index": day_index,
        }
        for j, (kind, low, high) in enumerate(
            (("less", None, "69"), ("between", "69", "71"), ("greater", "71", None))
        ):
            quotes = {
                str(decision + h * 3600): {"bid": f"{0.18 + 0.1 * j:.4f}", "ask": f"{0.22 + 0.1 * j:.4f}"}
                for h in (-3, -1, 0, 1, 2)
            }
            markets.append(
                {
                    "metadata": {
                        "ticker": f"{event}-{j}",
                        "event": event,
                        "day": day.date().isoformat(),
                        "series": "KXHIGHAUS",
                        "source_window_status": "nws_standard_time_mapping",
                        "source_period_end": datetime.fromtimestamp(decision + 12 * 3600, UTC).isoformat(),
                        "open_ts": decision - 5 * 3600,
                        "close_ts": decision + 14 * 3600,
                        "settled_ts": decision + 15 * 3600,
                        "outcome": int(j == day_index % 3),
                        "expiration_value": "DO NOT EXPOSE",
                        "strike_type": kind,
                        "floor_strike": low,
                        "cap_strike": high,
                        "metadata_provenance": source,
                    },
                    "quotes": quotes,
                    "quote_provenance": {t: source for t in quotes},
                }
            )
    return {"complete_event_census": True, "contracts": len(markets), "markets": markets}


def config():
    return json.loads(DEFAULT_CONFIG.read_bytes())


@pytest.fixture
def dataset():
    return synthetic_dataset()


@pytest.mark.parametrize("candidate_name", ["midpoint", "logistic_offset", "ridge_net_return"])
def test_future_outcome_and_quote_mutation_preserves_earlier_model_and_decisions(
    tmp_path, dataset, candidate_name
):
    settings = config()
    candidate = next(c for c in settings["candidates"] if c["id"] == candidate_name)
    changed = copy.deepcopy(dataset)
    cutoff = stamp("2026-04-01")
    for market in changed["markets"]:
        if market["metadata"]["settled_ts"] >= cutoff:
            market["metadata"]["outcome"] = 1 - market["metadata"]["outcome"]
        for when, quote in market["quotes"].items():
            if int(when) >= cutoff:
                quote.update(bid=".51", ask=".55")
    results = []
    for index, values in enumerate((dataset, changed)):
        archive = Archive(tmp_path / str(index))
        try:
            results.append(
                fit_predict_candidate(
                    archive,
                    1,
                    candidate,
                    build_opportunities(values)["opportunities"],
                    make_private_lookup(values),
                    settings,
                )
            )
        finally:
            archive.close()
    for key in ("predictions", "decisions"):
        before = [r for r in results[0][key] if r["decision_ts"] < cutoff]
        after = [r for r in results[1][key] if r["decision_ts"] < cutoff]
        assert before == after
    assert (
        results[0]["provenance"]["folds"][0]["model_sha256"]
        == results[1]["provenance"]["folds"][0]["model_sha256"]
    )


def test_selection_does_not_subtract_return_costs_twice_or_change_with_future_fields(dataset):
    row = build_opportunities(dataset)["opportunities"][0]
    prediction = {
        **row,
        "opportunity": row,
        "eligible": True,
        "kind": "net_return",
        "prediction": [0.04, -0.03],
    }
    selected = choose_decisions([prediction], "ridge_net_return", config())
    assert len(selected["decisions"]) == 1
    assert selected["decisions"][0]["predicted_edge"] == 0.04
    assert selected["decisions"][0]["side"] == "yes"
    assert (
        choose_decisions([{**prediction, "outcome": 0, "future_ask": 0.99}], "ridge_net_return", config())
        == selected
    )


def test_original_limit_stress_rejects_without_rewriting_signal_or_paper_cash(dataset):
    row = next(r for r in build_opportunities(dataset)["opportunities"] if r["day"] == "2026-03-03")
    predictions = [{**row, "opportunity": row, "eligible": True, "kind": "probability", "prediction": 0.95}]
    settings = config()
    selected = choose_decisions(predictions, "logistic_offset", settings)["decisions"]
    proof = {
        "record_id": 1,
        "body_sha256": "a" * 64,
        "record_sha256": "b" * 64,
        "decisions_sha256": digest(selected),
    }
    accounts = []
    for scenario in settings["scenarios"]:
        resolved = resolve_intents(
            selected,
            make_private_lookup(dataset),
            scenario,
            stamp(settings["account_end"]),
            prediction_artifact=proof,
        )
        result = replay_candidate(resolved["orders"], settings, "logistic_offset", scenario)
        accounts.append(result)
        assert result["audit"]["accounting_reproduced"]
        verified = replay_candidate(resolved["orders"], settings, "logistic_offset", scenario, "verified")
        assert verified["account"]["cash"] == 200
        assert not verified["account"]["accepted_trades"]
    assert len(accounts[0]["account"]["accepted_trades"]) == 1
    assert len(accounts[1]["account"]["accepted_trades"]) == 0
    assert accounts[1]["account"]["cash"] == 200
    assert accounts[0]["account"]["schedule_metadata"]["cash_initializations"] == 1


def test_cash_calendar_and_all_candidate_diagnostics_are_retained():
    settings = config()
    accounts = {
        f"{c['id']}:{s['name']}": replay_candidate([], settings, c["id"], s)
        for c in settings["candidates"]
        for s in settings["scenarios"]
    }
    result = compare_accounts(accounts, settings)
    assert len(result["columns"]) == 8
    assert set(result["bounds"]) == {"1", "7", "14"}
    for account in accounts.values():
        assert len(account["account"]["daily"]) == 365
        assert summarize_account(account)["cash"] == 200
    assert all(not g["further_research_candidate"] for g in result["research_gate"].values())


@pytest.mark.parametrize("unreleased", [False, True])
def test_full_registered_synthetic_campaign_keeps_all_four_trials(tmp_path, dataset, unreleased):
    if unreleased:
        for market in dataset["markets"]:
            market["metadata"]["settled_ts"] = None
    archive = Archive(tmp_path / "archive")
    try:
        body = gzip.compress(canonical(dataset).encode(), mtime=0)
        dataset_id = archive.append("e024_dataset_gzip", "synthetic-only", utcnow(), {}, body)
        settings = config()
        settings.update(
            experiment="E032-SYNTHETIC-ONLY", dataset_record_id=dataset_id, dataset_body_sha256=digest(body)
        )
        path = tmp_path / "config.json"
        path.write_text(canonical(settings))
        registration = register(archive, path)
        receipt = execute(archive, registration, tmp_path / "outputs")
        report = json.loads(Path(receipt["report_path"]).read_bytes())
        assert report["campaign_summary"]["attempted_trials"] == 4
        assert report["campaign_summary"]["results"] == (2 if unreleased else 4), report["trials"]
        assert report["campaign_summary"]["failures"] == (2 if unreleased else 0)
        assert len(report["comparison"]["columns"]) == (4 if unreleased else 8)
        assert len(report["comparison"]["planned_columns"]) == 8
        if unreleased:
            assert all(
                "incomplete_campaign" in r["reasons"] for r in report["comparison"]["research_gate"].values()
            )
        assert not report["profitability_proven"]
        assert report["actual_fills"] == 0
        assert report["coverage"]["input_contracts"] == 675
        if not unreleased:
            for name in ("logistic_offset", "ridge_net_return"):
                folds = archive.db.execute(
                    "SELECT id,key FROM records WHERE kind='e032_fold_predictions_gzip' AND key LIKE ? ORDER BY id",
                    (f"{registration}:{name}:%",),
                ).fetchall()
                labels = archive.db.execute(
                    "SELECT id,key FROM records WHERE kind='e032_training_labels_gzip' AND key LIKE ? ORDER BY id",
                    (f"{registration}:{name}:%",),
                ).fetchall()
                assert len(folds) == len(labels) == 6
                for i in range(5):
                    assert labels[i]["id"] < folds[i]["id"] < labels[i + 1]["id"]
        with pytest.raises(ValueError, match="already started"):
            execute(archive, registration, tmp_path / "outputs")
        assert archive.verify()["records_verified"] > 50
    finally:
        archive.close()


def test_comparison_rejects_equal_length_but_different_calendar():
    settings = config()
    result = replay_candidate([], settings, "cash", settings["scenarios"][0])
    result["account"]["daily"][-1]["timestamp"] -= 1
    with pytest.raises(ValueError, match="complete contiguous"):
        compare_accounts({"cash:costed": result}, settings)
