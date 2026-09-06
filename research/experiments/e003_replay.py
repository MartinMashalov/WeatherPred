"""Verify source cards, training lineage and all saved validation probabilities offline."""

import hashlib
import json
from collections import Counter, defaultdict
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from research.experiments.e002_baselines import build_quotes, read_rows
from research.experiments.e003_daily_baselines import build_weather_rows
from weatherpred.archive import Archive, canonical
from weatherpred.calibration import LogisticCalibration, binary_metrics
from weatherpred.daily_forecasts import predict_model
from weatherpred.nbm import station_cards
from weatherpred.nws_climate import parse_climate_report
from weatherpred.timeutil import iso, utcnow


def main():
    archive = Archive()
    summary = json.loads(Path("reports/E003_daily_baselines.json").read_text())
    model_record = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (summary["model_artifact_record_id"],)
    ).fetchone()
    artifact = archive.json(model_record)
    protocol = artifact["protocol"]
    stations = json.loads(Path("config/e003_nbm_acquisition.json").read_text())["stations"]
    windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
    wanted = set(stations.values())
    cards_checked = 0
    # Reconstruct all downloaded development station cards from exact raw bodies.
    for row in archive.db.execute("SELECT * FROM records WHERE kind='e003_nbm_day' ORDER BY key"):
        day = archive.json(row)
        reconstructed = {}
        for rec in day["raw_source_record_ids"]:
            source = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            body = archive.body(source)
            headers = json.loads(source["metadata"])["headers"]
            if headers["etag"] != day["object"]["etag"]:
                raise ValueError("Raw object and derived ETag differ")
            for station, card in station_cards(body, wanted).items():
                value = {k: card[k] for k in ("station", "version", "runtime", "rows")}
                if station in reconstructed and reconstructed[station] != value:
                    raise ValueError("Conflicting station cards in original responses")
                reconstructed[station] = value
        for station, card in day["cards"].items():
            if reconstructed[station] != {k: card[k] for k in ("station", "version", "runtime", "rows")}:
                raise ValueError("Derived NBM card differs from raw source")
            cards_checked += 1
    # Use the label dataset bytes pinned BEFORE fitting, not a later revised file.
    label_source = archive.db.execute(
        "SELECT * FROM records WHERE id=?", (artifact["source_record_ids"]["reports/E003_NWS_labels.jsonl"],)
    ).fetchone()
    labels = [json.loads(line) for line in archive.body(label_source).decode().splitlines()]
    products = json.loads(Path("config/e003_nws_labels.json").read_text())["products"]
    zipped, labels_checked = {}, 0
    for label in labels:
        selected = label["selected_report"]
        if selected is None:
            continue
        rec = selected["source_record_id"]
        if rec not in zipped:
            raw = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
            zipped[rec] = ZipFile(BytesIO(archive.body(raw)))
        entry = zipped[rec].infolist()[selected["zip_entry_index"]]
        text = zipped[rec].read(entry)
        if hashlib.sha256(text).hexdigest() != selected["raw_report_sha256"]:
            raise ValueError("Original NWS version bytes differ from selected report")
        parsed = parse_climate_report(
            text.decode("ascii"),
            entry.filename,
            products[label["series"]],
            protocol["training_start"],
            protocol["validation_end_exclusive"],
            windows[label["series"]]["standard_utc_offset_hours"],
        )
        if any(parsed[k] != selected[k] for k in ("maximum_f", "issued_at", "day", "station_name")):
            raise ValueError("Selected NWS label differs from raw report")
        labels_checked += 1
    for value in zipped.values():
        value.close()
    weather_rows, _ = build_weather_rows(archive, labels, protocol, stations, windows)
    train = [r for r in weather_rows if r["split"] == "train"]
    if hashlib.sha256(canonical(train).encode()).hexdigest() != artifact["training_data_sha256"]:
        raise ValueError("Rebuilt training lineage differs from frozen artifact")
    weather = {r["event"]: r for r in weather_rows if r["split"] == "validation"}
    markets = read_rows("reports/E002_development_markets.jsonl")
    market_map = {m["ticker"]: m for m in markets}
    acquisition = json.loads(Path("reports/E002_acquisition.json").read_text())
    quote_protocol = json.loads(Path("config/e002_market_baseline.json").read_text())
    details = json.loads(Path("config/e002_scoring.json").read_text())
    quotes, _ = build_quotes(archive, markets, acquisition, quote_protocol, details)
    quote_map = {(r["ticker"], r["horizon_hours"]): r for r in quotes if r["split"] == "validation"}
    market_model = archive.json(
        archive.db.execute(
            "SELECT * FROM records WHERE id=?", (summary["market_model_record_id"],)
        ).fetchone()
    )
    logistic = {r["horizon_hours"]: LogisticCalibration(**r["model"]) for r in market_model["models"]}
    rows = read_rows("reports/E003_scored.jsonl")
    paired = [r for r in quotes if r["split"] == "validation" and r["event"] in weather]
    names = [*protocol["models"], "raw_market", "logistic_market"]
    expected_keys = {(r["ticker"], r["horizon_hours"], name) for r in paired for name in names}
    actual_keys = {(r["ticker"], r["horizon_hours"], r["model"]) for r in rows}
    if actual_keys != expected_keys or len(rows) != len(expected_keys):
        raise ValueError("Saved score coverage has missing, extra or duplicate rows")
    contract_counts = Counter((r["horizon_hours"], r["day"], r["event"]) for r in paired)
    event_counts = Counter((h, day) for h, day, _ in contract_counts)
    event_scores = defaultdict(list)
    distributions = {}
    largest = 0.0
    for row in rows:
        quote = quote_map[(row["ticker"], row["horizon_hours"])]
        if row["outcome"] != quote["outcome"] or row["event"] != quote["event"]:
            raise ValueError("Outcome/event changed between raw quote and saved score")
        if row["model"] == "raw_market":
            probability = quote["midpoint"]
        elif row["model"] == "logistic_market":
            probability = float(logistic[row["horizon_hours"]].predict([quote["midpoint"]])[0])
        else:
            key = row["event"], row["model"]
            if key not in distributions:
                distributions[key] = predict_model(artifact["models"], row["model"], weather[row["event"]])
            probability = distributions[key].probability(market_map[row["ticker"]])
        differences = [abs(probability - row["probability"])]
        scores = binary_metrics([probability], [row["outcome"]])
        differences.extend(abs(float(value[0]) - row[k]) for k, value in scores.items())
        expected_weight = (
            1
            / contract_counts[(row["horizon_hours"], row["day"], row["event"])]
            / event_counts[(row["horizon_hours"], row["day"])]
        )
        differences.append(abs(expected_weight - row["weight"]))
        for metric, values in scores.items():
            event_scores[(row["model"], row["horizon_hours"], row["day"], row["event"], metric)].append(
                float(values[0])
            )
        largest = max(largest, *differences)
    daily_scores = defaultdict(list)
    for (name, horizon, day, _, metric), values in event_scores.items():
        daily_scores[(name, horizon, day, metric)].append(sum(values) / len(values))
    overall = defaultdict(list)
    for (name, horizon, _, metric), values in daily_scores.items():
        overall[(name, horizon, metric)].append(sum(values) / len(values))
    for row in summary["validation_scores"]:
        for metric in ("brier", "log_loss"):
            values = overall[(row["model"], row["horizon_hours"], metric)]
            largest = max(largest, abs(sum(values) / len(values) - row[metric]))
    if largest > 1e-12:
        raise ValueError("Saved probabilities/scores disagree with frozen model replay")
    result = {
        "generated_at": iso(utcnow()),
        "nbm_station_cards_reconstructed": cards_checked,
        "nws_versions_reconstructed": labels_checked,
        "training_events_reproduced": len(train),
        "validation_score_rows_reproduced": len(rows),
        "complete_score_membership_verified": True,
        "event_then_day_aggregation_verified": True,
        "maximum_numeric_difference": largest,
        "model_refits": 0,
        "network_requests": 0,
        "profitability_proven": False,
    }
    archive.append("experiment_audit", "E003_raw_replay", utcnow(), {}, canonical(result).encode())
    Path("reports/E003_replay.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    archive.close()


if __name__ == "__main__":
    main()
