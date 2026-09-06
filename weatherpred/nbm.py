"""Read NBM fixed-width station cards without conflating their extrema windows."""

import re
from datetime import UTC, datetime, timedelta
from itertools import pairwise

HEADER = re.compile(
    rb"(?m)^ ([A-Z0-9]{4,6}) +NBM V([0-9.]+) NBS GUIDANCE +"
    rb"(\d{1,2})/(\d{2})/(\d{4}) +(\d{2})(\d{2}) UTC[^\n]*\n"
)


def station_cards(body, wanted):
    """A range may contain neighboring cards; identify exact station headers."""
    headers = list(HEADER.finditer(body))
    result = {}
    for index, header in enumerate(headers):
        station = header[1].decode()
        if station not in wanted:
            continue
        if station in result:
            raise ValueError("Duplicate station card")
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        raw = body[header.start() : end]
        # A complete NBS land card ends with SOL, followed by the blank separator.
        # This rejects a truncated byte range instead of silently accepting a prefix.
        if re.search(rb"(?m)^ SOL[^\n]*\n {20,}\n", raw) is None:
            raise ValueError("Incomplete station card")
        runtime = datetime(
            int(header[5]), int(header[3]), int(header[4]), int(header[6]), int(header[7]), tzinfo=UTC
        )
        lines = raw.decode("ascii").splitlines()
        fields = {}
        for name in ("UTC", "FHR", "TMP", "TSD", "TXN", "XND"):
            matches = [line for line in lines if line.startswith(" " + name + " ")]
            if len(matches) != 1 or len(matches[0]) != 74:
                raise ValueError("Missing, duplicate or malformed NBS field: " + name)
            cells = [matches[0][5 + i * 3 : 8 + i * 3].strip() for i in range(23)]
            fields[name] = [None if not value or value == "999" else int(value) for value in cells]
        rows = []
        for i, hour in enumerate(fields["FHR"]):
            if hour is None or hour < 0:
                raise ValueError("Missing or negative forecast horizon")
            valid = runtime + timedelta(hours=hour)
            if valid.hour != fields["UTC"][i]:
                raise ValueError("Forecast-hour and UTC columns disagree")
            rows.append(
                {
                    "valid_at": valid.isoformat(),
                    "forecast_hour": hour,
                    **{name.lower(): fields[name][i] for name in ("TMP", "TSD", "TXN", "XND")},
                }
            )
        if any(a["forecast_hour"] >= b["forecast_hour"] for a, b in pairwise(rows)):
            raise ValueError("Forecast hours are not strictly increasing")
        result[station] = {
            "station": station,
            "version": header[2].decode(),
            "runtime": runtime.isoformat(),
            "rows": rows,
            "relative_byte_offset": header.start(),
            "raw_card_bytes": len(raw),
        }
    return result
