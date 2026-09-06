"""Original NWS daily climate versions with issue-date and complete-period gates."""

import re
from datetime import UTC, date, datetime

from weatherpred.contracts import nws_standard_day

SUMMARY = re.compile(r"THE\s+(.+?)\s+CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})", re.DOTALL)
MONTHS = [
    "JANUARY",
    "FEBRUARY",
    "MARCH",
    "APRIL",
    "MAY",
    "JUNE",
    "JULY",
    "AUGUST",
    "SEPTEMBER",
    "OCTOBER",
    "NOVEMBER",
    "DECEMBER",
]


def parse_climate_report(text, filename, product, start_day, end_day, standard_offset):
    summary = SUMMARY.search(text)
    if summary is None:
        raise ValueError("No unambiguous daily climate summary date")
    day = date(int(summary[4]), MONTHS.index(summary[2]) + 1, int(summary[3]))
    if not start_day <= day.isoformat() < end_day:
        return {"status": "outside_development_dates"}
    # All date gates precede reading the observed temperature.
    name = re.fullmatch(re.escape(product) + r"_(\d{12})\.txt", filename)
    if name is None or re.search(r"(?m)^" + re.escape(product) + r"\s*$", text) is None:
        raise ValueError("Filename or body product identifier mismatch")
    issued = datetime.strptime(name[1], "%Y%m%d%H%M").replace(tzinfo=UTC)
    indexed_issue = issued
    header = re.search(r"(?m)^CDUS\d{2} [A-Z]{4} (\d{6})(?: (CC[A-Z]))?\s*$", text)
    if header is None:
        raise ValueError("Filename issue time conflicts with WMO header")
    if header[1] != issued.strftime("%d%H%M"):
        if not header[2]:
            raise ValueError("Filename issue time conflicts with WMO header without correction marker")
        candidates = []
        for delta in (-1, 0, 1):
            absolute_month = issued.year * 12 + issued.month - 1 + delta
            try:
                candidates.append(
                    datetime(
                        absolute_month // 12,
                        absolute_month % 12 + 1,
                        int(header[1][:2]),
                        int(header[1][2:4]),
                        int(header[1][4:]),
                        tzinfo=UTC,
                    )
                )
            except ValueError:
                continue
        corrected = min(candidates, key=lambda t: abs((t - issued).total_seconds()))
        if not 0 <= (corrected - issued).total_seconds() <= 86400:
            raise ValueError("Correction timestamp requires manual date reconciliation")
        issued = corrected
    _, end = nws_standard_day(day, standard_offset)
    metadata = {
        "day": day.isoformat(),
        "issued_at": issued.isoformat(),
        "station_name": " ".join(summary[1].split()),
        "filename": filename,
        "archive_index_issue_at": indexed_issue.isoformat(),
        "correction_marker": header[2],
    }
    if re.search(r"VALID (?:TODAY )?AS OF", text) or issued < end:
        return {**metadata, "status": "partial_day_report"}
    temperature = re.search(r"TEMPERATURE \(F\)(.*?)(?:PRECIPITATION|DEGREE DAYS)", text, re.DOTALL)
    if temperature is None:
        raise ValueError("Missing temperature section")
    maximum = re.search(r"(?m)^\s+MAXIMUM\s+(-?\d+|MM)(?=\s|R)", temperature[1])
    if maximum is None:
        raise ValueError("Missing or malformed daily maximum")
    return {
        **metadata,
        "status": "missing_maximum" if maximum[1] == "MM" else "complete",
        "maximum_f": None if maximum[1] == "MM" else int(maximum[1]),
    }
