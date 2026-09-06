import copy
from datetime import UTC, datetime

import pytest

from weatherpred.bankroll_replay import replay
from weatherpred.statistical_replay_data import (
    FEATURE_NAMES,
    HOUR,
    build_opportunities,
    digest,
    label_rows_before,
    make_private_lookup,
    resolve_intents,
    side_limit,
)

D = int(datetime(2026, 1, 10, 18, tzinfo=UTC).timestamp())
COSTED = {"entry_delay_hours": 1, "slippage": ".01", "entry_coefficient": ".07"}
STRESS = {"entry_delay_hours": 2, "slippage": ".02", "entry_coefficient": ".07"}


def source(identifier=1):
    return {
        "source_record_id": identifier,
        "body_sha256": "a" * 64,
        "record_sha256": "b" * 64,
        "available_at": "2026-09-06T19:00:00Z",
        "source_row_index": 0,
    }


def fixture():
    event = "KXHIGHAUS-26JAN10"
    markets = []
    for suffix, kind, low, high in (
        ("T69", "less", None, "69"),
        ("B70", "between", "69", "71"),
        ("T71", "greater", "71", None),
    ):
        meta = {
            "ticker": f"{event}-{suffix}",
            "event": event,
            "day": "2026-01-10",
            "series": "KXHIGHAUS",
            "source_window_status": "nws_standard_time_mapping",
            "source_period_end": datetime.fromtimestamp(D + 12 * HOUR, UTC).isoformat(),
            "open_ts": D - 5 * HOUR,
            "close_ts": D + 14 * HOUR,
            "settled_ts": D + 15 * HOUR,
            "outcome": 1,
            "expiration_value": "NEVER MODEL INPUT",
            "rules_primary": "PRIVATE",
            "strike_type": kind,
            "floor_strike": low,
            "cap_strike": high,
            "metadata_provenance": source(),
        }
        quotes = {
            D - 3 * HOUR: {"bid": ".30", "ask": ".34"},
            D - HOUR: {"bid": ".34", "ask": ".38"},
            D: {"bid": ".38", "ask": ".42"},
            D + HOUR: {"bid": ".39", "ask": ".41"},
            D + 2 * HOUR: {"bid": ".39", "ask": ".42"},
        }
        markets.append(
            {
                "metadata": meta,
                "quotes": quotes,
                "quote_provenance": {t: source(i + 2) for i, t in enumerate(quotes)},
            }
        )
    return {"complete_event_census": True, "contracts": 3, "markets": markets}


def public(dataset=None):
    dataset = fixture() if dataset is None else dataset
    return build_opportunities(dataset), make_private_lookup(dataset)


def decision(opportunity, side="yes"):
    return {
        "opportunity": opportunity,
        "policy_id": "synthetic_model",
        "side": side,
        "limit_price": str(side_limit(opportunity, side)),
    }


def seal(decisions):
    return {
        "record_id": 100,
        "body_sha256": "a" * 64,
        "record_sha256": "b" * 64,
        "decisions_sha256": digest(decisions),
    }


class NoOutcomes(dict):
    def __getitem__(self, key):
        if key in ("outcome", "expiration_value", "settled_ts", "rules_primary"):
            raise AssertionError("Model builder inspected a future label/full rule")
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in ("outcome", "expiration_value", "settled_ts", "rules_primary"):
            raise AssertionError("Model builder inspected a future label/full rule")
        return super().get(key, default)


def test_quote_only_feature_order_values_and_no_private_metadata():
    data = fixture()
    for market in data["markets"]:
        market["metadata"] = NoOutcomes(market["metadata"])
    result = build_opportunities(data)
    assert result["census"] == {
        "input_contracts": 3,
        "opportunities": 3,
        "complete_features": 3,
        "excluded": 0,
        "events": 1,
    }
    by_ticker = {row["ticker"]: row for row in result["opportunities"]}
    row = by_ticker["KXHIGHAUS-26JAN10-B70"]
    assert tuple(row["feature_names"]) == FEATURE_NAMES and len(FEATURE_NAMES) == 15
    assert row["feature_vector"][:7] == pytest.approx([-0.4054651081081643, 0.04, 0.04, 0.08, 1.2, 1.0, 0.5])
    assert row["feature_vector"][9:] == [0.0] * 6
    assert row["decision_ts"] == D and row["no_ask"] == "0.62"
    assert not {"metadata", "outcome", "settled_ts", "expiration_value", "rules_primary"} & row.keys()
    assert row["signal_provenance"]["available_ts"] > D
    assert row["signal_provenance"]["assumed_available_ts"] == D
    assert row["signal_provenance"]["availability_verified"] is False


def test_future_value_mutation_does_not_change_feature_or_opportunity_hash():
    data = fixture()
    original = build_opportunities(data)
    for market in data["markets"]:
        market["metadata"].update(outcome=0, expiration_value="CHANGED", settled_ts=D + 100 * HOUR)
        market["quotes"][D + HOUR] = {"bid": ".01", "ask": ".99"}
        market["quotes"][D + 100 * HOUR] = {"bid": "BAD", "ask": "BAD"}
    assert build_opportunities(data) == original


def test_future_member_insertion_and_strike_changes_do_not_change_active_rows():
    data = fixture()
    original = build_opportunities(data)["opportunities"]
    future = copy.deepcopy(data["markets"][-1])
    future["metadata"].update(
        ticker="KXHIGHAUS-26JAN10-T90",
        open_ts=D + HOUR,
        floor_strike="90",
        metadata_provenance={**source(99), "available_at": "2026-09-06T20:00:00Z"},
    )
    data["markets"].append(future)
    data["contracts"] += 1
    for future_floor in ("90", "60"):
        future["metadata"]["floor_strike"] = future_floor
        rows = build_opportunities(data)["opportunities"]
        retained = [r for r in rows if r["ticker"] != future["metadata"]["ticker"]]
        assert retained == original  # Includes all features and complete provenance seals.
        abstention = next(r for r in rows if r["ticker"] == future["metadata"]["ticker"])
        assert abstention["feature_complete"] is False
        assert "outside_market_hours" in abstention["missing_reasons"]
        assert abstention["feature_vector"][6] is None


@pytest.mark.parametrize("outside", ["future", "closed"])
def test_inactive_member_strikes_are_not_consulted_even_during_sort(outside):
    class NoInactiveStrikes(dict):
        def __getitem__(self, key):
            if key in {"strike_type", "floor_strike", "cap_strike"}:
                raise AssertionError("Inactive contract strike entered a decision-time feature")
            return super().__getitem__(key)

        def get(self, key, default=None):
            if key in {"strike_type", "floor_strike", "cap_strike"}:
                raise AssertionError("Inactive contract strike entered a decision-time feature")
            return super().get(key, default)

    data = fixture()
    meta = data["markets"][-1]["metadata"]
    if outside == "future":
        meta["open_ts"] = D + HOUR
    else:
        meta["close_ts"] = D
    data["markets"][-1]["metadata"] = NoInactiveStrikes(meta)
    rows = build_opportunities(data)["opportunities"]
    active = [r for r in rows if r["feature_complete"]]
    assert len(active) == 2
    assert {r["feature_vector"][6] for r in active} == {0.0, 1.0}
    assert all(r["feature_vector"][5] == 1.0 for r in active)


def test_entire_inactive_event_remains_an_explicit_abstention_census():
    data = fixture()
    for market in data["markets"]:
        market["metadata"]["open_ts"] = D + HOUR
    result = build_opportunities(data)
    assert result["census"]["opportunities"] == 3
    assert result["census"]["complete_features"] == 0
    assert all("outside_market_hours" in row["missing_reasons"] for row in result["opportunities"])
    assert all(row["feature_vector"][6] is None for row in result["opportunities"])


def test_missing_history_or_one_event_member_stays_in_full_opportunity_census():
    data = fixture()
    del data["markets"][0]["quotes"][D - HOUR]
    del data["markets"][1]["quotes"][D]
    result = build_opportunities(data)
    assert len(result["opportunities"]) == 3
    assert all(row["feature_vector"][4] == pytest.approx(0.8) for row in result["opportunities"])
    assert all(row["feature_vector"][5] == pytest.approx(2 / 3) for row in result["opportunities"])
    assert sum(row["feature_complete"] for row in result["opportunities"]) == 1
    assert result["census"]["excluded"] == 0


def test_unsupported_source_is_an_explicit_exclusion_not_retimed():
    data = fixture()
    for market in data["markets"]:
        market["metadata"].update(
            source_period_end=None, source_window_status="unsupported_primary_source_window"
        )
    result = build_opportunities(data)
    assert result["opportunities"] == [] and len(result["exclusions"]) == 3
    assert all(row["reason"] == "unsupported_source_window" for row in result["exclusions"])


def test_partial_source_support_cannot_shrink_the_event_panel():
    data = fixture()
    data["markets"][0]["metadata"].update(
        source_period_end=None, source_window_status="unsupported_primary_source_window"
    )
    with pytest.raises(ValueError, match="mixed source-window"):
        build_opportunities(data)


def test_attempt_targets_known_zero_rejections_unknown_missing_and_exact_unit_fee():
    result, lookup = public()
    row = result["opportunities"][0]
    labels = label_rows_before([row], lookup, D + 16 * HOUR, COSTED)[0]
    assert labels["probability_known"] and labels["probability_label"] == 1
    assert labels["net_return_known"] == [True, True]
    # YES entry.42: model fee.017052, aggregate cent-rounded debit.44 =>+.56.
    assert labels["net_return_labels"] == pytest.approx([0.56, -0.64])
    assert labels["net_return_release_ts"] == [D + 15 * HOUR] * 2
    market = lookup[row["ticker"]]
    market["quotes"][D + HOUR] = {"bid": ".45", "ask": ".47"}
    labels = label_rows_before([row], lookup, D + 2 * HOUR, COSTED)[0]
    assert labels["net_return_known"] == [True, False]
    assert labels["net_return_labels"] == [0.0, 0.0]
    assert labels["side_reasons"][0] == "known_limit_rejection"
    assert labels["net_return_release_ts"][0] == D + HOUR
    del market["quotes"][D + HOUR]
    labels = label_rows_before([row], lookup, D + 16 * HOUR, COSTED)[0]
    assert labels["probability_known"] and labels["net_return_known"] == [False, False]
    assert labels["side_reasons"] == ["unknown_missing_entry_endpoint"] * 2


def test_label_boundary_strict_and_future_outcome_not_inspected():
    result, lookup = public()
    row = result["opportunities"][0]

    class FutureOutcome(dict):
        def get(self, key, default=None):
            if key == "outcome":
                raise AssertionError("Unreleased outcome was inspected")
            return super().get(key, default)

    lookup[row["ticker"]]["metadata"] = FutureOutcome(lookup[row["ticker"]]["metadata"])
    for cutoff in (D, D + HOUR, D + 15 * HOUR):
        label = label_rows_before([row], lookup, cutoff, COSTED)[0]
        assert not label["probability_known"] and label["net_return_known"] == [False, False]


def test_masks_independent_for_side_and_probability_settlement_release():
    result, lookup = public()
    row = result["opportunities"][0]
    lookup[row["ticker"]]["metadata"]["settled_ts"] = None
    lookup[row["ticker"]]["quotes"][D + HOUR] = {"bid": ".45", "ask": ".47"}
    label = label_rows_before([row], lookup, D + 99 * HOUR, COSTED)[0]
    assert not label["probability_known"]
    assert label["net_return_known"] == [True, False]


def test_no_future_endpoint_resolution_without_exact_prediction_seal():
    result, lookup = public()
    decisions = [decision(result["opportunities"][0])]

    class NeverOpen(dict):
        def __getitem__(self, key):
            raise AssertionError("Private data opened before prediction artifact check")

    with pytest.raises(ValueError, match="prediction"):
        resolve_intents(decisions, NeverOpen(lookup), COSTED, D + 20 * HOUR, prediction_artifact={})
    bad = seal(decisions)
    decisions[0]["side"] = "no"
    with pytest.raises(ValueError, match="prediction"):
        resolve_intents(decisions, NeverOpen(lookup), COSTED, D + 20 * HOUR, prediction_artifact=bad)


def test_stress_keeps_original_limit_and_unresolved_orders_stay_present():
    result, lookup = public()
    decisions = [decision(result["opportunities"][0])]
    artifact = seal(decisions)
    normal = resolve_intents(decisions, lookup, COSTED, D + 20 * HOUR, prediction_artifact=artifact)
    stress = resolve_intents(decisions, lookup, STRESS, D + 20 * HOUR, prediction_artifact=artifact)
    assert normal["orders"][0]["limit_price"] == stress["orders"][0]["limit_price"] == "0.43"
    assert normal["statuses"][0]["reason"] == "settled_conditional_fill"
    assert stress["statuses"][0]["reason"] == "known_limit_rejection"
    early = resolve_intents(decisions, lookup, COSTED, D + 2 * HOUR, prediction_artifact=artifact)
    order = early["orders"][0]
    assert order["entry_price"] == "0.42" and order["exit_ts"] is None
    assert order["terminal_cash_per_contract"] is None and order["available_quantity"] is None
    assert order["signal_provenance"] == normal["orders"][0]["signal_provenance"]
    del lookup[decisions[0]["opportunity"]["ticker"]]["quotes"][D + HOUR]
    missing = resolve_intents(decisions, lookup, COSTED, D + 20 * HOUR, prediction_artifact=artifact)
    assert len(missing["orders"]) == 1 and missing["orders"][0]["entry_price"] is None


def test_resolver_output_runs_original_cash_engine_without_depth_invention():
    result, lookup = public()
    decisions = [decision(result["opportunities"][0])]
    orders = resolve_intents(decisions, lookup, COSTED, D + 20 * HOUR, prediction_artifact=seal(decisions))[
        "orders"
    ]
    config = {
        "initial_cash": "200",
        "risk_fraction": ".01",
        "max_event_fraction": ".05",
        "max_cluster_fraction": ".10",
        "max_total_fraction": ".25",
        "entry_coefficient": ".07",
        "exit_coefficient": ".07",
        "conditional_depth_cap": 100,
        "mode": "hypothetical",
    }
    account = replay(orders, config, D, D + 24 * HOUR)
    assert account["cash"] == 202.25 and account["unresolved_holdings"] == []
    config["mode"] = "verified"
    verified = replay(orders, config, D, D + 24 * HOUR)
    assert verified["cash"] == 200 and verified["accepted_trades"] == []


def test_modified_opportunity_or_cross_scenario_limit_is_rejected():
    result, lookup = public()
    row = copy.deepcopy(result["opportunities"][0])
    row["yes_ask"] = ".01"
    with pytest.raises(ValueError, match="opportunity"):
        label_rows_before([row], lookup, D + 20 * HOUR, COSTED)
    decisions = [decision(result["opportunities"][0])]
    decisions[0]["limit_price"] = ".44"
    with pytest.raises(ValueError, match="limit changed"):
        resolve_intents(decisions, lookup, STRESS, D + 20 * HOUR, prediction_artifact=seal(decisions))
