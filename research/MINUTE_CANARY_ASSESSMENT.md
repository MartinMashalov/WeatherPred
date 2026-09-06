# E028 minute canary: coverage gate fails

The registered January–August minute-data proposal cannot proceed to its
242-day acquisition. The January canary contains no observations; the August
canary omits Miami entirely and supplies only missing markers for
Kendall–Tamiami. HTTP 200 means the requests returned successfully, not that
the requested observations exist.

This assessment reads the three receipts already acquired under protocol
**119562**, terminal report **122603**. It made **zero new network requests**,
converted **zero weather measurements** to numeric values, and computed no
weather errors, model fits or trading scores. Frozen source files and original
empty receipts remain unchanged. The [metadata evidence JSON](../evidence/E028_canary_assessment.json)
records request parameters, source hashes, actual receipts, station names,
CSV line references and separate sixty-minute masks for absent rows, numeric
syntax and the literal `M` marker. Its SHA256 is
`a1e518a125d82d1cfa075d0ead2a4c3ad7cdf2da34569abaa3bfe4e8a80367e3`.

## What the three responses actually contain

| Request, UTC | Response record | Bytes | Requested station-minutes | Rows | Rows with numeric syntax in all four fields |
| --- | ---: | ---: | ---: | ---: | ---: |
| January 1, 00:00–00:59, seven stations | 122584 | 52 | 420 | 0 | 0 |
| August 30, 12:00–12:59, seven stations | 122593 | 18,489 | 420 | 360 | 300 |
| January 1, 00:00–00:59, HST | 122602 | 52 | 60 | 0 | 0 |
| Total | — | 18,593 | 900 | 360 | 300 |

The two empty responses contain only the same CSV header. Their body hash is
`5a6c2f856cb12ebd611e2d302d57a66b97ff846fbfe1c1c7567e57ae650fb3cf`, but their
request records, receipt times and record hashes are distinct. Keep both
observations of missing coverage; shared bytes do not make them one request.

The August body hash is
`cda76374cb6116309a09387cadb07427b63369020d5783b4af499524c386947d`.
Its per-station inventory is:

| IEM / ICAO identity | Present minutes out of 60 | Numeric cells per variable | Literal `M` cells per variable |
| --- | ---: | ---: | ---: |
| MIA / KMIA | 0 | 0 | 0 |
| OPF / KOPF | 60 | 60 | 0 |
| FLL / KFLL | 60 | 60 | 0 |
| FXE / KFXE | 60 | 60 | 0 |
| PMP / KPMP | 60 | 60 | 0 |
| TMB / KTMB | 60 | 0 | 60 |
| HWO / KHWO | 60 | 60 | 0 |

Each present station has exactly one row at every minute from 12:00 through
12:59. Its station name exactly matches the archived directory. No duplicate
station-minute, unrequested identity, out-of-window timestamp, extra column
or unknown lexical field token was observed. Across the three requests,
**540 station-minutes are absent**, and another **60 present station-minutes
have all four fields marked `M`**. These are different missing-data states.
HST was not requested in August and must not be described as tested then.

## Schema and clocks

All three bodies have precisely these columns:

```text
station,station_name,valid(UTC),tmpf,dwpf,sknt,drct
```

The archived interface documents `tmpf`/`dwpf` in Fahrenheit, `sknt` in knots,
and `drct` as wind direction. There is no cloud field, instrument identifier,
quality-control flag, averaging-period field or provider publication timestamp
in this CSV. Numeric syntax alone does not establish physical validity or
quality. The `M` token is directly observed; a future parser should retain it
and produce a missing value, never zero. Its precise source reason and the
meaning of other possible missing tokens remain unverified. The archived IEM
help does not document every CSV missing convention; the NCEI source-format
reference discusses blanks and `[M]`, which is not a license to assume all
source and CSV conventions are identical.
[IEM download interface](https://mesonet.agron.iastate.edu/request/asos/1min.phtml),
raw **111827**; [NCEI source format](https://www.ncei.noaa.gov/pub/data/asos-onemin/td6406.txt),
raw **113884**.

`valid(UTC)` contains minute-resolution strings such as
`2026-08-30 12:00`, interpreted in UTC because both the request and header say
UTC. The actual August receipt was **September 6 at 19:46:46.357742 UTC**.
The HTTP `Date` header is a response date, not the first release of the
observations. First historical availability and earlier editions remain
unknown. Observing :00 and :59 is consistent with the requested interval;
it does not determine whether the API's exact end boundary is inclusive or
exclusive, since the request ends at :59:59 and no returned row lies on that
second. No revised request syntax was substituted.

## Why January is empty remains unresolved

The raw response provides no error explanation or station-level availability
metadata. The same station-list, variable, sampling, timezone and timestamp
syntax produces rows in August, so a globally unsupported request format is
not established. This does **not** rule out a date-specific service problem,
a missing source partition or missing observations. No one of those causes
can be asserted from these receipts.

The seven directory entries advertise `HAS1MIN=1`, but their general station
archive dates do not establish minute coverage for January or any specific
hour. HST is absent from that directory. The IEM overview says some stations
have minute data back to 2000; it does not promise these eight stations or
complete coverage. The backend's approximately 24-hour delay and overview's
18–36-hour-or-longer delay are product descriptions, not an explanation for
an empty eight-month-old hour. Nothing inspected establishes a rolling
retention cutoff or proves all of January is unavailable.
[IEM minute directory](https://mesonet.agron.iastate.edu/geojson/network/ASOS1MIN.geojson),
raw **113795**; [backend help](https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?help),
raw **113778**; [IEM overview](https://mesonet.agron.iastate.edu/ASOS/), raw **113797**.

The frozen proposal required all seven identities in **both** mandatory
canaries. January supplies none, and August supplies six. Therefore the gate
fails without changing its requirements. Dropping Miami, dropping TMB, treating
`M` as a number, or starting the old bulk plan in August would not repair the
registered January–August study. The recorded NCEI-via-IEM product also remains
separate from the Synoptic minute feed used by the contractual index; its
station identity and observation valid time do not establish an equivalent
canonical index or historical trading input.

## Proposed replacement availability probe, not executed

A new registration could authorize **six fixed one-hour requests**, all at
12:00–12:59:59 UTC. Preserve the original three responses and retain every new
response, including empty or failed ones. No new station is chosen using
weather errors.

| Date | Requested IDs | Purpose |
| --- | --- | --- |
| January 1 | Original seven | Compare another hour on the same date, away from the year boundary |
| January 15 | Original seven | Test a fixed interior January date |
| June 1 | Original seven | Check the beginning of the previously proposed summer context |
| July 1 | Original seven | Check a fixed summer month boundary |
| August 1 | Original seven | Check the next fixed summer month boundary |
| August 30 | Original seven plus HST | Check the missing eighth airport in an hour where five peers previously had numeric fields; retain a distinct later edition |

The evidence JSON enumerates these exact URLs and parameters. The limits are
**six physical attempts, no retries, 1 MiB payload plus 16 KiB overflow detection
per response, 6,389,760 total read bytes, five minutes wall time, 30-second
request timeout and at least one second between starts**. The calendar contains
2,580 intended station-minutes; actual availability is unknown. No adaptive
extra dates, alternative products or bulk requests follow automatically.
No request in this proposal has been made by this assessment.

Report every station and field at every probe, including MIA, TMB and HST
failures. If coverage is still insufficient, retain the failed data plan and
propose another documented source route separately. Even successful probes
would justify only another bounded coverage manifest, not complete-month
claims or automatic model training. Freeze any revised study dates and
missingness rules in a separate protocol before fitting or scoring.

## Verification performed

A read-only script checked all registered canary source hashes, **13 referenced
records' body hashes, record hashes and immediate previous links**, each
request/receipt/terminal-report binding, station-directory names, exact CSV
schemas, **360 station-minute identities** and **1,440 lexical field classes**.
It did not traverse unrelated archive bodies. Actual output:

```text
physical_attempts: 3; http_200: 3; received_bytes: 18593
expected_station_minutes: 900; observed_station_minutes: 360
numeric_complete_station_minutes: 300; absent_station_minutes: 540
numeric_syntax_cells: 1200; literal_M_cells: 240
original_bulk_gate_passes: false
new_network_requests: 0; weather_or_trading_scores: 0
```
