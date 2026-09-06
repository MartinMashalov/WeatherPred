from copy import deepcopy

import pytest

from research.probes.observation_reaction import (
    classify_versions,
    one_contract_quote,
    reaction,
    select_quote,
)


def version(identifier, hour, receipt_minute, temperature, *, raw=None, backfill=False):
    return {
        "id": identifier,
        "station": "KMIA",
        "observation_at": f"2026-09-06T{hour:02}:00:00+00:00",
        "first_received_at": f"2026-09-06T{hour:02}:{receipt_minute:02}:00+00:00",
        "provider_receipt_at": f"2026-09-06T{hour:02}:01:00+00:00",
        "is_initial_backfill": backfill,
        "report": {"temp": temperature, "rawOb": raw or f"KMIA {hour:02}00 report"},
    }


def test_only_raw_or_temperature_changes_create_news_and_backfill_stays_excluded():
    rows = [version(1, 9, 2, 30, backfill=True), version(2, 10, 2, 31), version(3, 10, 3, 31)]
    rows.append(version(4, 10, 4, 31, raw="KMIA 1000 corrected cloud report"))
    result = classify_versions(rows, "2026-09-06T09:30:00+00:00")
    assert [r["classification"] for r in result] == [
        "initial_or_old_provider_backfill",
        "new_weather_report",
        "metadata_only_copy",
        "new_weather_report",
    ]
    assert result[1]["temperature_change_c"] == 1
    assert result[3]["temperature_change_c"] == 0
    assert result[1]["provider_to_own_receipt_seconds"] == 60
    changed = deepcopy(rows)
    changed.append(version(5, 11, 2, 99))
    assert classify_versions(changed, "2026-09-06T09:30:00+00:00")[:4] == result


def test_one_contract_prices_walk_real_depth_and_reject_missing_or_crossed_books():
    item = {"orderbook_fp": {"yes_dollars": [[".39", ".5"], [".4", ".5"]], "no_dollars": [[".5", "2"]]}}
    assert one_contract_quote(item) == {
        "yes_bid": "0.395",
        "yes_ask": "0.5",
        "no_bid": "0.5",
        "no_ask": "0.605",
    }
    item["orderbook_fp"]["yes_dollars"] = [[".4", ".99"]]
    with pytest.raises(ValueError, match="insufficient"):
        one_contract_quote(item)
    item["orderbook_fp"]["yes_dollars"] = [[".6", "2"]]
    with pytest.raises(ValueError, match="crossed"):
        one_contract_quote(item)


def snapshot(identifier, requested, received, *, error=None):
    result = {"source_record_id": identifier, "requested_ts": requested, "received_ts": received}
    if error:
        result["error"] = error
    else:
        result["quote"] = {"yes_bid": ".3", "yes_ask": ".35", "no_bid": ".65", "no_ask": ".7"}
    return result


def test_window_selection_rejects_prefetched_future_quote_and_does_not_skip_shallow_first_book():
    snapshots = [
        snapshot(1, 99, 103),
        snapshot(2, 101, 104, error="insufficient_one_contract_depth"),
        snapshot(3, 102, 105),
    ]
    result = select_quote(snapshots, 100, 75, 500)
    assert result["source_record_id"] == 2
    assert result["status"] == "insufficient_one_contract_depth"
    assert select_quote(snapshots, 110, 75, 500)["status"] == "no_eligible_quote"
    assert select_quote(snapshots, 500, 75, 500)["status"] == "market_closed_at_window"
    assert select_quote([snapshot(0, 1, 2)], 100, 75, 500, before=True)["status"] == "no_eligible_quote"


def test_signed_bid_ask_changes_are_not_directionally_mislabeled_for_temperature_brackets():
    before, arrival, later = snapshot(1, 98, 99), snapshot(2, 101, 102), snapshot(3, 160, 161)
    for row in (before, arrival, later):
        row["status"] = "quoted"
    arrival["quote"] = {"yes_bid": ".31", "yes_ask": ".36", "no_bid": ".64", "no_ask": ".69"}
    later["quote"] = {"yes_bid": ".38", "yes_ask": ".4", "no_bid": ".6", "no_ask": ".62"}
    result = reaction(before, arrival, later, 100, 60, -2, False)
    assert result["changes"] == {"yes_bid": 0.08, "yes_ask": 0.05}
    assert result["gross_cross_spread_difference"] == {"yes": 0.02, "no": -0.09}
    assert "temperature_aligned_changes" not in result
    monotone = reaction(before, arrival, later, 100, 60, -2, True)
    assert monotone["temperature_aligned_changes"] == {"yes_bid": -0.08, "yes_ask": -0.05}
    late_arrival = {**arrival, "received_ts": 160}
    assert (
        reaction(before, late_arrival, later, 100, 60, -2, False)["cross_spread_status"]
        == "arrival_at_or_after_horizon"
    )
