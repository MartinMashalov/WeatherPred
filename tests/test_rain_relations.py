from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from research.experiments.e019_rain_pairs import submit_pair
from weatherpred.archive import Archive
from weatherpred.fees import FeeSchedule
from weatherpred.paper import PaperLedger, taker_slices
from weatherpred.rain_relations import calendar_rule, confirmed_dry, payout_bounds, plan_pair, relation

T = datetime(2026, 9, 6, 17, tzinfo=UTC)
FEE = FeeSchedule("quadratic", D(1))
SCENARIO = {
    "name": "taker_5s_half",
    "latency_seconds": 5,
    "leg_stagger_seconds": 2,
    "depth_retained": "0.5",
    "slippage": "0.01",
}


def market(ticker, dates):
    return {
        "ticker": ticker,
        "event_ticker": ticker.rsplit("-", 1)[0],
        "market_type": "binary",
        "notional_value_dollars": "1.0000",
        "strike_type": "greater",
        "floor_strike": 0,
        "rules_primary": f"If the total precipitation at CLINYC in New York City {dates} is strictly greater than 0 inches, then the market resolves to Yes.",
        "rules_secondary": "Weather Company https://weather.com/kalshi Trace and missing daily precipitation values are counted as 0 inches.",
        "close_time": "2026-09-07T05:00:00Z",
        "status": "active",
        "price_ranges": [{"start": "0", "end": "1", "step": "0.01"}],
    }


def members():
    return [
        market("KXRAIN-26SEP05-NYC", "in Sep 5, 2026"),
        market("KXRAIN-26SEP06-NYC", "in Sep 6, 2026"),
        market("KXRAINWKND-26SEP05-NYC", "on any day within September 5, 2026 through September 6, 2026"),
    ]


def test_exact_calendar_identity_rejects_wrong_station_period_and_convention():
    a, b, w = members()
    assert relation(a, b, w)["start"] == "2026-09-05"
    for field, replacement in (
        ("rules_primary", w["rules_primary"].replace("CLINYC", "CLILGA")),
        ("rules_primary", w["rules_primary"].replace("September 6", "September 7")),
        ("rules_secondary", w["rules_secondary"] + " Use corrected values."),
        ("floor_strike", 0.01),
    ):
        with pytest.raises(ValueError):
            relation(a, b, {**w, field: replacement})
    assert calendar_rule({**b, "close_time": "2026-09-07T04:00:00Z"}) == calendar_rule(b)


def test_dry_saturday_requires_released_final_label_and_exact_official_station():
    a, _, _ = members()
    a.update(status="finalized", result="no", expiration_value="0.00", settlement_ts="2026-09-06T12:00:00Z")
    table = {
        "date": "2026-09-05",
        "results": [
            {
                "station": {"cliId": "NYC", "icao": "KNYC"},
                "status": "official",
                "data": {"isOfficial": True, "reportDate": "2026-09-05", "precipitation": 0},
            }
        ],
    }
    assert confirmed_dry(a, table, T) == "KNYC"
    with pytest.raises(ValueError):
        confirmed_dry(a, table, T - timedelta(hours=6))
    for field, value in (
        ("precipitation", None),
        ("precipitation", 0.01),
        ("isOfficial", False),
        ("reportDate", "2026-09-04"),
    ):
        bad = deepcopy(table)
        bad["results"][0]["data"][field] = value
        with pytest.raises(ValueError):
            confirmed_dry(a, bad, T)
    bad = deepcopy(table)
    bad["results"][0]["station"]["cliId"] = "LGA"
    with pytest.raises(ValueError):
        confirmed_dry(a, bad, T)


def test_costed_pair_rejects_mirage_and_limits_quantity_to_retained_depth():
    daily = {"yes": [(D(".08"), D(20))], "no": [(D(".90"), D(20))]}
    weekend = {"yes": [(D(".19"), D(3))], "no": [(D(".80"), D(20))]}
    plan = plan_pair([daily, weekend], [FEE, FEE], SCENARIO)
    assert plan["sides"] == ["yes", "no"]
    assert plan["quantity"] == "1"
    assert D(plan["decision_cost"]) == D(".9473")
    assert D(plan["surplus_after_source_allowance"]) == D(".0427")
    # A one-cent gross gap is smaller than fees plus the source allowance.
    bad = {**weekend, "yes": [(D(".11"), D(20))]}
    assert plan_pair([daily, bad], [FEE, FEE], SCENARIO) is None
    assert plan_pair([daily, weekend], [FEE, FEE], SCENARIO, budget=".50") is None


def test_independent_leg_failure_is_a_real_open_loss_not_a_matched_profit(tmp_path):
    archive = Archive(tmp_path)
    paper = PaperLedger(archive, "rain-test")
    paper.emit("account", {"account": SCENARIO["name"], "initial_cash": "100"}, T)
    _, b, w = members()
    member = {"city": "NYC", "markets": [b, w], "series_terms": [{}, {}]}
    plan = {"sides": ["yes", "no"], "quantity": "2", "limits": [".11", ".82"]}
    submit_pair(paper, member, plan, SCENARIO, [FEE, FEE], T, 1, 2, submitted_at=T)
    orders = list(paper.state["orders"].values())
    assert len(orders) == 2
    assert parse_due(orders[1]) - parse_due(orders[0]) == timedelta(seconds=2)
    # Later daily liquidity fills only 0.50 contract. Weekend ask moved above
    # the immutable .82 limit, so its independent IOC has no fills.
    arrived = parse_due(orders[1]) + timedelta(seconds=1)
    later = [{"yes": [], "no": [(D(".90"), D(1))]}, {"yes": [(D(".15"), D(20))], "no": []}]
    for i, order in enumerate(orders):
        paper.emit(
            "arrival", {"order_id": order["id"], "queue_ahead": "0", "book_record_id": 10 + i}, arrived
        )
        fills = taker_slices(later[i], order["side"], 2, order["limit"], "0.5", ".01")
        if i == 1:
            assert fills == []
        for f in fills:
            paper.emit(
                "fill",
                {
                    **f,
                    "order_id": order["id"],
                    "fill_id": "F",
                    "evidence_at": arrived.isoformat(),
                    "source_record_id": 10 + i,
                },
                arrived,
            )
        paper.emit("cancel", {"order_id": order["id"], "reason": "IOC"}, arrived)
    account = paper.state["accounts"][SCENARIO["name"]]
    assert D(account["cash"]) == D("99.9415")
    assert D(account["realized_pnl"]) == 0
    assert payout_bounds([".50", "0"], ["yes", "no"]) == (D(0), D(".50"), D(0))
    assert PaperLedger(archive, "rain-test").state == paper.state
    archive.close()


def parse_due(order):
    return datetime.fromisoformat(order["arrival_due_at"])


def test_two_leg_reservations_fail_before_any_intent_is_published(tmp_path):
    archive = Archive(tmp_path)
    paper = PaperLedger(archive, "reserve-test")
    paper.emit("account", {"account": SCENARIO["name"], "initial_cash": "100"}, T)
    _, b, w = members()
    with pytest.raises(ValueError, match="exposure"):
        submit_pair(
            paper,
            {"city": "NYC", "markets": [b, w], "series_terms": [{}, {}]},
            {"sides": ["yes", "no"], "quantity": "10", "limits": [".11", ".82"]},
            SCENARIO,
            [FEE, FEE],
            T,
            1,
            2,
            submitted_at=T,
        )
    assert paper.state["orders"] == {}
    assert archive.db.execute("SELECT count(*) FROM records WHERE kind='paper_event'").fetchone()[0] == 1
    archive.close()
