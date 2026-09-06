"""Reproduce the exploratory availability probes. No outcome is a sealed holdout.

Run: uv run python research/experiments/e000_data_availability.py
Requests are public GETs, raw replies are permanently archived, summaries describe
coverage only. These probes do not establish historical fill or publication times.
"""

import json
from pathlib import Path

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.http import PublicClient
from weatherpred.timeutil import parse_time, utcnow


def market_probes(client):
    results = []
    events = [
        "KXHIGHNY-26JUL01",
        "KXHIGHNY-26AUG01",
        "KXHIGHNY-26SEP01",
        "KXHIGHNY-26SEP05",
        "KXTEMPMIAH-26SEP0507",
    ]
    for event in events:
        entry = {"event": event, "exploratory_only": True}
        try:
            historical = "JUL01" in event
            path = "/historical/markets" if historical else "/markets"
            data, rec = client.json(
                path, {"event_ticker": event, "limit": 1000}, kind="historical_probe", key=event
            )
            entry.update(record_id=rec, markets=len(data["markets"]), cursor_present=bool(data.get("cursor")))
            if data["markets"]:
                m = min(data["markets"], key=lambda x: x["ticker"])
                entry.update(
                    sample_ticker=m["ticker"],
                    rules_primary=m["rules_primary"],
                    rules_secondary=m["rules_secondary"],
                )
                series = event.split("-")[0]
                start, end = (int(parse_time(m[k]).timestamp()) for k in ("open_time", "close_time"))
                prefix = "/historical/markets/" if historical else "/series/" + series + "/markets/"
                cd, cr = client.json(
                    prefix + m["ticker"] + "/candlesticks",
                    {"start_ts": start, "end_ts": end, "period_interval": 1},
                    kind="candle_probe",
                    key=m["ticker"],
                )
                entry.update(
                    candles_record_id=cr,
                    candles=len(cd.get("candlesticks", [])),
                    candle_fields=list(cd["candlesticks"][0]) if cd.get("candlesticks") else [],
                )
                td, tr = client.json(
                    "/historical/trades" if historical else "/markets/trades",
                    {"ticker": m["ticker"], "limit": 10},
                    kind="trades_probe",
                    key=m["ticker"],
                )
                entry.update(
                    trades_record_id=tr,
                    sample_trades=len(td.get("trades", [])),
                    trade_fields=list(td["trades"][0]) if td.get("trades") else [],
                )
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return results


def weather_probes(client):
    urls = [
        (
            "HRRR historical index",
            "https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.20260701/conus/hrrr.t12z.wrfsfcf01.grib2.idx",
        ),
        (
            "NBM historical objects",
            "https://noaa-nbm-grib2-pds.s3.amazonaws.com/?list-type=2&prefix=blend.20260701/12/&max-keys=5",
        ),
        (
            "GFS MOS historical",
            "https://mesonet.agron.iastate.edu/api/1/mos.json?station=KNYC&model=GFS&runtime=2026-07-01T12:00Z",
        ),
        (
            "NBM MOS historical",
            "https://mesonet.agron.iastate.edu/api/1/mos.json?station=KNYC&model=NBS&runtime=2026-07-01T12:00Z",
        ),
        (
            "CLI metadata and raw products",
            "https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=CLINYC&sts=2026-07-02T00:00Z&ets=2026-07-03T00:00Z",
        ),
        ("TWC daily source", "https://weather.com/kalshi/api/climate/primary?date=2026-09-05"),
    ]
    results = []
    for name, url in urls:
        row = {"name": name, "url": url}
        try:
            response, rec = client.get(url, kind="weather_source_probe", key=name)
            row.update(
                record_id=rec,
                status=response.status_code,
                bytes=len(response.content),
                content_type=response.headers.get("content-type"),
                last_modified=response.headers.get("last-modified"),
            )
            if "json" in response.headers.get("content-type", ""):
                data = response.json()
                row["keys"] = list(data)
                row["data_count"] = len(data.get("data", data.get("results", [])))
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            row["error"] = str(exc)
        results.append(row)
    return results


def main():
    archive = Archive()
    client = PublicClient(archive)
    try:
        report = {
            "generated_at": utcnow().isoformat(),
            "markets": market_probes(client),
            "weather": weather_probes(client),
            "sealed_holdout": False,
        }
        rec = archive.append("experiment_report", "E000-probes", utcnow(), {}, canonical(report).encode())
        Path("reports/E000_data_availability.json").write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    "report_record_id": rec,
                    "market_probes": len(report["markets"]),
                    "weather_probes": len(report["weather"]),
                    "errors": [r for k in ("markets", "weather") for r in report[k] if "error" in r],
                },
                indent=2,
            )
        )
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
