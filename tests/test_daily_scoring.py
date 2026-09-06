from collections import defaultdict
from datetime import UTC, datetime

import pytest

from research.experiments.e002_baselines import build_quotes, event_weights
from weatherpred.archive import Archive, canonical


def test_daily_weights_do_not_count_extra_contracts_or_cities_as_extra_days():
    rows = [
        *({"day": "2025-07-01", "event": "cityA"} for _ in range(6)),
        *({"day": "2025-07-01", "event": "cityB"} for _ in range(2)),
        {"day": "2025-07-02", "event": "cityA"},
    ]
    weights = event_weights(rows)
    daily, events = defaultdict(float), defaultdict(float)
    for r, w in zip(rows, weights, strict=True):
        daily[r["day"]] += w
        events[(r["day"], r["event"])] += w
    assert dict(daily) == pytest.approx({"2025-07-01": 1, "2025-07-02": 1})
    assert events[("2025-07-01", "cityA")] == pytest.approx(0.5)
    assert events[("2025-07-01", "cityB")] == pytest.approx(0.5)


def test_daily_dataset_rejects_late_training_label_and_future_candle(tmp_path):
    ar = Archive(tmp_path)
    now = datetime(2026, 9, 6, tzinfo=UTC)
    source_id = ar.append(
        "history",
        "test",
        now,
        {},
        canonical(
            {
                "markets": [
                    {
                        "ticker": "train",
                        "event_ticker": "train-event",
                        "result": "yes",
                        "settlement_ts": "2025-07-01T00:00:00Z",
                    },
                    {
                        "ticker": "val",
                        "event_ticker": "val-event",
                        "result": "no",
                        "settlement_ts": "2025-07-03T00:00:00Z",
                    },
                ]
            }
        ).encode(),
    )
    markets, acquisition = [], {"rows": []}
    for ticker, split, day, outcome in (
        ("train", "train", "2025-06-30", 1),
        ("val", "validation", "2025-07-02", 0),
    ):
        decision = 1751414400  # July 2, 2025 00 UTC, safely after fit cutoff.
        m = {
            "ticker": ticker,
            "event": ticker + "-event",
            "series": "test",
            "day": day,
            "split": split,
            "source_record_id": source_id,
            "outcome": outcome,
            "decision_ts": {"3": decision},
            "open_ts": decision - 3600,
            "close_ts": decision + 1,
            "predicate_provenance": "fixture",
            "strike_type": "greater",
        }
        markets.append(m)
        # Only a quote AFTER the decision. It must never get backfilled.
        candle_id = ar.append(
            "candle",
            ticker,
            now,
            {},
            canonical(
                {
                    "ticker": ticker,
                    "candlesticks": [
                        {
                            "end_period_ts": decision + 60,
                            "yes_bid": {"close_dollars": ".4"},
                            "yes_ask": {"close_dollars": ".6"},
                        }
                    ],
                }
            ).encode(),
        )
        acquisition["rows"].append({"ticker": ticker, "record_id": candle_id})
    protocol = {
        "development_start": "2025-01-01",
        "validation_end_exclusive": "2025-10-01",
        "decision_hours_before_period_end": [3],
    }
    quotes, statuses = build_quotes(
        ar, markets, acquisition, protocol, {"fit_cutoff": "2025-07-01T00:00:00Z"}
    )
    assert quotes == []
    assert [s["status"] for s in statuses] == [
        "training_label_not_settled_before_fit",
        "missing_exact_two_sided_quote",
    ]
    with pytest.raises(ValueError, match="Sealed"):
        build_quotes(
            ar, [{"day": "2025-10-01"}], acquisition, protocol, {"fit_cutoff": "2025-07-01T00:00:00Z"}
        )
    ar.close()
