"""Compare current US source tables, original NWS editions and exchange settlements."""

import hashlib
import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.contracts import historical_predicate, yes_at
from weatherpred.http import PublicClient
from weatherpred.nws_climate import parse_climate_report
from weatherpred.timeutil import iso, parse_time, utcnow


def main():
    archive = Archive()
    client = PublicClient(archive, interval=0.5)
    try:
        stations = json.loads(Path("config/e003_nbm_acquisition.json").read_text())["stations"]
        products = json.loads(Path("config/e003_nws_labels.json").read_text())["products"]
        windows = json.loads(Path("config/e002_source_windows.json").read_text())["series"]
        # Current catalog and raw rules32204 explicitly map the new Houston
        # series to CLIHOU. Preserve the original v1 missing-event evidence.
        aliases = {"KXHIGHHOU": "KXHIGHTHOU"}
        stations = {aliases.get(s, s): v for s, v in stations.items()}
        products = {aliases.get(s, s): v for s, v in products.items()}
        windows = {aliases.get(s, s): v for s, v in windows.items()}
        protocol = {
            "experiment": "E012-current-US-source-matching-v2",
            "amendment": "Five v1 Houston events404 because current domestic catalog uses KXHIGHTHOU. Source32204 explicitly names CLIHOU, the same original KHOU station/product. Change only this catalog alias, retaining all8stations/all5days and v1 results; no model score or selection changes.",
            "series_aliases": aliases,
            "parent_protocol_record_id": 31696,
            "start_day": "2026-09-01",
            "end_day_exclusive": "2026-09-06",
            "stations": stations,
            "products": products,
            "rule": "Compare all8stations/all5days, retaining preliminary/missing/conflicting sources. Parse each ZIP edition separately. Compare first and latest complete NWS versions issued by exchange settlement with current TWC table and exact exchange value/predicates. No outcomes inferred from winning brackets. NWS document timestamps are not verified receipt times. No fitted model, quote PnL or2025holdout.",
        }
        source_id = archive.append(
            "research_source", str(Path(__file__)), utcnow(), {}, Path(__file__).read_bytes()
        )
        registration = archive.append(
            "experiment_protocol",
            protocol["experiment"],
            utcnow(),
            {},
            canonical({**protocol, "source_record_id": source_id}).encode(),
        )
        print(json.dumps({"source_matching_protocol": registration}), flush=True)
        tables, versions, errors, raw_ids = {}, defaultdict(list), [], []
        for i in range(5):
            day = (date(2026, 9, 1) + timedelta(days=i)).isoformat()
            cached = archive.latest("e012_daily_table", day)
            if cached:
                table, rec = archive.json(cached), cached["id"]
            else:
                table, rec = client.json(
                    "https://weather.com/kalshi/api/climate/primary",
                    {"date": day},
                    kind="e012_daily_table",
                    key=day,
                )
            raw_ids.append(rec)
            if table["date"] != day or len({r["station"]["icao"] for r in table["results"]}) != len(
                table["results"]
            ):
                raise ValueError("TWC date or station membership mismatch")
            tables[day] = {r["station"]["icao"]: {**r, "source_record_id": rec} for r in table["results"]}
        for series, product in products.items():
            cached = archive.latest("e012_nws_zip", product)
            if cached:
                body, rec = archive.body(cached), cached["id"]
            else:
                response, rec = client.get(
                    "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py",
                    {
                        "pil": product,
                        "sdate": "2026-09-01T00:00Z",
                        "edate": "2026-09-06T13:00Z",
                        "fmt": "zip",
                        "limit": 9999,
                        "order": "asc",
                    },
                    kind="e012_nws_zip",
                    key=product,
                )
                body = response.content
            raw_ids.append(rec)
            with ZipFile(BytesIO(body)) as zipped:
                if len(zipped.infolist()) >= 9999:
                    raise ValueError("Source editions may be truncated")
                for index, entry in enumerate(zipped.infolist()):
                    text = zipped.read(entry)
                    try:
                        parsed = parse_climate_report(
                            text.decode("ascii"),
                            entry.filename,
                            product,
                            protocol["start_day"],
                            protocol["end_day_exclusive"],
                            windows[series]["standard_utc_offset_hours"],
                        )
                        if parsed["status"] == "complete":
                            versions[series, parsed["day"]].append(
                                {
                                    **parsed,
                                    "source_record_id": rec,
                                    "zip_entry_index": index,
                                    "raw_sha256": hashlib.sha256(text).hexdigest(),
                                }
                            )
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append(
                            {
                                "stage": "NWS_parse",
                                "series": series,
                                "entry": entry.filename,
                                "source_record_id": rec,
                                "error": str(exc),
                            }
                        )
            print(
                json.dumps(
                    {
                        "NWS_product": product,
                        "editions": sum(len(v) for (s, _), v in versions.items() if s == series),
                    }
                ),
                flush=True,
            )
        rows = []
        for series, station in stations.items():
            for day in sorted(tables):
                event = series + "-" + date.fromisoformat(day).strftime("%y%b%d").upper()
                row = {"event": event, "series": series, "station": station, "day": day}
                try:
                    cached = archive.latest("e012_exchange_event", event)
                    if cached:
                        data, rec = archive.json(cached), cached["id"]
                    else:
                        data, rec = client.json("/events/" + event, kind="e012_exchange_event", key=event)
                    raw_ids.append(rec)
                    markets = data["markets"]
                    if not markets or any(m["event_ticker"] != event for m in markets):
                        raise ValueError("Missing or mixed event membership")
                    values = {Decimal(m["expiration_value"]) for m in markets if m.get("expiration_value")}
                    settled = [parse_time(m["settlement_ts"]) for m in markets if m.get("settlement_ts")]
                    if (
                        len(values) != 1
                        or len(settled) != len(markets)
                        or any(m["status"] != "finalized" for m in markets)
                    ):
                        raise ValueError("Missing consistent finalized numerical event value")
                    value, settlement = values.pop(), max(settled)
                    parsed_markets = [historical_predicate(m) for m in markets]
                    if any(bool(yes_at(m, value)) != (m["result"] == "yes") for m in parsed_markets):
                        raise ValueError("Numerical settlement and binary predicates differ")
                    twc = tables[day].get(station)
                    if twc is None or twc.get("data") is None:
                        raise ValueError("Missing matched TWC station row")
                    nws = sorted(
                        versions[series, day],
                        key=lambda r: (r["issued_at"], r["source_record_id"], r["zip_entry_index"]),
                    )
                    eligible = [r for r in nws if parse_time(r["issued_at"]) <= settlement]
                    first = (
                        [r for r in eligible if r["issued_at"] == eligible[0]["issued_at"]]
                        if eligible
                        else []
                    )
                    last = (
                        [r for r in eligible if r["issued_at"] == eligible[-1]["issued_at"]]
                        if eligible
                        else []
                    )
                    first_values, last_values = (
                        {r["maximum_f"] for r in first},
                        {r["maximum_f"] for r in last},
                    )
                    table_value = twc["data"]["maxTemp"]
                    rows.append(
                        {
                            **row,
                            "status": "compared",
                            "exchange_value": str(value),
                            "exchange_source_record_id": rec,
                            "exchange_settled_at": iso(settlement),
                            "contracts": len(markets),
                            "exchange_primary_sources": sorted(
                                {
                                    "TWC"
                                    if "weather company" in m.get("rules_primary", "").lower()
                                    else "other"
                                    for m in markets
                                }
                            ),
                            "twc": twc,
                            "twc_matches_exchange": Decimal(str(table_value)) == value,
                            "nws_versions": nws,
                            "nws_first_values_by_settlement": sorted(first_values),
                            "nws_last_values_by_settlement": sorted(last_values),
                            "nws_first_matches_twc": len(first_values) == 1
                            and Decimal(str(next(iter(first_values)))) == Decimal(str(table_value)),
                            "nws_last_matches_twc": len(last_values) == 1
                            and Decimal(str(next(iter(last_values)))) == Decimal(str(table_value)),
                            "all_binary_results_reproduced_from_exchange_value": True,
                        }
                    )
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    errors.append({**row, "stage": "event_compare", "error": str(exc)})
                    rows.append({**row, "status": "unresolved", "error": str(exc)})
        compared = [r for r in rows if r["status"] == "compared"]
        result = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": registration,
            "source_record_ids": sorted(set(raw_ids)),
            "expected_events": 40,
            "rows": rows,
            "errors": errors,
            "summary": {
                "event_statuses": dict(Counter(r["status"] for r in rows)),
                "twc_statuses": dict(Counter(r["twc"]["status"] for r in compared)),
                "twc_exchange_matches": sum(r["twc_matches_exchange"] for r in compared),
                "first_nws_twc_matches": sum(r["nws_first_matches_twc"] for r in compared),
                "last_nws_twc_matches": sum(r["nws_last_matches_twc"] for r in compared),
                "nonempty_twc_issue_times": sum(bool(r["twc"]["data"]["issueTime"]) for r in compared),
            },
            "limitations": [
                "Current displayed and retrieved historical source values do not establish original trader availability",
                "First-vintage versus later correction policy and CF6 backup require explicit checks wherever editions differ",
                "No general source equivalence follows from five days and eight stations",
            ],
            "forecasts_scored": False,
            "historical_pnl": None,
            "holdout_accessed": False,
            "profitability_proven": False,
        }
        archive.append("experiment_report", "E012_current_source", utcnow(), {}, canonical(result).encode())
        Path("reports/E012_current_source.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"summary": result["summary"], "errors": errors}, indent=2))
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
