"""Registered, finite prospective weather/market receipts; no trading/model calls."""

import argparse
import fcntl
import hashlib
import json
import platform
import re
import shutil
import sys
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.books import parse_book
from weatherpred.timeutil import iso, parse_time, utcnow

CONFIG = Path("config/e027_prospective_capture.json")
SOURCE_PATHS = [
    "research/experiments/e027_prospective_capture.py",
    str(CONFIG),
    "tests/test_e027_prospective_capture.py",
    "weatherpred/archive.py",
    "weatherpred/timeutil.py",
    "weatherpred/books.py",
    "weatherpred/fees.py",
    "pyproject.toml",
    "uv.lock",
]


def digest(body):
    return hashlib.sha256(body).hexdigest()


def read_record(archive, row):
    body = archive.body(row)
    if digest(body) != row["body_sha256"]:
        raise ValueError("Archived bytes changed")
    return json.loads(body)


def environment():
    return {
        "python": sys.version,
        "executable": str(Path(sys.executable).resolve()),
        "platform": platform.platform(),
        "httpx": httpx.__version__,
    }


def verify_sources(protocol):
    if environment() != protocol["environment"] or str(Path.cwd().resolve()) != protocol["working_directory"]:
        raise ValueError("Registered environment/working directory changed")
    for path, expected in protocol["source_sha256"].items():
        if digest(Path(path).read_bytes()) != expected:
            raise ValueError("Registered source/input changed: " + path)


def validate_config(config):
    body = Path(config["parent_manifest"]).read_bytes()
    if digest(body) != config["parent_manifest_sha256"]:
        raise ValueError("Parent E022 station manifest changed")
    expected = sorted({c["station_id"] for c in json.loads(body)["cases"]} | {"KMDW"})
    if config["stations"] != expected or len(expected) != 21:
        raise ValueError("Expected exact20 E022 stations plus KMDW")
    if (
        config["duration_seconds"] != 86400
        or config["maximum_cycles"] != 1440
        or config["interval_seconds"] != 60
    ):
        raise ValueError("Expected a finite24h/1440slot/60second protocol")
    if not 1 <= config["maximum_contracts"] <= 300:
        raise ValueError("Contract cap exceeds declared scope")


def register(archive, config):
    validate_config(config)
    now = utcnow()
    start = now + timedelta(seconds=config["start_delay_seconds"])
    protocol = {
        "config": config,
        "registered_at": iso(now),
        "start_at": iso(start),
        "stop_at": iso(start + timedelta(seconds=config["duration_seconds"])),
        "working_directory": str(Path.cwd().resolve()),
        "environment": environment(),
        "archive_root": str(archive.root.resolve()),
        "registration_command": sys.argv,
        "run_command_template": [
            sys.executable,
            "-m",
            "research.experiments.e027_prospective_capture",
            "--run-record-id",
            "REGISTRATION_ID",
            "--archive-root",
            str(archive.root.resolve()),
        ],
        "source_sha256": {},
        "source_record_ids": {},
        "public_inputs_before_registration": 0,
    }
    for path in SOURCE_PATHS + [config["parent_manifest"]]:
        body = Path(path).read_bytes()
        protocol["source_sha256"][path] = digest(body)
        protocol["source_record_ids"][path] = archive.append("e027_source", path, now, {}, body)
    rec = archive.append("e027_protocol", config["experiment"], now, {}, canonical(protocol).encode())
    return rec, protocol


class CaptureStop(RuntimeError):
    """A hard protocol limit or operator stop, never an extended window."""


class ReceiptClient:
    def __init__(self, archive, run_id, protocol, *, transport=None):
        self.archive, self.run_id, self.protocol = archive, run_id, protocol
        self.config = protocol["config"]
        self.next_request = 0.0
        self.client = httpx.Client(
            transport=transport,
            follow_redirects=False,
            timeout=self.config["request_timeout_seconds"],
            headers={
                "User-Agent": "WeatherPred-E027/1.0 public receipt research",
                "Accept-Encoding": "identity",
            },
        )
        starts, finishes = {}, {}
        for row in archive.db.execute(
            "SELECT * FROM records WHERE kind IN ('e027_request_started','e027_http') AND key LIKE ?",
            (f"{run_id}:%",),
        ):
            meta = json.loads(row["metadata"])
            if row["kind"] == "e027_request_started":
                starts[row["id"]] = meta["reserved_bytes"]
            else:
                identity = meta["request_started_record_id"]
                if identity in finishes:
                    raise ValueError("Duplicate completed request")
                body = archive.body(row)
                if digest(body) != row["body_sha256"]:
                    raise ValueError("Archived payload changed")
                finishes[identity] = len(body)
        if not set(finishes) <= set(starts):
            raise ValueError("Response lacks a durable request intent")
        self.requests = len(starts)
        self.bytes_used = sum(finishes.get(key, reserved) for key, reserved in starts.items())

    def close(self):
        self.client.close()

    def check(self, reserve=0, *, in_flight=False):
        if utcnow() >= parse_time(self.protocol["stop_at"]):
            raise CaptureStop("registered_deadline")
        if (self.archive.root / self.config["stop_file"]).exists():
            raise CaptureStop("stop_file")
        if shutil.disk_usage(self.archive.root).free < self.config["minimum_free_disk_bytes"]:
            raise CaptureStop("minimum_free_disk")
        if not in_flight and self.requests >= self.config["maximum_requests"]:
            raise CaptureStop("request_limit")
        if self.bytes_used + reserve > self.config["maximum_payload_bytes"]:
            raise CaptureStop("payload_limit")

    def get(self, path, params, *, role, slot, catalog=False):
        url = path if path == self.config["metar_url"] else self.config["market_base"] + path
        if path != self.config["metar_url"] and path not in {
            "/series",
            "/markets",
            "/markets/orderbooks",
            "/live_data/weather/miami",
            "/live_data/weather/miami/calibrations",
        }:
            raise ValueError("Unregistered read-only public endpoint")
        cap = self.config["catalog_response_limit_bytes" if catalog else "response_limit_bytes"]
        reserve = cap + 16384
        identity = f"{self.run_id}:{slot}:{role}"
        for attempt in range(self.config["maximum_attempts"]):
            self.check(reserve)
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.check(reserve)
            self.next_request = time.monotonic() + self.config["minimum_request_interval_seconds"]
            started = utcnow()
            intent = self.archive.append(
                "e027_request_started",
                identity,
                started,
                {
                    "url": url,
                    "params": params,
                    "role": role,
                    "slot": slot,
                    "attempt": attempt,
                    "reserved_bytes": reserve,
                    "request_started_at": iso(started),
                },
                b"",
            )
            self.requests += 1
            self.bytes_used += reserve
            body, status, headers, failure = bytearray(), None, {}, None
            try:
                with self.client.stream("GET", url, params=params) as response:
                    status, headers = response.status_code, dict(response.headers)
                    length = int(headers["content-length"]) if "content-length" in headers else None
                    if length is not None and not 0 <= length <= cap:
                        raise ValueError("Response Content-Length exceeds payload limit")
                    if headers.get("content-encoding", "identity").lower() != "identity":
                        raise ValueError("Unexpected compressed response")
                    for chunk in response.iter_raw(chunk_size=16384):
                        body.extend(chunk)
                        if len(body) > cap:
                            raise ValueError("Response exceeds payload limit")
                        self.check(in_flight=True)
                    if length is not None and len(body) != length:
                        raise ValueError("Truncated response")
                    if status not in (200, 204):
                        raise ValueError(f"HTTP status {status}")
                    self.check(in_flight=True)
            except (httpx.HTTPError, ValueError, CaptureStop) as exc:
                failure = exc
            received = utcnow()
            metadata = {
                "url": url,
                "params": params,
                "role": role,
                "slot": slot,
                "request_started_record_id": intent,
                "request_started_at": iso(started),
                "actual_received_at": iso(received),
                "status": status,
                "headers": headers,
                "complete": failure is None,
                "error": None if failure is None else str(failure),
                "payload_bytes": len(body),
                "raw_sha256": digest(body),
            }
            rec = self.archive.append("e027_http", identity, received, metadata, bytes(body))
            self.bytes_used -= reserve - len(body)
            if failure is None:
                return (None if status == 204 else json.loads(body)), rec, metadata
            if isinstance(failure, CaptureStop):
                raise failure
            if not (isinstance(failure, httpx.TransportError) or status == 429 or (status or 0) >= 500):
                raise ValueError(f"{failure}; raw_record={rec}") from failure
            if attempt + 1 == self.config["maximum_attempts"]:
                raise ValueError(f"Retries exhausted: {failure}; raw_record={rec}") from failure
            self.check()
            time.sleep(2**attempt)
        raise AssertionError("Unreachable retry loop")


def remember_version(archive, run_id, kind, value, source_id, row_index, received_at, details):
    revision = digest(canonical(value).encode())
    key = f"{run_id}:{kind}:{revision}"
    existing = archive.latest("e027_first_seen", key)
    if existing:
        return existing["id"], False
    event = {
        "kind": kind,
        "revision_sha256": revision,
        "source_record_id": source_id,
        "source_row_index": row_index,
        "first_local_received_at": received_at,
        "raw_version": value,
        **details,
    }
    rec = archive.append("e027_first_seen", key, parse_time(received_at), {}, canonical(event).encode())
    return rec, True


def metar_details(report, received_at, registered_at, first_station_batch):
    local = parse_time(received_at)
    registration = parse_time(registered_at)
    errors, observation, provider, report_time = [], None, None, None
    try:
        if isinstance(report.get("obsTime"), bool):
            raise TypeError("Boolean observation time")
        observation = datetime.fromtimestamp(float(report["obsTime"]), UTC)
        if observation > local:
            errors.append("observation_after_local_receipt")
    except (ValueError, TypeError, KeyError, OverflowError):
        errors.append("invalid_observation_time")
    try:
        provider = parse_time(report["receiptTime"])
        if provider > local:
            errors.append("provider_receipt_after_local_receipt")
    except (ValueError, TypeError, KeyError, AttributeError):
        errors.append("invalid_provider_receipt_time")
    try:
        report_time = iso(report["reportTime"])
    except (ValueError, TypeError, KeyError, AttributeError):
        errors.append("invalid_or_missing_report_time")
    old_observation = observation is not None and observation < registration
    old_provider = provider is not None and provider < registration
    return {
        "station_id": report.get("icaoId"),
        "observation_at": None if observation is None else iso(observation),
        "provider_receipt_at": None if provider is None else iso(provider),
        "report_time_at": report_time,
        "report_time_raw": report.get("reportTime"),
        "timestamp_errors": errors,
        "first_successful_station_batch": first_station_batch,
        "observation_predates_registration": old_observation,
        "provider_receipt_predates_registration": old_provider,
        "is_initial_or_older_backfill": first_station_batch or old_observation or old_provider,
        "report_time_is_publication_proof": False,
    }


def capture_metar(client, slot, station_seen):
    config, archive = client.config, client.archive
    data, source, meta = client.get(
        config["metar_url"],
        {"ids": ",".join(config["stations"]), "format": "json", "hours": config["metar_history_hours"]},
        role="metar",
        slot=slot,
    )
    data = [] if data is None else data
    if not isinstance(data, list):
        raise TypeError("METAR response must be an array")
    records, new, found, errors = [], [], set(), []
    initially_seen = set(station_seen)
    for i, report in enumerate(data):
        if not isinstance(report, dict) or report.get("icaoId") not in config["stations"]:
            errors.append({"row_index": i, "reason": "unrequested_or_missing_station"})
            continue
        station = report["icaoId"]
        found.add(station)
        details = metar_details(
            report,
            meta["actual_received_at"],
            client.protocol["registered_at"],
            station not in initially_seen,
        )
        rec, created = remember_version(
            archive, client.run_id, "metar", report, source, i, meta["actual_received_at"], details
        )
        records.append({"row_index": i, "first_seen_record_id": rec})
        if created:
            new.append(rec)
    station_seen.update(found)
    return {
        "source_record_id": source,
        "report_count": len(data),
        "versions": records,
        "new_version_record_ids": new,
        "missing_stations": sorted(set(config["stations"]) - found),
        "possible_response_cap": len(data) >= 400,
        "errors": errors,
    }


def temperature_series(catalog, config):
    selected, excluded = [], []
    seen = set()
    for row in sorted(catalog, key=lambda row: str(row.get("ticker", ""))):
        ticker = row.get("ticker")
        text = " ".join(
            [
                str(row.get("title", "")),
                " ".join(row.get("tags") or []),
                str(row.get("contract_terms_url", "")),
            ]
        )
        reason = None
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9_-]{1,128}", ticker):
            reason = "unsupported_series_ticker"
        elif ticker in seen:
            reason = "duplicate_series_ticker"
        elif row.get("category") != config["category"]:
            reason = "outside_weather_category"
        elif not re.search(config["temperature_pattern"], text, re.IGNORECASE):
            reason = "unsupported_non_temperature_metadata"
        elif len(selected) >= config["maximum_temperature_series"]:
            reason = "series_cap"
        else:
            selected.append(row)
        seen.add(ticker)
        if reason:
            excluded.append({"ticker": ticker, "reason": reason})
    return selected, excluded


def select_contracts(markets, now, cap):
    selected, states, seen = [], [], set()
    for row in sorted(markets, key=lambda row: str(row.get("ticker", ""))):
        ticker, reason = row.get("ticker"), None
        if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z0-9_-]{1,128}", ticker):
            reason = "unsupported_contract_ticker"
        elif ticker in seen:
            reason = "duplicate_contract_ticker"
        else:
            try:
                if parse_time(row["close_time"]) <= now:
                    reason = "closed_by_timestamp"
                elif parse_time(row["open_time"]) > now:
                    reason = "not_yet_open"
                elif row.get("status") not in ("active", "open"):
                    reason = "unsupported_market_status"
                elif len(selected) >= cap:
                    reason = "contract_cap"
            except (ValueError, TypeError, KeyError, AttributeError):
                reason = "unsupported_market_times"
        seen.add(ticker)
        if reason is None:
            selected.append(row)
        states.append(
            {
                "ticker": ticker,
                "series_ticker": row.get("series_ticker"),
                "source_record_id": row.get("source_record_id"),
                "reason": reason,
                "rule_station_mapping_verified": False,
            }
        )
    return selected, states


def refresh_markets(client, slot):
    cfg = client.config
    data, catalog_id, _ = client.get(
        "/series",
        {"category": cfg["category"], "include_product_metadata": "true"},
        role="series_catalog",
        slot=slot,
        catalog=True,
    )
    series, exclusions = temperature_series(data["series"], cfg)
    markets, errors, pages = [], [], []
    for series_row in series:
        ticker, cursor, seen = series_row["ticker"], None, set()
        for page in range(cfg["maximum_market_pages_per_series"]):
            params = {"series_ticker": ticker, "status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            try:
                data, rec, meta = client.get("/markets", params, role=f"markets:{ticker}:{page}", slot=slot)
                pages.append(rec)
                markets.extend(
                    {
                        **m,
                        "series_ticker": ticker,
                        "source_record_id": rec,
                        "metadata_actual_received_at": meta["actual_received_at"],
                    }
                    for m in data["markets"]
                )
                cursor = data.get("cursor")
                if not cursor:
                    break
                if cursor in seen:
                    raise ValueError("Repeated market cursor")
                seen.add(cursor)
                if page + 1 == cfg["maximum_market_pages_per_series"]:
                    errors.append({"series": ticker, "reason": "market_page_cap", "remaining_cursor": cursor})
            except (ValueError, KeyError, TypeError) as exc:
                errors.append({"series": ticker, "reason": str(exc)})
                break
    selected, states = select_contracts(markets, utcnow(), cfg["maximum_contracts"])
    result = {
        "slot": slot,
        "refresh_bucket": slot // 10,
        "catalog_record_id": catalog_id,
        "series_exclusions": exclusions,
        "market_page_ids": pages,
        "market_states": states,
        "selected_markets": selected,
        "errors": errors,
        "complete": not errors,
        "created_at": iso(utcnow()),
        "selection_uses_prices": False,
    }
    rec = client.archive.append(
        "e027_market_panel", str(client.run_id), utcnow(), {}, canonical(result).encode()
    )
    return result, rec


def capture_index(client, slot):
    data, source, meta = client.get(
        "/live_data/weather/miami",
        {"last_sec": client.config["index_history_seconds"], "detailed": "true"},
        role="miami_detailed",
        slot=slot,
    )
    refs, new, errors, timestamps = [], [], [], []
    initial = (
        client.archive.db.execute(
            "SELECT 1 FROM records WHERE kind='e027_first_seen' AND key LIKE ? LIMIT 1",
            (f"{client.run_id}:miami_point:%",),
        ).fetchone()
        is None
    )
    for i, point in enumerate(data["timeseries"]):
        if not isinstance(point, dict) or not isinstance(point.get("t"), int):
            errors.append({"row_index": i, "reason": "unsupported_index_point"})
            continue
        timestamps.append(point["t"])
        stations = point.get("stations", [])
        ids = [s.get("station_id") for s in stations]
        details = {
            "point_time_ms": point.get("t"),
            "status": point.get("status"),
            "config_version": data.get("config_version"),
            "units": data.get("units"),
            "canonical_published_value_present": "v" in point
            and point.get("status") in ("normal", "degraded")
            and not point.get("receipt_basis"),
            "missing_primary_components": sorted(set(client.config["miami_primary_components"]) - set(ids)),
            "unexpected_components": sorted(
                str(s) for s in set(ids) - set(client.config["miami_primary_components"])
            ),
            "duplicate_component_ids": len(ids) != len(set(ids)),
            "administrator_receipts_are_local_receipts": False,
            "initial_or_older_backfill": initial
            or point["t"] < int(parse_time(client.protocol["registered_at"]).timestamp() * 1000),
            "point_after_local_receipt": point["t"]
            > int(parse_time(meta["actual_received_at"]).timestamp() * 1000),
        }
        value = {
            "city": data.get("city"),
            "config_version": data.get("config_version"),
            "units": data.get("units"),
            "point": point,
        }
        rec, created = remember_version(
            client.archive,
            client.run_id,
            "miami_point",
            value,
            source,
            i,
            meta["actual_received_at"],
            details,
        )
        refs.append(rec)
        if created:
            new.append(rec)
    times = sorted(set(timestamps))
    gaps = [
        {"previous_ms": a, "next_ms": b, "missing_minutes": (b - a) // 60000 - 1}
        for a, b in pairwise(times)
        if b - a > 60000
    ]
    return {
        "source_record_id": source,
        "point_versions": refs,
        "new_version_record_ids": new,
        "observed_timestamp_gaps": gaps,
        "duplicate_timestamps": len(timestamps) != len(times),
        "errors": errors,
        "missing_minutes_imputed": False,
    }


def capture_books(client, slot, panel, panel_id):
    eligible, states = select_contracts(
        panel["selected_markets"], utcnow(), client.config["maximum_contracts"]
    )
    result = {"panel_record_id": panel_id, "records": [], "contract_states": states, "errors": []}
    for offset in range(0, len(eligible), client.config["book_batch_size"]):
        batch = eligible[offset : offset + client.config["book_batch_size"]]
        requested = {m["ticker"] for m in batch}
        try:
            data, rec, _ = client.get(
                "/markets/orderbooks",
                [("tickers", m["ticker"]) for m in batch],
                role=f"books:{offset}",
                slot=slot,
            )
            seen, validation = set(), []
            for book in data["orderbooks"]:
                ticker, reason = book.get("ticker"), None
                if ticker not in requested or ticker in seen:
                    reason = "unexpected_or_duplicate_book"
                else:
                    try:
                        parsed = parse_book(book)
                        if not parsed["yes"] or not parsed["no"]:
                            reason = "observed_empty_side"
                    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                        reason = "unsupported_book:" + str(exc)
                seen.add(ticker)
                validation.append({"ticker": ticker, "reason": reason})
            result["records"].append(
                {
                    "source_record_id": rec,
                    "requested_tickers": sorted(requested),
                    "missing_tickers": sorted(requested - seen),
                    "book_states": validation,
                }
            )
        except (ValueError, KeyError, TypeError) as exc:
            result["errors"].append({"requested_tickers": sorted(requested), "reason": str(exc)})
    return result


def slot_number(protocol, now):
    return int(
        (now - parse_time(protocol["start_at"])).total_seconds() // protocol["config"]["interval_seconds"]
    )


def run(archive, run_id, report_dir):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (run_id,)).fetchone()
    if row is None or row["kind"] != "e027_protocol":
        raise ValueError("Expected an E027 registration")
    protocol = read_record(archive, row)
    if protocol["archive_root"] != str(archive.root.resolve()):
        raise ValueError("Registered archive changed")
    verify_sources(protocol)
    client = ReceiptClient(archive, run_id, protocol)
    cfg, stop, cycles_this_invocation = protocol["config"], "error", 0
    station_seen = set()
    for source in archive.db.execute(
        "SELECT * FROM records WHERE kind='e027_first_seen' AND key LIKE ?", (f"{run_id}:metar:%",)
    ):
        station_seen.add(read_record(archive, source)["station_id"])
    latest = archive.latest("e027_market_panel", str(run_id))
    panel, panel_id = (read_record(archive, latest), latest["id"]) if latest else (None, None)
    started_slots = {
        int(r["key"].split(":")[-1])
        for r in archive.db.execute(
            "SELECT key FROM records WHERE kind='e027_cycle_started' AND key LIKE ?", (f"{run_id}:%",)
        )
    }
    last_slot = max(started_slots, default=-1)
    errors_total = Counter()
    try:
        while True:
            client.check()
            now = utcnow()
            slot = slot_number(protocol, now)
            if slot >= cfg["maximum_cycles"]:
                raise CaptureStop("cycle_limit")
            if slot < 0 or slot <= last_slot:
                time.sleep(1)
                continue
            verify_sources(protocol)
            gap = list(range(last_slot + 1, slot))
            intent = {
                "slot": slot,
                "scheduled_at": iso(parse_time(protocol["start_at"]) + timedelta(seconds=slot * 60)),
                "actual_started_at": iso(now),
                "skipped_slots_since_previous": gap,
            }
            archive.append("e027_cycle_started", f"{run_id}:{slot}", now, {}, canonical(intent).encode())
            last_slot = slot
            frame = {**intent, "registration_id": run_id, "errors": []}
            for name, action in (
                ("metar", lambda slot=slot: capture_metar(client, slot, station_seen)),
                ("miami_index", lambda slot=slot: capture_index(client, slot)),
            ):
                try:
                    frame[name] = action()
                except (ValueError, KeyError, TypeError) as exc:
                    frame["errors"].append({"phase": name, "error": str(exc)})
            refresh = panel is None or panel["refresh_bucket"] != slot // 10
            if refresh:
                try:
                    panel, panel_id = refresh_markets(client, slot)
                except (ValueError, KeyError, TypeError) as exc:
                    frame["errors"].append({"phase": "market_refresh", "error": str(exc)})
                try:
                    data, rec, meta = client.get(
                        "/live_data/weather/miami/calibrations", None, role="miami_calibrations", slot=slot
                    )
                    frame["miami_calibrations"] = {"source_record_id": rec, "versions": []}
                    for i, calibration in enumerate(data["calibrations"]):
                        identifier, _ = remember_version(
                            archive,
                            run_id,
                            "miami_calibration",
                            calibration,
                            rec,
                            i,
                            meta["actual_received_at"],
                            {"provider_publication_is_local_receipt": False},
                        )
                        frame["miami_calibrations"]["versions"].append(identifier)
                except (ValueError, KeyError, TypeError) as exc:
                    frame["errors"].append({"phase": "calibrations", "error": str(exc)})
            if panel is not None and panel["refresh_bucket"] == slot // 10:
                frame["books"] = capture_books(client, slot, panel, panel_id)
            else:
                frame["errors"].append({"phase": "books", "error": "no_current_refresh_panel"})
            frame.update(
                finished_at=iso(utcnow()),
                requests_total=client.requests,
                charged_payload_bytes=client.bytes_used,
                signals=0,
                scores=0,
                fills=0,
                orders=0,
            )
            verify_sources(protocol)
            frame_id = archive.append("e027_cycle", str(run_id), utcnow(), {}, canonical(frame).encode())
            cycles_this_invocation += 1
            errors_total.update(error["phase"] for error in frame["errors"])
            progress = {
                "registration_id": run_id,
                "frame_record_id": frame_id,
                "slot": slot,
                "scheduled_slots": cfg["maximum_cycles"],
                "stop_at": protocol["stop_at"],
                "requests_total": client.requests,
                "charged_payload_bytes": client.bytes_used,
                "metar_reports": frame.get("metar", {}).get("report_count", 0),
                "metar_missing": frame.get("metar", {}).get("missing_stations", cfg["stations"]),
                "selected_contracts": len(panel["selected_markets"]) if panel else 0,
                "metadata_current": panel is not None and panel["refresh_bucket"] == slot // 10,
                "market_exclusions": dict(Counter(s["reason"] for s in panel["market_states"] if s["reason"]))
                if panel
                else {},
                "market_refresh_errors": len(panel["errors"]) if panel else 0,
                "new_metar_versions": len(frame.get("metar", {}).get("new_version_record_ids", [])),
                "new_index_versions": len(frame.get("miami_index", {}).get("new_version_record_ids", [])),
                "index_timestamp_gaps": len(frame.get("miami_index", {}).get("observed_timestamp_gaps", [])),
                "book_batches": len(frame.get("books", {}).get("records", [])),
                "missing_book_contracts": sum(
                    len(r["missing_tickers"]) for r in frame.get("books", {}).get("records", [])
                ),
                "book_batch_errors": len(frame.get("books", {}).get("errors", [])),
                "errors": frame["errors"],
                "signals": 0,
                "scores": 0,
                "fills": 0,
                "orders": 0,
            }
            publish(report_dir / f"E027_progress_{run_id}.json", progress)
            print(canonical(progress), flush=True)
    except CaptureStop as exc:
        stop = str(exc)
    finally:
        client.close()
    verify_sources(protocol)
    completed_slots = {
        read_record(archive, r)["slot"]
        for r in archive.db.execute("SELECT * FROM records WHERE kind='e027_cycle' AND key=?", (str(run_id),))
    }
    all_started = {
        int(r["key"].split(":")[-1])
        for r in archive.db.execute(
            "SELECT key FROM records WHERE kind='e027_cycle_started' AND key LIKE ?", (f"{run_id}:%",)
        )
    }
    completed = len(completed_slots)
    summary = {
        "registration_id": run_id,
        "stop_reason": stop,
        "stop_at": protocol["stop_at"],
        "finished_at": iso(utcnow()),
        "completed_cycles": completed,
        "last_started_slot": last_slot,
        "cycles_this_invocation": cycles_this_invocation,
        "requests_total": client.requests,
        "charged_payload_bytes": client.bytes_used,
        "errors_by_phase_this_invocation": dict(errors_total),
        "interrupted_started_slots": sorted(all_started - completed_slots),
        "uncaptured_elapsed_slots": sorted(set(range(max(last_slot + 1, 0))) - all_started),
        "signals": 0,
        "scores": 0,
        "fills": 0,
        "orders": 0,
    }
    rec = archive.append("e027_stopped", str(run_id), utcnow(), {}, canonical(summary).encode())
    summary["report_record_id"] = rec
    publish(report_dir / f"E027_stopped_{run_id}.json", summary)
    return summary


def publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--register", action="store_true")
    mode.add_argument("--run-record-id", type=int)
    parser.add_argument("--archive-root", default="data")
    parser.add_argument("--report-dir", default="reports")
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    if args.check:
        validate_config(config)
        print(
            canonical(
                {
                    "stations": len(config["stations"]),
                    "maximum_contracts": config["maximum_contracts"],
                    "duration_seconds": config["duration_seconds"],
                    "network_requests": 0,
                }
            )
        )
        return
    archive = Archive(args.archive_root)
    try:
        with (archive.root / config["lock_file"]).open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("Another E027 collector holds the singleton lock") from None
            if args.register:
                rec, protocol = register(archive, config)
                result = {
                    "registration_id": rec,
                    "start_at": protocol["start_at"],
                    "stop_at": protocol["stop_at"],
                    "network_requests": 0,
                }
                publish(Path(args.report_dir) / "E027_registration.json", {**result, "protocol": protocol})
                print(canonical(result))
            else:
                print(canonical(run(archive, args.run_record_id, Path(args.report_dir))))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
