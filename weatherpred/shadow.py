"""Prospective forecast logging with pinned models and zero real-money orders.

The only client used here exposes public GET requests. A recorded expected value
is a model diagnostic; no confidence bound or executable profit is implied.
"""

import fcntl
import hashlib
import json
import logging
import time
from datetime import timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from weatherpred.archive import canonical
from weatherpred.basket import resolve_current_fee
from weatherpred.books import parse_book, purchase_cost
from weatherpred.forecasts import IndexSeries, ResidualDistribution
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def slots_after(start, settlement_hours):
    """Choose future scheduled decisions, never backdate missed slots."""
    hour = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    slots = []
    for offset in range(settlement_hours):
        settlement = hour + timedelta(hours=offset)
        for horizon in (30, 15, 5):
            decision = settlement - timedelta(minutes=horizon)
            if decision > start:
                slots.append((decision, settlement, horizon))
    return slots


def validate_lineage(archive, record_ids, decision, settlement, model_record_id):
    if decision >= settlement:
        raise ValueError("Forecast publication must precede settlement")
    for rec in [model_record_id, *record_ids]:
        row = archive.db.execute("SELECT available_at FROM records WHERE id=?", (rec,)).fetchone()
        if row is None or parse_time(row["available_at"]) > decision:
            raise ValueError("Information or model was not available by decision time")


def forecast_snapshot(client, model_row, scheduled, settlement, horizon):
    archive = client.archive
    model = archive.json(model_row)
    evidence = []
    # Resolve the target using actual close_time, not a guessed timezone ticker.
    markets = []
    for page, rec in client.pages(
        "/markets",
        "markets",
        {"series_ticker": "KXTEMPMIAH", "status": "open", "limit": 1000},
        kind="shadow_markets",
        key="KXTEMPMIAH",
    ):
        evidence.append(rec)
        markets.extend(m for m in page if parse_time(m["close_time"]) == settlement)
    if not markets or len({m["event_ticker"] for m in markets}) != 1:
        raise ValueError("Missing or ambiguous target event")
    if any(m["status"] != "active" or m["strike_type"] != "greater" for m in markets):
        raise ValueError("Unexpected market status or strike predicate")
    event = markets[0]["event_ticker"]
    series, rec = client.json("/series/KXTEMPMIAH", kind="shadow_series", key="KXTEMPMIAH")
    evidence.append(rec)
    changes = []
    for page, rec in client.pages(
        "/events/fee_changes",
        "event_fee_changes",
        {"event_ticker": event, "limit": 1000},
        kind="shadow_fees",
        key=event,
    ):
        changes.extend(page)
        evidence.append(rec)
    _, rec = client.json("/live_data/weather/miami/calibrations", kind="shadow_calibrations", key="miami")
    evidence.append(rec)
    index, rec = client.json(
        "/live_data/weather/miami", {"last_sec": 4200, "detailed": "true"}, kind="shadow_index", key="miami"
    )
    if index["city"] != "miami" or index["units"] != "fahrenheit":
        raise ValueError("Unexpected index source or units")
    evidence.append(rec)
    books, book_id = client.json(
        "/markets/orderbooks", [("tickers", m["ticker"]) for m in markets], kind="shadow_books", key=event
    )
    evidence.append(book_id)
    snapshot_at = utcnow()
    if not 0 <= (snapshot_at - scheduled).total_seconds() <= 15:
        raise ValueError("Scheduled snapshot missed its 15-second collection window")
    if any(parse_time(m["open_time"]) > snapshot_at for m in markets):
        raise ValueError("Market not yet open")
    raw_book = archive.db.execute("SELECT * FROM records WHERE id=?", (book_id,)).fetchone()
    metadata = json.loads(raw_book["metadata"])
    headers = metadata["headers"]
    server_date = parsedate_to_datetime(headers["date"]) if "date" in headers else None
    if (
        server_date is None
        or max(float(headers.get("age", 0)), (snapshot_at - server_date).total_seconds()) > 5
    ):
        raise ValueError("Stale or undated quote response")
    if (snapshot_at - parse_time(metadata["request_started_at"])).total_seconds() > 5:
        raise ValueError("Slow orderbook response")
    book_map = {b["ticker"]: parse_book(b) for b in books["orderbooks"]}
    if set(book_map) != {m["ticker"] for m in markets} or len(books["orderbooks"]) != len(markets):
        raise ValueError("Missing or extra orderbook")
    protocol = model["protocol"]
    # Keep the same ten-minute feature lag used to fit this frozen benchmark.
    # The actual response receipt time, separately, must precede publication.
    feature = IndexSeries(index["timeseries"]).features(
        int(snapshot_at.timestamp() * 1000),
        int(settlement.timestamp() * 1000),
        protocol["historical_publication_lag_minutes"] * 60_000,
        protocol["max_last_point_age_minutes"] * 60_000,
        protocol["trend_lookback_minutes"] * 60_000,
    )
    if feature is None:
        raise ValueError("Insufficient eligible feature history")
    schedule = resolve_current_fee(series["series"], changes, snapshot_at)
    rows = []
    for m in sorted(markets, key=lambda m: m["floor_strike"]):
        probabilities = {}
        for name in protocol["models"]:
            fit = model["models"][f"{name}:{horizon}"]
            distribution = ResidualDistribution(tuple(fit["residuals"]), fit["kind"])
            probabilities[name] = distribution.probability(feature[fit["base"]], m)
        costs = {}
        for side in ("yes", "no"):
            cost = purchase_cost(book_map[m["ticker"]], side, 1, schedule, "0.5", "0.01")
            costs[side] = (
                None
                if cost is None
                else {
                    "quantity": "1",
                    "principal_usd": str(cost.principal),
                    "fees_usd": str(cost.fees),
                    "total_usd": str(cost.total),
                    "model_expected_value_usd": {
                        name: float((Decimal(str(p)) if side == "yes" else 1 - Decimal(str(p))) - cost.total)
                        for name, p in probabilities.items()
                    },
                }
            )
        rows.append(
            {
                "ticker": m["ticker"],
                "floor_strike": m["floor_strike"],
                "probabilities": probabilities,
                "displayed_cost_scenarios": costs,
                "intended_trade": "abstain",
                "intended_quantity": 0,
                "confidence_bound": None,
                "hypothetical_execution": "no order; no fill",
            }
        )
    publication = utcnow()
    if (publication - scheduled).total_seconds() > 15:
        raise ValueError("Forecast computation missed its publication window")
    validate_lineage(archive, evidence, publication, settlement, model_row["id"])
    result = {
        "protocol": "E004-forward-v1",
        "model_record_id": model_row["id"],
        "scheduled_at": iso(scheduled),
        "snapshot_at": iso(snapshot_at),
        "published_at": iso(publication),
        "settlement_at": iso(settlement),
        "horizon_minutes": horizon,
        "event": event,
        "evidence_record_ids": evidence,
        "features": feature,
        "markets": rows,
        "real_money_recommended_size": 0,
        "fills": 0,
        "abstention_reason": "No validated positive edge or reliable uncertainty lower bound",
        "limitations": [
            "Exploratory frozen model; no profitability evidence",
            "Displayed cost after half depth and one cent slippage is not a fill",
            "Up to 15 seconds of collection/computation delay is explicitly recorded",
        ],
    }
    rec = archive.append(
        "shadow_forecast", event + ":" + str(horizon), publication, {}, canonical(result).encode()
    )
    # Actual persistence must also be before outcome, not just a precomputed time.
    if utcnow() >= settlement:
        archive.append("shadow_invalidated", str(rec), utcnow(), {}, b"Persisted after settlement")
        raise ValueError("Forecast persistence crossed settlement")
    return {
        "record_id": rec,
        "event": event,
        "horizon_minutes": horizon,
        "contracts": len(rows),
        "published_at": iso(publication),
        "real_money_recommended_size": 0,
    }


def run_shadow(client, settlement_hours=4, model_record_id=None):
    if not 1 <= settlement_hours <= 24:
        raise ValueError("A bounded run of 1 through 24 settlement hours is required")
    root = Path(client.archive.root)
    lock = (root / "shadow.lock").open("a+")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another shadow forecast process holds the lock") from None
    reason = "error"
    try:
        model_row = (
            client.archive.db.execute(
                "SELECT * FROM records WHERE id=? AND kind='model_artifact'", (model_record_id,)
            ).fetchone()
            if model_record_id
            else client.archive.latest("model_artifact", "E004-v1")
        )
        if model_row is None:
            raise ValueError("No fitted model artifact")
        model = client.archive.json(model_row)
        for path in ("weatherpred/forecasts.py", "weatherpred/index.py"):
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != model["code_sha256"][path]:
                raise ValueError("Forecast implementation differs from pinned model artifact")
        slots = slots_after(utcnow(), settlement_hours)
        registration = {
            "model_record_id": model_row["id"],
            "slots": [
                {"decision_at": iso(d), "settlement_at": iso(s), "horizon_minutes": h} for d, s, h in slots
            ],
            "maximum_lateness_seconds": 15,
            "size": 0,
            "purpose": "Prospective baseline probabilities and actual receipt lineage; no model promotion",
            "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        }
        client.archive.append(
            "experiment_protocol", "E004-forward-v1", utcnow(), {}, canonical(registration).encode()
        )
        LOG.info(
            "Pinned model=%s; %s future decision slots; first=%s",
            model_row["id"],
            len(slots),
            iso(slots[0][0]) if slots else "none",
        )
        for scheduled, settlement, horizon in slots:
            while utcnow() < scheduled:
                if (root / "STOP_SHADOW").exists():
                    reason = "stop_file"
                    return {"stop_reason": reason}
                time.sleep(min(1, max(0, (scheduled - utcnow()).total_seconds())))
            if (root / "STOP_SHADOW").exists():
                reason = "stop_file"
                return {"stop_reason": reason}
            try:
                result = forecast_snapshot(client, model_row, scheduled, settlement, horizon)
                LOG.info("Prospective forecast %s", canonical(result))
                Path("reports/shadow_status.json").write_text(json.dumps(result, indent=2))
            except (httpx.HTTPError, ValueError, KeyError) as exc:
                result = {"scheduled_at": iso(scheduled), "settlement_at": iso(settlement), "error": str(exc)}
                client.archive.append("shadow_skip", "E004", utcnow(), {}, canonical(result).encode())
                LOG.warning("Skipped scheduled forecast: %s", canonical(result))
        reason = "slot_limit"
        return {"stop_reason": reason}
    finally:
        client.archive.append("shadow_stopped", "E004", utcnow(), {}, canonical({"reason": reason}).encode())
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()
