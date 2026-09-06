"""Acquire and reconcile original NWS daily report versions; no forecast scoring."""

import hashlib
import json
import logging
from collections import Counter, defaultdict
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import httpx

from weatherpred.archive import Archive, canonical
from weatherpred.contracts import yes_at
from weatherpred.http import PublicClient
from weatherpred.nws_climate import parse_climate_report
from weatherpred.timeutil import iso, parse_time, utcnow

LOG = logging.getLogger(__name__)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archive = Archive()
    client = PublicClient(archive, interval=1)
    protocol = json.loads(Path("config/e003_nws_labels.json").read_text())
    windows = json.loads(Path("config/e002_source_windows.json").read_text())
    source_ids = [
        archive.append("research_source", str(p), utcnow(), {}, p.read_bytes())
        for p in (Path(__file__), Path("weatherpred/nws_climate.py"), Path("config/e003_nws_labels.json"))
    ]
    registration = archive.append(
        "experiment_protocol",
        protocol["experiment"],
        utcnow(),
        {},
        canonical({"protocol": protocol, "source_ids": source_ids}).encode(),
    )
    versions, sources, statuses, errors = defaultdict(list), [], Counter(), []
    try:
        for series, product in protocol["products"].items():
            cached = archive.latest("e003_nws_zip", product)
            if cached:
                body, rec = archive.body(cached), cached["id"]
            else:
                response, rec = client.get(
                    "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py",
                    {
                        "pil": product,
                        "sdate": protocol["request_start"],
                        "edate": protocol["request_end"],
                        "fmt": "zip",
                        "limit": 9999,
                        "order": "asc",
                    },
                    kind="e003_nws_zip",
                    key=product,
                )
                body = response.content
            sources.append(rec)
            with ZipFile(BytesIO(body)) as zipped:
                names = zipped.namelist()
                if len(names) >= 9999:
                    raise ValueError("Daily report query may be truncated")
                for entry_index, entry in enumerate(zipped.infolist()):
                    name = entry.filename
                    try:
                        raw_body = zipped.read(entry)
                        row = parse_climate_report(
                            raw_body.decode("ascii"),
                            name,
                            product,
                            protocol["start_day"],
                            protocol["end_day_exclusive"],
                            windows["series"][series]["standard_utc_offset_hours"],
                        )
                        statuses[row["status"]] += 1
                        if row["status"] == "complete":
                            versions[(series, row["day"])].append(
                                {
                                    **row,
                                    "source_record_id": rec,
                                    "zip_entry_index": entry_index,
                                    "raw_report_sha256": hashlib.sha256(raw_body).hexdigest(),
                                }
                            )
                    except (ValueError, UnicodeDecodeError) as exc:
                        errors.append(
                            {"series": series, "filename": name, "error": str(exc), "source_record_id": rec}
                        )
            LOG.info(
                "Read %s archived %s files; cumulative parse errors=%s", len(names), product, len(errors)
            )
        markets = [
            json.loads(line)
            for line in Path("reports/E002_development_markets.jsonl").read_text().splitlines()
        ]
        if any(not protocol["start_day"] <= m["day"] < protocol["end_day_exclusive"] for m in markets):
            raise ValueError("Out-of-development event reached source reconciliation")
        by_event, pages = defaultdict(list), {}
        for m in markets:
            by_event[m["event"]].append(m)
        rows = []
        for event, group in sorted(by_event.items()):
            m = group[0]
            values = {Decimal(str(g["expiration_value"])) for g in group if g["expiration_value"]}
            if len(values) != 1:
                raise ValueError("Missing or inconsistent exact event settlement value")
            observed = values.pop()
            if observed != observed.to_integral_value() or any(
                yes_at(g, observed) != bool(g["outcome"]) for g in group
            ):
                raise ValueError("Event value is noninteger or differs from binary results")
            settled = []
            for g in group:
                rec = g["source_record_id"]
                if rec not in pages:
                    raw = archive.db.execute("SELECT * FROM records WHERE id=?", (rec,)).fetchone()
                    pages[rec] = {r["ticker"]: r for r in archive.json(raw)["markets"]}
                value = pages[rec][g["ticker"]].get("settlement_ts")
                if value:
                    settled.append(parse_time(value))
            settlement = max(settled) if len(settled) == len(group) else None
            all_versions = sorted(versions[(m["series"], m["day"])], key=lambda r: r["issued_at"])
            available = [r for r in all_versions if settlement and parse_time(r["issued_at"]) <= settlement]
            selected = available[-1] if available else None
            simultaneous = [r for r in available if selected and r["issued_at"] == selected["issued_at"]]
            status = (
                "missing_report_at_settlement"
                if selected is None
                else ("match" if selected["maximum_f"] == observed else "mismatch")
            )
            if len({r["maximum_f"] for r in simultaneous}) > 1:
                status = "ambiguous_same_issue_versions"
            rows.append(
                {
                    "event": event,
                    "series": m["series"],
                    "day": m["day"],
                    "split": m["split"],
                    "exchange_value_f": int(observed),
                    "settled_at": iso(settlement) if settlement else None,
                    "status": status,
                    "selected_report": selected,
                    "versions": all_versions,
                }
            )
        report = {
            "generated_at": iso(utcnow()),
            "protocol_record_id": registration,
            "raw_source_record_ids": sources,
            "events": len(rows),
            "event_status_counts": dict(Counter(r["status"] for r in rows)),
            "report_parse_status_counts": dict(statuses),
            "errors": errors,
            "station_names": {
                s: sorted(
                    {r["station_name"] for (series, _), rs in versions.items() if series == s for r in rs}
                )
                for s in protocol["products"]
            },
            "scores_computed": False,
            "holdout_values_parsed": False,
            "limitations": [
                "NWS issue times are document timestamps, not independently recorded receipt times",
                "Latest report available at recorded exchange settlement is audited; all earlier/corrected versions retained",
                "A source mismatch must be resolved before this event trains a weather model",
            ],
        }
        dataset = "\n".join(canonical(row) for row in rows) + "\n"
        archive.append("research_dataset", "E003_nws_labels", utcnow(), {}, dataset.encode())
        archive.append("experiment_report", "E003_nws_labels", utcnow(), {}, canonical(report).encode())
        Path("reports/E003_NWS_labels.jsonl").write_text(dataset)
        Path("reports/E003_NWS_audit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    except (httpx.HTTPError, ValueError) as exc:
        archive.append(
            "experiment_failure", "E003_nws_labels", utcnow(), {}, canonical({"error": str(exc)}).encode()
        )
        raise
    finally:
        client.close()
        archive.close()


if __name__ == "__main__":
    main()
