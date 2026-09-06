"""Read-only descriptive reaction study of original E017 weather/quote receipts."""

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path


def now():
    return datetime.now(UTC).isoformat()


def ts(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timestamp requires an explicit timezone")
    return result.timestamp()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ReadOnlyArchive:
    def __init__(self, root="data"):
        self.root = Path(root)
        self.db = sqlite3.connect((self.root / "archive.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.checked = set()
        self.cache = {}

    def raw(self, identifier):
        if identifier not in self.cache:
            record = self.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
            if record is None:
                raise ValueError("Missing original source")
            body = (self.root / "blobs" / record["body_sha256"]).read_bytes()
            if hashlib.sha256(body).hexdigest() != record["body_sha256"]:
                raise ValueError("Original source hash mismatch")
            self.cache[identifier] = (dict(record), json.loads(body))
            self.checked.add(identifier)
        return self.cache[identifier]


def protocol(archive):
    last = archive.db.execute("SELECT MAX(id) FROM records").fetchone()[0]
    return {
        "study": "E017-observation-reaction-descriptive-v1",
        "declared_at": now(),
        "maximum_source_record_id": last,
        "source_run": 47846,
        "observer_run": 30355,
        "horizons_seconds": [60, 300, 900],
        "window_tolerance_seconds": 75,
        "maximum_baseline_age_seconds": 75,
        "arrival_delay_seconds": 1,
        "quote_quantity": 1,
        "primary_station_series": {"KXHIGHMIA": "KMIA", "KXHIGHCHI": "KMDW", "KXHIGHLAX": "KLAX"},
        "reference_proxy_series": {"KXTEMPMIAH": "KMIA"},
        "panel": "Only the four contract identities frozen in E017 registration47846; no replacement markets.",
        "novelty": "First station/observation/rawOb/temp identity; initial or preregistration provider backfill excluded. Metadata-only copies retained as exclusions.",
        "signal": "Temperature change from latest earlier-received same-station observation no older than90minutes; report raw-only changes and missing baselines without inventing a sign.",
        "statistics": "All eligible reports; raw signed one-contract YES bid and ask changes from pre-receipt baseline at fixed1/5/15min windows. Separate each ticker/horizon; temperature-aligned signs only for monotone greater contracts. No p-values, fitted coefficients, best-horizon selection or profit claims.",
        "arrival": "First quote requested at least1s after own weather receipt and completed within75s of that threshold. Candidate paper arrival only, no fill.",
        "windows": "First source requested no earlier than each horizon, completed within75s afterward, and before market close. Earliest invalid or shallow snapshot causes abstention, not a search for later favorable prices.",
        "gross_cross_spread": "Later one-contract bid minus arrival one-contract ask, separately YES and NO. Requires arrival before horizon and a fresh later request. No fees, fills or inventory assumed.",
        "overlap": "Retain events with newer same-station weather before the selected quote and label the confound; do not discard them after seeing outcomes.",
        "interpretation": "Retrospective diagnostic of prospective receipts, not an untouched validation period. Provider receipt is not independently verified first publication. Daily brackets are not monotone in temperature; hourly index is only a reference proxy for KMIA METAR.",
        "code_sha256": {str(Path(__file__).resolve()): fingerprint(__file__)},
    }


def classify_versions(versions, registration_at):
    seen, history, rows = set(), defaultdict(list), []
    for item in sorted(
        versions, key=lambda r: (ts(r["first_received_at"]), ts(r["observation_at"]), r["id"])
    ):
        row = dict(item)
        report = row["report"]
        identity = (row["station"], row["observation_at"], report.get("rawOb"), report.get("temp"))
        receipt = ts(row["first_received_at"])
        observation = ts(row["observation_at"])
        if identity in seen:
            row["classification"] = "metadata_only_copy"
        elif row["is_initial_backfill"] or ts(row["provider_receipt_at"]) < ts(registration_at):
            row["classification"] = "initial_or_old_provider_backfill"
        elif not isinstance(report.get("rawOb"), str) or not report["rawOb"].strip():
            row["classification"] = "missing_raw_report"
        elif row.get("frame_errors"):
            row["classification"] = "frame_error"
        else:
            row["classification"] = "new_weather_report"
        seen.add(identity)
        prior = [
            r
            for r in history[row["station"]]
            if ts(r["first_received_at"]) < receipt
            and observation - 5400 <= ts(r["observation_at"]) <= observation
            and r["report"].get("temp") is not None
        ]
        if prior and report.get("temp") is not None:
            previous = max(
                prior, key=lambda r: (ts(r["observation_at"]), ts(r["first_received_at"]), r["id"])
            )
            row["previous_temperature_record_id"] = previous["id"]
            row["temperature_change_c"] = float(
                Decimal(str(report["temp"])) - Decimal(str(previous["report"]["temp"]))
            )
        else:
            row["previous_temperature_record_id"], row["temperature_change_c"] = None, None
        row["provider_to_own_receipt_seconds"] = receipt - ts(row["provider_receipt_at"])
        rows.append(row)
        history[row["station"]].append(row)
    return rows


def one_contract_quote(item):
    sides = {}
    if "orderbook_fp" not in item:
        raise ValueError("missing_fixed_point_book")
    for side in ("yes", "no"):
        raw = item["orderbook_fp"].get(side + "_dollars")
        if raw is None:
            raise ValueError("missing_side")
        levels = [(Decimal(str(p)), Decimal(str(q))) for p, q in raw]
        if len({p for p, _ in levels}) != len(levels) or any(
            not p.is_finite() or not q.is_finite() or not 0 < p < 1 or q <= 0 for p, q in levels
        ):
            raise ValueError("invalid_book_levels")
        sides[side] = sorted(levels, reverse=True)
    if sides["yes"] and sides["no"] and sides["yes"][0][0] + sides["no"][0][0] >= 1:
        raise ValueError("locked_or_crossed_book")
    amounts = {}
    for side, levels in sides.items():
        remaining, value = Decimal(1), Decimal(0)
        for price, quantity in levels:
            take = min(quantity, remaining)
            value += take * price
            remaining -= take
            if remaining == 0:
                break
        if remaining:
            raise ValueError("insufficient_one_contract_depth")
        amounts[side] = value
    return {
        "yes_bid": str(amounts["yes"]),
        "yes_ask": str(1 - amounts["no"]),
        "no_bid": str(amounts["no"]),
        "no_ask": str(1 - amounts["yes"]),
    }


def select_quote(snapshots, threshold, tolerance, close_at, *, before=False):
    if threshold >= close_at:
        return {"status": "market_closed_at_window"}
    if before:
        eligible = [s for s in snapshots if threshold - tolerance <= s["received_ts"] < threshold]
        selected = (
            max(eligible, key=lambda s: (s["received_ts"], s["source_record_id"])) if eligible else None
        )
    else:
        eligible = [
            s
            for s in snapshots
            if threshold <= s["requested_ts"] <= s["received_ts"] <= threshold + tolerance
            and s["received_ts"] < close_at
        ]
        selected = (
            min(eligible, key=lambda s: (s["received_ts"], s["source_record_id"])) if eligible else None
        )
    if selected is None:
        return {"status": "no_eligible_quote"}
    return {**selected, "status": selected.get("error") or "quoted"}


def reaction(before, arrival, later, receipt, horizon, temperature_change, monotone):
    row = {"horizon_seconds": horizon, "baseline": before, "arrival": arrival, "later": later}
    if before["status"] != "quoted" or later["status"] != "quoted":
        return dict(row, status="unscored_quote_window")
    changes = {
        side: float(Decimal(later["quote"][side]) - Decimal(before["quote"][side]))
        for side in ("yes_bid", "yes_ask")
    }
    row.update(status="descriptive_price_change", changes=changes)
    if monotone and temperature_change is not None and temperature_change != 0:
        sign = 1 if temperature_change > 0 else -1
        row["temperature_aligned_changes"] = {side: sign * value for side, value in changes.items()}
    if arrival["status"] != "quoted":
        row["cross_spread_status"] = "no_arrival_quote"
    elif arrival["received_ts"] >= receipt + horizon:
        row["cross_spread_status"] = "arrival_at_or_after_horizon"
    elif later["requested_ts"] <= arrival["received_ts"]:
        row["cross_spread_status"] = "later_quote_was_requested_before_arrival"
    else:
        row["cross_spread_status"] = "gross_quote_difference_only"
        row["gross_cross_spread_difference"] = {
            side: float(Decimal(later["quote"][side + "_bid"]) - Decimal(arrival["quote"][side + "_ask"]))
            for side in ("yes", "no")
        }
    return row


def evaluate(archive, declaration):
    for path, expected in declaration["code_sha256"].items():
        if fingerprint(path) != expected:
            raise ValueError("Declared diagnostic source changed")
    cutoff = declaration["maximum_source_record_id"]
    _, registration = archive.raw(declaration["source_run"])
    for path, expected in registration["code_sha256"].items():
        if fingerprint(path) != expected:
            raise ValueError("Original collector code changed")
    _, observer = archive.raw(declaration["observer_run"])
    # The observer source is Python text, not JSON; check its archived bytes directly.
    record = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (observer["source_record_id"],)
    ).fetchone()
    if (
        fingerprint(archive.root / "blobs" / record["body_sha256"]) != record["body_sha256"]
        or fingerprint(record["key"]) != record["body_sha256"]
    ):
        raise ValueError("Observer source bytes changed")
    archive.checked.add(record["id"])
    frames = list(
        archive.db.execute(
            "SELECT id FROM records WHERE kind='station_receipt_frame' AND key=? AND id<=? ORDER BY id",
            (str(declaration["source_run"]), cutoff),
        )
    )
    versions, book_ids, frame_errors = [], set(), []
    for frame_row in frames:
        _, frame = archive.raw(frame_row["id"])
        frame_errors.extend({"frame_record_id": frame_row["id"], **e} for e in frame["errors"])
        if not frame["errors"]:
            before_record, _ = archive.raw(frame["before_book_record_id"])
            metar_record, _ = archive.raw(frame["metar_record_id"])
            after_record, _ = archive.raw(frame["after_book_record_id"])
            if (
                not ts(before_record["available_at"])
                < ts(metar_record["available_at"])
                < ts(after_record["available_at"])
            ):
                raise ValueError("Weather receipt is not bracketed by the original books")
        for side in ("before", "after"):
            if frame.get(side + "_book_record_id"):
                book_ids.add(frame[side + "_book_record_id"])
        for identifier in frame.get("new_version_ids", []):
            record, version = archive.raw(identifier)
            source, observations = archive.raw(version["source_record_id"])
            metadata = json.loads(source["metadata"])
            if version["report"] not in observations or not (
                record["available_at"]
                == version["first_received_at"]
                == source["available_at"]
                == metadata["received_at"]
            ):
                raise ValueError("METAR version does not match its original receipt")
            if (
                version["station"] != version["report"]["icaoId"]
                or ts(version["observation_at"]) != version["report"]["obsTime"]
                or ts(version["provider_receipt_at"]) != ts(version["report"]["receiptTime"])
            ):
                raise ValueError("Station, observation or receipt differs from raw METAR fields")
            if ts(version["observation_at"]) > ts(source["available_at"]) or ts(
                version["provider_receipt_at"]
            ) > ts(source["available_at"]):
                raise ValueError("Future weather information in original version")
            versions.append(
                {
                    "id": identifier,
                    **version,
                    "frame_record_id": frame_row["id"],
                    "frame_errors": frame["errors"],
                }
            )
    book_ids.update(
        r[0]
        for r in archive.db.execute(
            "SELECT id FROM records WHERE kind='paper_diagnostic_books' AND key=? AND id<=?",
            (str(declaration["observer_run"]), cutoff),
        )
    )
    books, book_sources = defaultdict(list), Counter()
    for identifier in sorted(book_ids):
        record, raw = archive.raw(identifier)
        metadata = json.loads(record["metadata"])
        requested, received = metadata["request_started_at"], metadata["received_at"]
        if record["available_at"] != received or ts(requested) > ts(received):
            raise ValueError("Quote timestamp mismatch")
        book_sources[record["kind"]] += 1
        for item in raw["orderbooks"]:
            snapshot = {
                "source_record_id": identifier,
                "source_kind": record["kind"],
                "requested_at": requested,
                "received_at": received,
                "requested_ts": ts(requested),
                "received_ts": ts(received),
            }
            try:
                snapshot["quote"] = one_contract_quote(item)
            except (ValueError, KeyError, TypeError) as exc:
                snapshot["error"] = str(exc)
            books[item["ticker"]].append(snapshot)
    rows = classify_versions(versions, registration["started_at"])
    news = [r for r in rows if r["classification"] == "new_weather_report"]
    results = []
    mapped = {}
    for market in registration["panel"]:
        series = market["series_ticker"]
        station = declaration["primary_station_series"].get(series)
        scope = "exact_daily_station"
        if station:
            if "CLI" + station[1:] not in market["rules_primary"]:
                raise ValueError("Daily source station does not match the report station")
        else:
            station = declaration["reference_proxy_series"].get(series)
            scope = "hourly_index_reference_proxy"
        if station is None:
            raise ValueError("Undeclared station mapping")
        mapped[market["ticker"]] = {"station": station, "scope": scope}
        snapshots = books[market["ticker"]]
        for report in news:
            if report["station"] != station:
                continue
            receipt, close = ts(report["first_received_at"]), ts(market["close_time"])
            before = select_quote(
                snapshots, receipt, declaration["maximum_baseline_age_seconds"], close, before=True
            )
            arrival = select_quote(
                snapshots,
                receipt + declaration["arrival_delay_seconds"],
                declaration["window_tolerance_seconds"],
                close,
            )
            for horizon in declaration["horizons_seconds"]:
                later = (
                    {"status": "window_not_yet_elapsed"}
                    if receipt + horizon > ts(declaration["declared_at"])
                    else select_quote(
                        snapshots, receipt + horizon, declaration["window_tolerance_seconds"], close
                    )
                )
                result = reaction(
                    before,
                    arrival,
                    later,
                    receipt,
                    horizon,
                    report["temperature_change_c"],
                    market["strike_type"] == "greater",
                )
                later_time = later.get("received_ts", receipt + horizon)
                overlaps = [
                    r["id"]
                    for r in news
                    if r["station"] == station and receipt < ts(r["first_received_at"]) <= later_time
                ]
                results.append(
                    {
                        "version_record_id": report["id"],
                        "ticker": market["ticker"],
                        "station": station,
                        "scope": scope,
                        "first_received_at": report["first_received_at"],
                        "temperature_change_c": report["temperature_change_c"],
                        "subsequent_news_record_ids": overlaps,
                        **result,
                    }
                )
    summaries = []
    for ticker, mapping in mapped.items():
        for horizon in declaration["horizons_seconds"]:
            selected = [r for r in results if r["ticker"] == ticker and r["horizon_seconds"] == horizon]
            scored = [r for r in selected if r["status"] == "descriptive_price_change"]
            cross = [r for r in selected if r.get("cross_spread_status") == "gross_quote_difference_only"]
            summaries.append(
                {
                    "ticker": ticker,
                    **mapping,
                    "horizon_seconds": horizon,
                    "new_report_windows": len(selected),
                    "scored_price_changes": len(scored),
                    "statuses": dict(Counter(r["status"] for r in selected)),
                    "later_statuses": dict(Counter(r["later"]["status"] for r in selected)),
                    "mean_bid_change": sum(r["changes"]["yes_bid"] for r in scored) / len(scored)
                    if scored
                    else None,
                    "mean_ask_change": sum(r["changes"]["yes_ask"] for r in scored) / len(scored)
                    if scored
                    else None,
                    "changed_bid_or_ask_count": sum(
                        any(v != 0 for v in r["changes"].values()) for r in scored
                    ),
                    "overlapping_news_windows": sum(bool(r["subsequent_news_record_ids"]) for r in selected),
                    "gross_cross_spread_windows": len(cross),
                    "positive_gross_yes_quote_differences": sum(
                        r["gross_cross_spread_difference"]["yes"] > 0 for r in cross
                    ),
                    "positive_gross_no_quote_differences": sum(
                        r["gross_cross_spread_difference"]["no"] > 0 for r in cross
                    ),
                }
            )
    station_counts = Counter(r["station"] for r in news)
    quoted_stations = set(declaration["primary_station_series"].values())
    return {
        "generated_at": now(),
        "declaration": declaration,
        "frames": len(frames),
        "last_frame_record_id": frames[-1][0] if frames else None,
        "frame_errors": frame_errors,
        "raw_sources_verified": len(archive.checked),
        "version_classifications": dict(Counter(r["classification"] for r in rows)),
        "new_reports_by_station": dict(station_counts),
        "new_reports_without_exact_station_panel": sum(
            count for station, count in station_counts.items() if station not in quoted_stations
        ),
        "book_source_counts": dict(book_sources),
        "all_book_tickers": sorted(books),
        "fixed_panel": mapped,
        "distinct_utc_observation_days": len({r["observation_at"][:10] for r in news}),
        "versions": rows,
        "reaction_rows": results,
        "summaries": summaries,
        "network_requests": 0,
        "archive_writes": 0,
        "simulated_or_real_fills": 0,
        "profitability_proven": False,
        "p_values": None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--declaration", type=Path, default=Path("reports/observation_reaction_declaration.json")
    )
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--output", type=Path, default=Path("reports/observation_reaction.json"))
    args = parser.parse_args()
    archive = ReadOnlyArchive()
    try:
        if args.register:
            declaration = protocol(archive)
            with args.declaration.open("x") as stream:
                json.dump(declaration, stream, indent=2)
            print(json.dumps(declaration, indent=2))
        else:
            result = evaluate(archive, json.loads(args.declaration.read_text()))
            args.output.write_text(json.dumps(result, indent=2))
            print(
                json.dumps(
                    {
                        k: v
                        for k, v in result.items()
                        if k not in {"versions", "reaction_rows", "all_book_tickers", "declaration"}
                    },
                    indent=2,
                )
            )
    finally:
        archive.db.close()


if __name__ == "__main__":
    main()
