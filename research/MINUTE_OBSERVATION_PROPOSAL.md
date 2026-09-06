# A bounded minute-observation training proposal

**The free IEM minute archive can supply a training-data canary, but it cannot
recreate historical live inputs to the Miami index.** Its own backend
documentation identifies the product as delayed **NCEI** data, distinct from
the minute feed through MADIS. In addition, its minute-station directory
contains seven of the eight requested airports; Homestead (`KHST`) is absent.
Keep that limitation visible rather than selecting another airport after
examining forecast errors.

This round, dated **6 September 2026**, fetched documentation and metadata
only: **6/12 permitted GETs**, five HTTP 200 responses and one retained HTTP
404 for an unverified repository source-code path. No June, July, August,
protected-quarter or other measurement body was downloaded. No model was
fitted or scored. E024 and its annual selection family remain unchanged;
E027 owns the prospective live collection.

Metadata protocol **113759** precedes these requests. Proposal artifact
**116196** contains the finite future request list and explicit
`measurement_acquisition_authorized=false` flag. The
[machine-readable proposal](../evidence/minute_observation_proposal.json) has
SHA256 `33155e9a0c91cf3d4c267e0ad206013ec7d9f32a81dc1498f4d1497a021ee535`.
The [metadata request manifest](../evidence/minute_observation_requests.json)
retains every receipt and failure. A separate registration is required before
executing any proposed measurement request.

## Identity: the same airport does not establish the same data product

The five contractual component identities come from the already archived
TEMPH methodology and calibration history. The additional three airports were
fixed as the nearest nonmembers in the previous geographic census, not chosen
using temperature errors. [Source-feasibility report](INNOVATION_SOURCE_FEASIBILITY.md).

| Role | Kalshi primary ID | Airport ICAO | IEM request ID | Listed in IEM minute directory? |
| --- | --- | --- | --- | --- |
| Index component airport | KMIA1M | KMIA | MIA | Yes |
| Index component airport | KOPF1M | KOPF | OPF | Yes |
| Index component airport | KFLL1M | KFLL | FLL | Yes |
| Index component airport | KFXE1M | KFXE | FXE | Yes |
| Index component airport | KPMP1M | KPMP | PMP | Yes |
| Nearest nonmember, 20.0 km from KMIA | — | KTMB | TMB | Yes |
| Next nonmember, 24.7 km | — | KHWO | HWO | Yes |
| Third nonmember, 33.7 km | — | KHST | HST | **No** |

The current `ASOS1MIN` directory returns **917 entries**, with generation time
`2026-09-06T18:48:54Z`. The seven matches have `HAS1MIN=1`, the expected
coordinates and timezone `America/New_York`. Their underlying metadata still
names `FL_ASOS`; `ASOS1MIN` is a selection of stations, not evidence of a
separate instrument. General `archive_begin` dates such as Miami's 1932 start
are not first dates of minute coverage. No per-station minute completeness was
established by the directory. [IEM minute directory](https://mesonet.agron.iastate.edu/geojson/network/ASOS1MIN.geojson),
raw **113795**.

Seven directory matches establish a request mapping, not equivalence with
Synoptic's `*1M` stream. They do not establish identical averaging, precision,
missing-value treatment, quality control, source editions or receipt times.
In particular, the index's historical administrator receipts cannot be inferred
from IEM observation timestamps. Do not average IEM values and name the result
a historical canonical index: the contractual source, eligible configuration,
source checks and first canonical edition are also required.

## Product, cadence and availability

The exact interface is
`https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py`.
Its help page supports station lists, variable lists, start/end timestamps,
IANA timezone, sampling and output format. It identifies an approximately
24-hour delay from NCEI collection. IEM's overview describes daily updates and
an **18–36-hour or longer** delay. These are advertised processing delays,
not guaranteed historical publication times.
[Backend help](https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?help),
raw **113778**; [IEM overview](https://mesonet.agron.iastate.edu/ASOS/), raw
**113797**.

The already captured minute-download page, raw **111827**, advertised archive
availability through **2 September 2026**. It offers `tmpf` and `dwpf` in
Fahrenheit, `sknt` in knots and `drct` for wind direction. Its sampling option
selects timestamps; it does not calculate interval averages. `sample=1hour`
would therefore not reproduce an hourly mean. Cloud-layer coverage is not
offered in the advertised variable list; do not promise cloud features from
this acquisition. [IEM minute-download interface](https://mesonet.agron.iastate.edu/request/asos/1min.phtml).

This product also differs from IEM's routine METAR/SPECI interface. Its request
has no documented routine/SPECI/HFMETAR type selector. Do not copy a routine
report's `report_type` parameter into the minute request or label these rows
METARs. The routine service's “1,000 station years” request allowance must not
be imported as an established limit for the minute service. The minute help
examined here did not state a numerical row or station-year limit; our own
limits below are intentionally small.

NOAA separately describes MADIS one-minute observations as binary messages
outside the METAR standard. Its live processing handles the current and prior
hour every five minutes; later recovery can reprocess observations one, seven
and thirty-five days old. Those timings describe **MADIS**, not the NCEI/IEM
pipeline used here. They demonstrate why an observation valid time must not be
treated as proof of first availability. A live MADIS route could be investigated
separately, without assuming the existing NCEI decoder is interchangeable.
[NOAA MADIS product documentation](https://madis.ncep.noaa.gov/madis_OMO.shtml),
raw **113875**.

The NCEI page-two format documentation describes fixed-width source records
and numeric missing values represented by blanks or sometimes `[M]`. It also
acknowledges incomplete format documentation and contains an inconsistent
minute-increment description. Its source conventions do not establish the
exact IEM CSV missing token. The canary must verify that serialization before
bulk parsing; never interpret an unrecognized token, zero or a sentinel as a
real temperature by default.
[NCEI format documentation](https://www.ncei.noaa.gov/pub/data/asos-onemin/td6406.txt),
raw **113884**.

## Finite canary: three requests, no fitting

Freeze the request JSON, parser source, metadata/source hashes and transport
budget in a new acquisition record first. The following requests are
**specified but not executed**. All use:

```text
vars=tmpf,dwpf,sknt,drct
sample=1min
tz=UTC
what=download
delim=comma
gis=no
```

| Order | Station parameter | `sts` | `ets` | Purpose |
| --- | --- | --- | --- | --- |
| 1 | `MIA,OPF,FLL,FXE,PMP,TMB,HWO` | `2026-01-01T00:00:00Z` | `2026-01-01T00:59:59Z` | Oldest requested-period schema and coverage canary |
| 2 | Same seven IDs | `2026-08-30T12:00:00Z` | `2026-08-30T12:59:59Z` | Latest requested-day canary, fixed before values |
| 3 | `HST` | `2026-01-01T00:00:00Z` | `2026-01-01T00:59:59Z` | Explicit check of the directory's missing eighth airport |

The second-resolution end timestamp is within each requested hour. Its support
and endpoint semantics must be verified by the canary; do not silently change
the timestamp syntax if the service rejects it. Retain every response, even an
empty CSV, HTML error or HTTP rejection. Each seven-station request has at most
**420 distinct intended station-minutes**, and the HST request at most sixty:
**900 intended slots total**, before counting duplicate editions. These are
calendar counts, not claims that those rows exist.

The canary has a one-MiB per-response ceiling and at most nine physical
attempts including two transient retries per logical request. Minimum request
start spacing is one second. Do not retry permanent 4xx failures. Pause on
429 using the provider's retry instruction within the total deadline; if that
cannot be honored within the budget, stop cleanly. Persist a stop-file check
and request ledger. No automatic station replacement or alternate data product.

Advance to the proposed bulk phase only if both seven-station canaries parse
unambiguously, all seven requested identities are present at least once in each
hour, and timestamps, units and missing tokens have a documented interpretation.
This is a **schema/availability gate**, not a completeness or forecasting test.
Any failed mandatory station stops this proposal pending an explicit new data
plan; do not drop it to make the gate pass. HST's dedicated failure stays a
reported limitation. If its canary unexpectedly returns correctly identified
minute data, it may enter the predeclared eight-station branch, with the expanded
request-list hash frozen before bulk acquisition. No weather error selects
that branch.

## Conditional larger manifest and hard resource limits

The JSON artifact enumerates **242 daily requests**, January 1 through
August 30, 2026 inclusive. Each requests the seven confirmed IDs, four variables
and all minute timestamps from `00:00:00Z` through `23:59:59Z`. If the fixed
HST canary succeeds, add HST to every daily request; do not add other airports.
HST remains in the coverage inventory with an explicit unsupported status if
its canary fails. Failure to acquire a whole requested day is different from
an acquired empty response.

| Quantity | Fixed limit or calendar count |
| --- | ---: |
| Canary plus daily logical requests | 245 |
| Physical attempts including two retries each | 735 maximum |
| Intended minute slots, seven stations | 2,439,360 |
| Intended minute slots, conditional eight stations | 2,787,840 |
| Daily response ceiling | 4 MiB |
| Entire measurement-transfer ceiling, including failures/retries | 1 GiB |
| Wall-clock limit | 30 minutes |
| Parser CPU limit | 10 minutes |
| Process memory ceiling | 512 MiB |
| Minimum free disk before starting | 4 GiB |

There is no measured CSV bytes-per-row estimate yet. At an illustrative
100–200 bytes per row, the seven-station panel would be approximately
244–488 MB; this is capacity planning, not an acquisition result. Use the actual
canary byte count to report a revised estimate without increasing the frozen
limits. The transport must stop reading when a byte ceiling binds, retain the
partial response as a failed receipt and never parse it as complete data.
A helper that first downloads an unlimited response and only then checks its
size does not enforce this limit.

The archive must retain original bytes, request/response times, URL and
parameters, HTTP status, available headers, retry index and SHA256. Atomic
per-day checkpoints make an exact-source resume possible; a completed day is
not refreshed into a newer edition. Exceeding a limit ends acquisition with
explicit missing days, rather than a partial dataset labeled complete. This
proposal does not launch the job and does not duplicate E027's live feeds.

## Normalized training rows and missingness

Retain source-native strings before numeric conversion. Proposed rows contain:

| Fields | Interpretation |
| --- | --- |
| `product`, `iem_station_id`, `icao_id`, `index_component_airport` | Explicit NCEI-via-IEM product and verified airport mapping; not a Kalshi canonical label |
| `source_valid_time`, `valid_at_utc`, `requested_timezone` | Original timestamp plus normalized UTC, with `tz=UTC` preserved |
| `local_date`, `local_time`, `local_fold` | Derived `America/New_York` display calendar; UTC remains the identity |
| `tmpf_raw`, `dwpf_raw`, `sknt_raw`, `drct_raw` | Original values or missing tokens |
| Parsed numeric values and `field_status` | Finite values with explicit source units, or null with an explanation |
| `raw_record_id`, `raw_row_index`, `body_sha256` | Exact measurement receipt and source row, once acquisition is authorized |
| `request_started_at`, `available_at` | This project's real future download times |
| `provider_publication_at`, `provider_revision_at` | Null unless the source actually supplies them; never invented from valid time |
| `historical_first_availability_verified` | False |
| `duplicate_group`, `parse_status`, `coverage_status` | Explicit duplicate, conflict, missing and rejection evidence |

Read and gate station/timestamp identity before measurement fields. Only rows
inside `[2026-01-01T00:00:00Z, 2026-08-31T00:00:00Z)` are eligible for model
exports. An unexpected protected date is a source-scope failure, not a row to
inspect and then discard. Out-of-request times, station changes, unexplained
columns, non-finite values and unknown missing tokens fail their parsing checks.
Do not infer temperature units from magnitude or manufacture missing wind/cloud
values. Empty and absent fields need separate statuses.

Use `(product, station, UTC minute)` for duplicate identity. Exact duplicate
rows retain all source indexes; conflicting versions are quarantined together
and do not resolve to whichever value helps a model. Different receipts with
different bytes remain distinct editions. This preserves changes observed by
our collector; it cannot recover overwritten provider history. Missing minutes
remain missing, with expected versus observed slot counts for **all eight**
requested airports. A known directory gap is not a valid zero-degree reading.

UTC requests avoid a repeated local hour becoming one key. The proposed interval
does contain the spring daylight-saving transition, so test local conversion
and missing UTC rows separately. Minute cadence does not establish whether
the underlying variable is instantaneous or averaged. Record that limitation
until source/product comparison establishes it. The result is retrospective
training material, with download-time availability, not a historical live-feed
replay.

## Challenge the upwind hypothesis with three fixed candidates

The following is a **future mechanism-study proposal**, not a fitted model or
an added annual trading policy. Its first question is whether local spatial
transport helps predict the recorded station temperature, rather than whether
minute observations would have been accessible to a historical trader.
Before scoring, freeze the final data manifest, source-compatible feature
construction, dates and code in a separate model protocol.

Use all five component airports as separate temperature targets, hourly target
times and a fixed sixty-minute forecast horizon. Proposed chronology is
January–June training, July 1–14 calibration, and July 15–August 30 development;
all intervals are 2026. Insufficient context and missing labels follow one
predeclared shared case grid. These would remain development results, not a
new untouched final test, and would not satisfy the trading validation gates
by themselves. No error-selected airport subset is permitted.

| Candidate | Fixed construction |
| --- | --- |
| C0: local baseline | One ridge regression with penalty 10, fixed station/time-of-day indicators, current temperature/dew point, and the previous hour's temperature change. No neighborhood input. |
| C1: transport feature | Same C0 plus one normalized upwind-neighbor temperature-change feature, its age and a missing-feature indicator. Use only the fixed TMB/HWO/HST roster; absent HST stays missing. |
| C2: wrong-wind control | Same inputs, feature dimension, ridge penalty, stations, speed, time grid and missing-data policy as C1, but rotate each wind direction by exactly 180 degrees before constructing the neighbor weights. |

Cap the combined fits at ten CPU minutes with no learning-rate, neighborhood,
penalty or horizon search. All three must produce results on the same registered
cases; unavailable neighborhood information maps to the registered missing
feature, not deletion of an inconvenient target. C2 is a falsification control,
not a candidate for promotion if it happens to make a better return plot.

Meteorological direction reports where wind comes **from**. Convert it into a
motion vector before projecting onto the source-to-target direction. Positive
projection defines a proposed upwind edge. Reject calm/variable/missing wind
under fixed rules; do not assign it the most useful direction. Let travel time
be distance divided by the positive projected speed, clipped to the registered
30–360-minute range. For target `T` and decision `D`, use a source time no later
than `min(D, T - travel_time)`, retaining its actual age. Never obtain a future
upstream observation to complete the transport feature.

Report paired day-level errors and all station contributions for C0/C1/C2.
If reversed wind performs similarly to or better than actual wind, the proposed
transport mechanism is unsupported: the apparent gain may be regional
co-movement, diurnal structure or feature availability. Freeze a practical
improvement threshold before evaluation; do not choose it after inspecting the
three curves. Even C1 beating both alternatives would be evidence consistent
with transport, not proof of a causal weather model or a profitable strategy.
Spatial weather modeling is established prior art, including
[GraphCast](https://arxiv.org/abs/2212.12794); this pilot is a small
application-specific falsification test.

There is also a **deployment gap** to test before using any candidate:
historical IEM minute wind and nonmember temperatures may be much denser than
the public METAR observations available to E027. Fit-time NCEI values must not
be presented as if a live system receives them immediately. A source-matched
resampling/delivery experiment and prospective calibration are required.
No present evidence establishes that that gap can be bridged. The minute
archive supplies no cloud-layer input and no first-publication history.

## What the next authorized work could answer

1. **Can this source supply the required training observations?** The three
   canaries can establish serialization, timestamps, airport coverage and the
   HST limitation; the daily census can quantify missingness. Neither proves
   historical live availability.
2. **Does the proposed transport direction carry additional predictive
   information?** The fixed local/actual-wind/reversed-wind comparison can reject
   a weak mechanism without swapping stations or tuning many models.
3. **Is that information usable with the actual live source?** Only a separate
   bridge to the captured component/METAR cadence and actual receipts can answer
   this. Trading profitability still additionally requires exact settlement
   mapping, calibrated probabilities, executable depth, fees and capital tests.

The next scout round should specify that **training-to-live input bridge** from
existing IEM documentation and E027's already permitted receipt schema. It
should identify which inputs can actually be populated and the smallest
source-matched baseline, rather than starting another collector or training a
larger model before the data mismatch is resolved.

## Metadata evidence and scope checks

| Retrieved source | Raw ID | HTTP | Body SHA256 prefix |
| --- | ---: | ---: | --- |
| IEM minute backend help | 113778 | 200 | 905e74cf49b5 |
| IEM ASOS1MIN station directory | 113795 | 200 | 255a7c2b84ed |
| IEM ASOS overview | 113797 | 200 | 952d563d7439 |
| NOAA MADIS one-minute product documentation | 113875 | 200 | 53a50567893b |
| NCEI page-two format | 113884 | 200 | 1612c4847ace |
| Unverified IEM repository source path | 114473 | 404 | d5558cd419c8 |

The failed source-code lookup is retained; no claim about the backend's
uninspected implementation is made. Current documentation supports the request
proposal, and the canary must establish actual boundary and CSV behavior.
The [metadata-only wrapper](probes/minute_observation_sources.py) exposes only
named documentation/directory routes and a twelve-attempt ceiling. No raw
observation route without `help` was called in this round. Full source IDs,
headers and receipts are preserved in the request manifest; no historical
weather or annual evaluation files were opened.
