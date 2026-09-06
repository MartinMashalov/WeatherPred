"""Offline reconstruction of updated guidance, feature timing and frozen scores.

The feature reconstruction below does not call updated_features/build_updates.
Original NWS/early-guidance lineage is separately checked by e003_replay.py.
"""

import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from research.experiments.e007_market_weather_pool import by_id, inputs
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import LogisticCalibration
from weatherpred.daily_forecasts import predict_model
from weatherpred.nbm import station_cards
from weatherpred.timeutil import iso, parse_time, utcnow

NS = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}


def record(archive, identifier):
    row = archive.db.execute("SELECT * FROM records WHERE id=?", (identifier,)).fetchone()
    if row is None:
        raise ValueError("Missing raw replay source")
    return row


def raw_cycle(archive, identifier, stations):
    source = by_id(archive, identifier)
    obj = source["object"]
    listing = record(archive, obj["listing_record_id"])
    root = ET.fromstring(archive.body(listing))
    found = [n for n in root.findall("s:Contents", NS) if n.findtext("s:Key", namespaces=NS) == obj["key"]]
    if root.findtext("s:IsTruncated", namespaces=NS) != "false" or len(found) != 1:
        raise ValueError("Raw updated-object listing is ambiguous")
    node = found[0]
    expected = {
        "etag": node.findtext("s:ETag", namespaces=NS),
        "last_modified": node.findtext("s:LastModified", namespaces=NS),
        "size": int(node.findtext("s:Size", namespaces=NS)),
    }
    if any(obj[k] != v for k, v in expected.items()):
        raise ValueError("Stored updated-object identity differs from raw listing")
    reconstructed = {}
    for raw_id in source["raw_source_record_ids"]:
        raw = record(archive, raw_id)
        if raw["kind"] == "e010_nbm_listing":
            if raw_id != listing["id"]:
                raise ValueError("Unexpected listing among source responses")
            continue
        metadata = json.loads(raw["metadata"])
        headers, body = metadata["headers"], archive.body(raw)
        if (
            headers["etag"] != obj["etag"]
            or parsedate_to_datetime(headers["last-modified"]) != parse_time(obj["last_modified"])
            or metadata["conditional_request_headers"]["If-Match"] != obj["etag"]
            or not metadata["url"].endswith("/" + obj["key"])
        ):
            raise ValueError("Raw update body is from a different object version")
        offset = 0
        if raw["kind"] == "e010_nbm_range":
            matched = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", headers["content-range"])
            if matched is None or metadata["status"] != 206:
                raise ValueError("Invalid archived byte-range response")
            offset, end, total = map(int, matched.groups())
            if len(body) != end - offset + 1 or total != obj["size"]:
                raise ValueError("Byte-range size differs from raw listing")
            if metadata["conditional_request_headers"]["Range"] != f"bytes={offset}-{end}":
                raise ValueError("Response range differs from request")
        elif raw["kind"] == "e010_nbm_full":
            if metadata["status"] != 200 or len(body) != obj["size"]:
                raise ValueError("Incomplete full-object recovery")
            if "-" not in obj["etag"] and hashlib.md5(body).hexdigest() != obj["etag"].strip('"'):
                raise ValueError("Full single-part object digest differs from S3 ETag")
        else:
            raise ValueError("Unexpected updated-guidance raw source kind")
        # Exact wanted station avoids interpreting unrelated partial edge cards.
        wanted = {raw["key"].rsplit(":", 1)[-1]} if raw["kind"] == "e010_nbm_range" else stations
        for station, card in station_cards(body, wanted).items():
            value = {k: card[k] for k in ("station", "version", "runtime", "rows")}
            value["relative_byte_offset"] = card["relative_byte_offset"] + offset
            if station in reconstructed and reconstructed[station] != value:
                raise ValueError("Conflicting raw station cards for one object")
            reconstructed[station] = value
    if set(reconstructed) != stations or set(source["cards"]) != stations:
        raise ValueError("Incomplete updated station membership")
    hour = source["cycle_utc_hour"]
    if obj["key"] != f"blend.{source['day'].replace('-', '')}/{hour:02}/text/blend_nbstx.t{hour:02}z":
        raise ValueError("Raw object key differs from source day/cycle")
    for station, value in reconstructed.items():
        if value != {k: source["cards"][station][k] for k in value}:
            raise ValueError("Updated station card differs from original response")
        if value["runtime"] != f"{source['day']}T{hour:02}:00:00+00:00":
            raise ValueError("Raw card runtime differs from object day/cycle")
    return {**source, "record_id": identifier, "cards": reconstructed}


def reconstruct_features(original, sources, station, offset):
    """Independently choose the newest eligible source for every exact valid time."""
    start = datetime.combine(date.fromisoformat(original["day"]), datetime.min.time(), tzinfo=UTC)
    start -= timedelta(hours=offset)
    end, decision = start + timedelta(days=1), start + timedelta(hours=12)
    grid = list(range(math.ceil(start.timestamp() / 10800) * 10800, int(end.timestamp()), 10800))
    if grid != original["features"]["grid_valid_times"] or len(grid) != 8:
        raise ValueError("Original grid is not the registered local-standard-time day")
    eligible = [s for s in sources if parse_time(s["object"]["last_modified"]) <= decision]
    eligible.sort(key=lambda s: parse_time(s["cards"][station]["runtime"]))
    if not eligible or parse_time(eligible[0]["cards"][station]["runtime"]).hour != 1:
        raise ValueError("Missing eligible original control")
    for s in eligible:
        if parse_time(s["cards"][station]["runtime"]) > parse_time(s["object"]["last_modified"]):
            raise ValueError("Raw source storage precedes its initialization")
    grid_lineage = {}
    for valid in grid:
        chosen = None
        for s in reversed(eligible):
            card = s["cards"][station]
            point = next(
                (r for r in card["rows"] if int(parse_time(r["valid_at"]).timestamp()) == valid), None
            )
            if point is not None and point["tmp"] is not None:
                chosen = {
                    "tmp": point["tmp"],
                    "source_record_id": s["record_id"],
                    "runtime": card["runtime"],
                    "object_last_modified": s["object"]["last_modified"],
                }
                break
        if chosen is None:
            raise ValueError("Updated grid cannot be reconstructed")
        grid_lineage[str(valid)] = chosen
    target = datetime.combine(
        date.fromisoformat(original["day"]) + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    )
    proxy = None
    for s in reversed(eligible):
        card = s["cards"][station]
        point = next((r for r in card["rows"] if parse_time(r["valid_at"]) == target), None)
        if point is not None and point["txn"] is not None and point["xnd"] is not None and point["xnd"] >= 0:
            proxy = {
                "txn_18h": float(point["txn"]),
                "xnd_18h": float(point["xnd"]),
                "source_record_id": s["record_id"],
                "runtime": card["runtime"],
                "object_last_modified": s["object"]["last_modified"],
                "version": card["version"],
            }
            break
    if proxy is None:
        raise ValueError("Updated proxy cannot be reconstructed")
    return {
        **original["features"],
        "grid_max": float(max(r["tmp"] for r in grid_lineage.values())),
        **{k: proxy[k] for k in ("txn_18h", "xnd_18h", "runtime", "object_last_modified", "version")},
        "grid_source_lineage": grid_lineage,
        "proxy_source_lineage": proxy,
        "eligible_source_record_ids": [s["record_id"] for s in eligible],
        "decision_at": decision.isoformat(),
    }


def main():
    archive = Archive()
    try:
        summary = json.loads(Path("reports/E010_updated_models.json").read_text())
        artifact = by_id(archive, summary["model_record_id"])
        config = artifact["config"]
        for path, identifier in artifact["source_record_ids"].items():
            if Path(path).read_bytes() != archive.body(record(archive, identifier)):
                raise ValueError("E010 fitted source changed: " + path)
        base, originals, markets, quotes = inputs(
            archive, {"weather_model_record_id": config["base_model_record_id"]}
        )
        acquisition_row = archive.latest(
            "experiment_report", "E010_acquisition", as_of=artifact["published_at"]
        )
        acquisition = archive.json(acquisition_row)
        identities = [
            (r["day"], r["cycle_utc_hour"]) for r in [*acquisition["cycles"], *acquisition["errors"]]
        ]
        expected = {(date(2025, 1, 1) + timedelta(days=i), h) for i in range(273) for h in (7, 13)}
        if {(date.fromisoformat(d), h) for d, h in identities} != expected or len(identities) != 546:
            raise ValueError("Acquisition census is incomplete or duplicated")
        stations = json.loads(Path("config/e010_nbm_updates.json").read_text())["stations"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        sources, cards = defaultdict(list), 0
        for r in acquisition["cycles"]:
            reconstructed = raw_cycle(archive, r["record_id"], set(stations.values()))
            if (reconstructed["day"], reconstructed["cycle_utc_hour"]) != (r["day"], r["cycle_utc_hour"]):
                raise ValueError("Acquisition manifest points to a different source")
            sources[r["day"]].append(reconstructed)
            cards += len(reconstructed["cards"])
        rebuilt = []
        for row in originals:
            early = dict(by_id(archive, row["nbm_record_id"]), record_id=row["nbm_record_id"])
            features = reconstruct_features(
                row,
                [early, *sources[row["day"]]],
                stations[row["series"]],
                windows[row["series"]]["standard_utc_offset_hours"],
            )
            rebuilt.append({**row, "features": features})
        saved = [json.loads(line) for line in Path("reports/E010_features.jsonl").read_text().splitlines()]
        if canonical(rebuilt) != canonical(saved):
            raise ValueError("Independent feature/source reconstruction differs from saved dataset")
        train = [r for r in rebuilt if r["split"] == "train"]
        if hashlib.sha256(canonical(train).encode()).hexdigest() != artifact["training_sha256"]:
            raise ValueError("Updated training data differs from frozen model")
        cutoff = parse_time(config["fit_cutoff"]).timestamp()
        if any(max(r["settled_ts"], r["nws_issue_ts"]) >= cutoff for r in train):
            raise ValueError("Training used an outcome published after fitting")
        weather = {r["event"]: r for r in rebuilt if r["split"] == "validation"}
        old_weather = {r["event"]: r for r in originals if r["split"] == "validation"}
        paired = [
            r
            for r in quotes
            if r["split"] == "validation" and r["horizon_hours"] == 12 and r["event"] in weather
        ]
        names = [*config["models"], *["old_" + n for n in config["models"]], "raw_market", "logistic_market"]
        scored = [json.loads(line) for line in Path("reports/E010_scored.jsonl").read_text().splitlines()]
        keys = {(r["ticker"], name) for r in paired for name in names}
        if {(r["ticker"], r["model"]) for r in scored} != keys or len(scored) != len(keys):
            raise ValueError("Missing, extra or duplicated score rows")
        quote_map = {r["ticker"]: r for r in paired}
        market_map = {r["ticker"]: r for r in markets}
        calibration = LogisticCalibration(
            **next(r["model"] for r in by_id(archive, 20410)["models"] if r["horizon_hours"] == 12)
        )
        contracts = Counter((r["day"], r["event"]) for r in paired)
        events = Counter(day for day, _ in contracts)
        distributions, event_scores, largest = {}, defaultdict(list), 0.0
        for row in scored:
            quote, name = quote_map[row["ticker"]], row["model"]
            if any(row[k] != quote[k] for k in ("event", "series", "day", "horizon_hours", "outcome")):
                raise ValueError("Saved score differs from raw quote identity/outcome")
            if name == "raw_market":
                probability = quote["midpoint"]
            elif name == "logistic_market":
                probability = float(calibration.predict([quote["midpoint"]])[0])
            else:
                key = row["event"], name
                if key not in distributions:
                    old = name.startswith("old_")
                    distributions[key] = predict_model(
                        base["models"] if old else artifact["models"],
                        name[4:] if old else name,
                        (old_weather if old else weather)[row["event"]],
                    )
                probability = distributions[key].probability(market_map[row["ticker"]])
            p, y = min(1 - 1e-6, max(1e-6, probability)), row["outcome"]
            values = {
                "brier": (probability - y) ** 2,
                "log_loss": -y * math.log(p) - (1 - y) * math.log1p(-p),
            }
            weight = 1 / contracts[(row["day"], row["event"])] / events[row["day"]]
            largest = max(
                largest,
                abs(probability - row["probability"]),
                abs(weight - row["weight"]),
                *[abs(v - row[k]) for k, v in values.items()],
            )
            for metric, value in values.items():
                event_scores[(name, row["day"], row["event"], metric)].append(value)
        daily = defaultdict(list)
        for (name, day, _, metric), values in event_scores.items():
            daily[name, day, metric].append(sum(values) / len(values))
        for row in summary["validation_scores"]:
            for metric in ("brier", "log_loss"):
                values = []
                for day in row["daily"]:
                    day_values = daily[row["model"], day, metric]
                    mean = sum(day_values) / len(day_values)
                    largest = max(largest, abs(mean - row["daily"][day][metric]))
                    values.append(mean)
                largest = max(largest, abs(sum(values) / len(values) - row[metric]))
        if largest > 1e-12:
            raise ValueError("Frozen probabilities or event/day scores differ from replay")
        result = {
            "generated_at": iso(utcnow()),
            "model_record_id": summary["model_record_id"],
            "updated_cycles_reconstructed": len(acquisition["cycles"]),
            "updated_station_cards_reconstructed": cards,
            "independent_features_reconstructed": len(rebuilt),
            "training_events_reproduced": len(train),
            "validation_score_rows_reproduced": len(scored),
            "source_storage_gates_verified": True,
            "complete_score_membership_verified": True,
            "event_then_day_aggregation_verified": True,
            "maximum_numeric_difference": largest,
            "model_refits": 0,
            "network_requests": 0,
            "profitability_proven": False,
        }
        archive.append("experiment_audit", "E010_raw_replay", utcnow(), {}, canonical(result).encode())
        Path("reports/E010_replay.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result, indent=2))
    finally:
        archive.close()


if __name__ == "__main__":
    main()
