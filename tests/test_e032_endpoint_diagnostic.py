import copy
from datetime import UTC, datetime

import pytest

from research.probes.e032_endpoint_diagnostic import (
    checked_row,
    classify_endpoint,
    inspect_case,
    read_record,
    sha,
    summarize,
)
from weatherpred.archive import Archive, canonical


@pytest.mark.parametrize(
    "quote,side,category,identifiable,comparison",
    [
        (None, "yes", "absent_row", False, "unknown"),
        ({"bid": ".2", "ask": None}, "yes", "required_side_null", False, "unknown"),
        ({"bid": None, "ask": ".3"}, "no", "required_side_null", False, "unknown"),
        ({"bid": None, "ask": ".3"}, "yes", "other_side_null", True, "interior_price_within_original_limit"),
        (
            {"bid": "0", "ask": ".3"},
            "yes",
            "other_side_boundary",
            True,
            "interior_price_within_original_limit",
        ),
        ({"bid": ".8", "ask": None}, "no", "other_side_null", True, "interior_price_within_original_limit"),
        (
            {"bid": ".8", "ask": "1"},
            "no",
            "other_side_boundary",
            True,
            "interior_price_within_original_limit",
        ),
        ({"bid": ".3", "ask": ".3"}, "yes", "locked", True, "interior_price_within_original_limit"),
        ({"bid": ".4", "ask": ".3"}, "yes", "crossed", True, "interior_price_within_original_limit"),
        (
            {"bid": "0", "ask": ".8"},
            "yes",
            "other_side_boundary",
            True,
            "interior_price_above_original_limit",
        ),
        ({"bid": ".3", "ask": None}, "no", "other_side_null", True, "interior_price_above_original_limit"),
        (
            {"bid": ".2", "ask": "1"},
            "yes",
            "required_side_boundary",
            True,
            "base_price_boundary_no_fill_inference",
        ),
        (
            {"bid": "1", "ask": ".8"},
            "no",
            "required_side_boundary",
            True,
            "base_price_boundary_no_fill_inference",
        ),
        (
            {"bid": "0", "ask": "0"},
            "yes",
            "required_side_boundary",
            True,
            "base_price_boundary_no_fill_inference",
        ),
        (
            {"bid": "NaN", "ask": ".3"},
            "yes",
            "other_side_invalid",
            True,
            "interior_price_within_original_limit",
        ),
        ({"bid": ".2", "ask": "NaN"}, "yes", "required_side_invalid", False, "unknown"),
        ({"bid": ".2", "ask": "2"}, "yes", "required_side_invalid", False, "unknown"),
        ({"bid": ".2", "ask": True}, "yes", "required_side_invalid", False, "unknown"),
        ({"bid": ".2", "ask": ".3"}, "yes", "strict_two_sided", True, "interior_price_within_original_limit"),
    ],
)
def test_complete_endpoint_categories(quote, side, category, identifiable, comparison):
    result = classify_endpoint(quote, side, ".01", ".4")
    assert result["category"] == category
    assert result["chosen_price_identifiable"] is identifiable
    assert result["chosen_price_comparison"] == comparison
    assert result["old_two_sided_valid"] is (category == "strict_two_sided")
    assert "pnl" not in result and "fill" not in result


def test_locked_and_boundary_flags_are_preserved_together():
    result = classify_endpoint({"bid": "0", "ask": "0"}, "yes", ".01", ".4")
    assert result["flags"] == ["required_side_boundary", "other_side_boundary", "locked"]
    assert result["interior_chosen_price"] is False


def case_fixture():
    decision_ts = int(datetime(2026, 4, 1, 18, tzinfo=UTC).timestamp())
    ticker = "KXHIGHAUS-26APR01-T80"
    meta = {
        "ticker": ticker,
        "event": "KXHIGHAUS-26APR01",
        "day": "2026-04-01",
        "series": "KXHIGHAUS",
        "open_ts": decision_ts - 86400,
        "close_ts": decision_ts + 7200,
        "source_period_end": datetime.fromtimestamp(decision_ts + 43200, UTC).isoformat(),
    }
    opportunity = {
        **meta,
        "opportunity_id": f"{ticker}:{decision_ts}",
        "decision_ts": decision_ts,
        "yes_ask": ".4",
        "no_ask": ".7",
    }
    opportunity["opportunity_sha256"] = sha(opportunity)
    decision = {
        "opportunity": opportunity,
        "opportunity_id": opportunity["opportunity_id"],
        "side": "yes",
        "policy_id": "ridge_net_return",
        "limit_price": ".41",
    }
    decision["decision_sha256"] = sha(decision)
    pin = {"id": 20, "body_sha256": "a" * 64}
    trade = f"ridge_net_return:{opportunity['opportunity_id']}:yes"
    order = {
        "trade_id": trade,
        "policy_id": "ridge_net_return",
        "event": meta["event"],
        "side": "yes",
        "decision_ts": decision_ts,
        "entry_ts": decision_ts + 3600,
        "prediction_record_id": 20,
        "prediction_body_sha256": "a" * 64,
        "limit_price": ".41",
    }
    status = {
        "trade_id": trade,
        "opportunity_id": opportunity["opportunity_id"],
        "reason": "unknown_missing_entry_endpoint",
    }
    market = {
        "metadata": meta,
        "quotes": {str(decision_ts + 3600): {"bid": "0", "ask": ".7"}},
        "quote_provenance": {str(decision_ts + 3600): {"source_record_id": 1}},
    }
    scenario = {"name": "costed", "entry_delay_hours": 1, "slippage": ".01"}
    return decision, order, status, market, scenario, pin


def test_independent_unknown_gate_and_source_check_without_outcome_access():
    decision, order, status, market, scenario, pin = case_fixture()

    class Private(dict):
        def get(self, key, default=None):
            if key in ("outcome", "settled_ts", "expiration_value"):
                raise AssertionError("Diagnostic consulted an outcome")
            return super().get(key, default)

        def __getitem__(self, key):
            if key in ("outcome", "settled_ts", "expiration_value"):
                raise AssertionError("Diagnostic consulted an outcome")
            return super().__getitem__(key)

    market["metadata"] = Private(market["metadata"])
    seen = []
    result = inspect_case(decision, order, status, market, "ridge_net_return", scenario, pin, seen.append)
    assert seen == [{"source_record_id": 1}]
    assert result["original_unknown_with_identifiable_interior_ask"] is True
    assert result["chosen_price_comparison"] == "interior_price_above_original_limit"
    counts = summarize([result])["ridge_net_return:costed"]
    assert counts["unknown_with_identifiable_interior_ask"] == 1
    assert counts["categories_original_unknown"] == {"other_side_boundary": 1}
    status["reason"] = "known_limit_rejection"
    with pytest.raises(ValueError, match="missingness"):
        inspect_case(decision, order, status, market, "ridge_net_return", scenario, pin, seen.append)


def test_provenance_is_verified_before_endpoint_fields_are_read():
    decision, order, status, market, scenario, pin = case_fixture()

    class Poison(dict):
        def get(self, key, default=None):
            raise AssertionError("Endpoint inspected before provenance")

    market["quotes"][str(order["entry_ts"])] = Poison()

    def reject(_source):
        raise ValueError("Source mismatch")

    with pytest.raises(ValueError, match="Source mismatch"):
        inspect_case(decision, order, status, market, "ridge_net_return", scenario, pin, reject)


@pytest.mark.parametrize("change", ["limit", "timestamp", "prediction", "seal"])
def test_tampered_original_binding_fails_before_quote_read(change):
    decision, order, status, market, scenario, pin = case_fixture()
    if change == "limit":
        order["limit_price"] = ".8"
    elif change == "timestamp":
        order["entry_ts"] += 3600
    elif change == "prediction":
        order["prediction_record_id"] += 1
    else:
        decision["opportunity"]["yes_ask"] = ".8"
    market["quotes"] = None
    with pytest.raises(ValueError):
        inspect_case(decision, order, status, market, "ridge_net_return", scenario, pin, lambda _: None)


def test_archive_body_and_record_identity_tampering_are_rejected(tmp_path):
    archive = Archive(tmp_path)
    try:
        identifier = archive.append("synthetic", "test", datetime.now(UTC), {}, canonical({"a": 1}).encode())
        row = checked_row(archive, identifier)
        assert read_record(archive, row) == {"a": 1}
        wrong = copy.deepcopy(dict(row))
        wrong["body_sha256"] = "a" * 64
        with pytest.raises(ValueError, match="identity"):
            checked_row(archive, identifier, wrong)
        (archive.root / "blobs" / row["body_sha256"]).write_bytes(b"changed")
        with pytest.raises(ValueError, match="body changed"):
            read_record(archive, row)
    finally:
        archive.close()
