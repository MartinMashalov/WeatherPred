"""NBH v2: accept one verified trailing UTC space; frozen v1 remains unchanged."""

import hashlib
import re
from datetime import UTC, datetime, timedelta

HEADER = re.compile(
    rb"(?m)^ ?([A-Z0-9]{4,6}) +NBM V([0-9.]+) NBH GUIDANCE +"
    rb"(\d{1,2})/(\d{1,2})/(\d{4}) +(\d{2})(\d{2}) UTC[^\r\n]*\r?\n"
)
SEPARATOR = re.compile(rb"\r?\n[ \t]*\r?\n")


def parse_cards(body, wanted, *, expected_run_ms=None, body_offset=0, versions=("5.0",)):
    """Read complete wanted cards from a full object or a verified byte range.

    Missing TMP lines/cells remain explicit nulls. Malformed timestamps, widths,
    duplicate cards and incomplete card separators are errors, never imputed.
    """
    headers = list(HEADER.finditer(body))
    cards = {}
    for i, header in enumerate(headers):
        station = header[1].decode("ascii")
        if station not in wanted:
            continue
        if station in cards:
            raise ValueError("Duplicate NBH station card: " + station)
        boundary = headers[i + 1].start() if i + 1 < len(headers) else len(body)
        separator = SEPARATOR.search(body, header.end(), boundary)
        if separator is None:
            raise ValueError("Incomplete NBH card separator: " + station)
        raw = body[header.start() : separator.end()]
        version = header[2].decode("ascii")
        if version not in versions:
            raise ValueError("Unregistered NBH version: " + version)
        runtime = datetime(
            int(header[5]),
            int(header[3]),
            int(header[4]),
            int(header[6]),
            int(header[7]),
            tzinfo=UTC,
        )
        run_ms = int(runtime.timestamp()) * 1000
        if runtime.minute != 0 or (expected_run_ms is not None and run_ms != expected_run_ms):
            raise ValueError("NBH runtime differs from registered cycle")
        fields, missing = {}, []
        for name in ("UTC", "TMP", "TSD"):
            lines = [line for line in raw.splitlines()[1:] if re.match(rb"^ ?" + name.encode() + rb" ", line)]
            if len(lines) > 1:
                raise ValueError("Duplicate NBH field: " + name)
            if not lines:
                if name == "UTC":
                    raise ValueError("Missing NBH UTC field")
                fields[name] = [None] * 25
                missing.append("missing_" + name.lower() + "_element")
                continue
            # Retained v1 source111293 has an81-byte UTC row: 25 fixed-width
            # cells plus exactly one trailing ASCII space. TMP/TSD remain80.
            # Accept only that UTC suffix, never strip arbitrary whitespace.
            suffix = rb" ?" if name == "UTC" else b""
            match = re.fullmatch(rb" ?" + name.encode() + rb" (.{75})" + suffix, lines[0])
            if match is None:
                raise ValueError("Malformed NBH field width: " + name)
            values = []
            for j in range(25):
                cell = match[1][3 * j : 3 * (j + 1)].strip()
                if not cell or cell == b"-99":
                    values.append(None)
                elif re.fullmatch(rb"-?\d{1,3}", cell) is None:
                    raise ValueError("Malformed NBH numeric field: " + name)
                else:
                    number = int(cell)
                    if not -98 <= number <= 998:
                        raise ValueError("Out-of-format NBH numeric value: " + name)
                    values.append(number)
            fields[name] = values
        rows = []
        for j in range(25):
            valid = runtime + timedelta(hours=j + 1)
            if fields["UTC"][j] != valid.hour:
                raise ValueError("NBH UTC columns disagree with hourly runtime progression")
            rows.append(
                {
                    "forecast_hour": j + 1,
                    "valid_ms": int(valid.timestamp()) * 1000,
                    "tmp_f": fields["TMP"][j],
                    "tsd_f": fields["TSD"][j],
                }
            )
        cards[station] = {
            "station_id": station,
            "version": version,
            "run_ms": run_ms,
            "byte_offset": body_offset + header.start(),
            "raw_card_bytes": len(raw),
            "raw_card_sha256": hashlib.sha256(raw).hexdigest(),
            "missing_elements": missing,
            "rows": rows,
        }
    return cards


def merged_ranges(offsets, size, radius):
    """Return ordered inclusive ranges; nearby station requests share bytes."""
    if not isinstance(size, int) or size <= 0 or not isinstance(radius, int) or radius <= 0:
        raise ValueError("Invalid object size or range radius")
    ranges = []
    for offset in sorted(offsets):
        if not isinstance(offset, int) or not 0 <= offset < size:
            raise ValueError("Station offset outside object")
        low, high = max(0, offset - radius), min(size - 1, offset + radius - 1)
        if ranges and low <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(high, ranges[-1][1]))
        else:
            ranges.append((low, high))
    return ranges
