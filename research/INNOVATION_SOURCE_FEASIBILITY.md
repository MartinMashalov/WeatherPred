# Public inputs for the Miami index and nearby weather

Source-feasibility round, **6 September 2026**. The exact five component
temperatures are accessible through the public Kalshi detailed index feed.
Public Aviation Weather Center (AWC) reports add wind, cloud and weather fields
for nearby airports. This supports a small prospective source/forecast study;
it does **not** establish a forecasting improvement, a latency advantage over
traders, or an executable profit.

There were **18 physical GETs**, including all attempts and redirects, against
a limit of twenty. Seventeen responses were HTTP 200; the anonymous Synoptic
request returned 401 and that API route was stopped. No token, paid service,
model weight, protected observation, annual price or outcome was acquired.
No weather or trading score was computed. The nearby station selection used
coordinates and directory status, not weather outcomes or past trading profit.

Protocol **111428** precedes these requests. Summary **112149** retains source
IDs, hashes, timing and coverage. The exact URLs, parameters, headers and
receipts are in [the request manifest](../evidence/innovation_source_requests.json);
its SHA256 is
`24ae954c2cc1c2640034a230018d5e32fdcfd6c378202d9f91998a859937f687`.
The [compact source summary](../evidence/innovation_source_summary.json) and
[geographic census](../evidence/innovation_nearby_directory.json) contain no
forecast or strategy results. Existing methodology and calibration archives
were reused before making new requests.

## Exact membership and source identity

The primary contract is TEMPH/MIAWINDEX, not an arbitrary Miami airport
temperature. The archived methodology is raw **1900**, SHA256
`83ccfd50b032e9b1f350890decd9e16917b6aa0f35670f5a2b75547156837025`.
Its source-selection page was checked in both extracted text and a rendered
image. [Official methodology, Appendix B](https://assets.kalshi.com/contract_terms/TEMPH.pdf).

| Index primary ID | Airport / genuine METAR fallback ID | Base weight | Latest returned offset, °C |
| --- | --- | ---: | ---: |
| KMIA1M | Miami International / KMIA | 0.20 | 0 |
| KOPF1M | Miami–Opa Locka / KOPF | 0.20 | −0.75 |
| KFLL1M | Fort Lauderdale–Hollywood / KFLL | 0.20 | 0 |
| KFXE1M | Fort Lauderdale Executive / KFXE | 0.20 | 0 |
| KPMP1M | Pompano Beach Airpark / KPMP | 0.20 | +0.375 |

The primary is Synoptic's HF-ASOS network **258**, at the exact event minute.
The sole fallback is the same airport's genuine official ASOS METAR/SPECI,
at most 75 minutes old; generated HFMETAR is excluded. KHWO and KTMB are
explicitly excluded from index membership because of insufficient
high-frequency coverage. KDJT is a spare, requiring a prospective amendment
before becoming a member. These airports can still be explanatory inputs to
a forecast without becoming settlement sources.

The index administrator's receipt deadline is event minute plus 300 seconds.
`normal` means all exact primary observations contributed; `degraded` can
involve an absent member, fallback or quality-control substitution.
At least four members and 0.80 base weight are required; otherwise the minute
is `unavailable`, with no value. Ordinary late observations do not revise a
canonical point. Exceptional corrections have separate versions, and the
contract's initially published canonical value governs ordinary settlement.
Raw provider flags and source-specific temporal/spatial checks are part of the
methodology, beyond checking whether a temperature is finite.

Calibration endpoint raw **111435** has the same body SHA256 as earlier raw
**3582**, `148e0b48624be6820f43d4752c9f3c0299802eb6e1302413783e0d07565c4e16`.
It returns version history, `published_at_ms`, `effective_at_ms`, station
weights/offsets and `city_reference_c`. The latest returned version is
`miami-temperature-v1.0-cal-20260831`, with reference −0.075°C and the offsets
above. A previously published quality-control repair has an effective time
before its publication time: gating only on effective time would introduce
information from the future.

For accepted members `A`, calculate in Celsius:

`B = sum_all_members(w_i * b_i)`

`I_C = sum_i_in_A(w_i * (x_i - b_i + B)) / sum_i_in_A(w_i)`.

When all five equal-weight members contribute, the offset terms cancel and
the result is their arithmetic mean. With a missing member, that cancellation
does not generally hold. Preserve the correct configuration and final
Fahrenheit rounding rather than carrying the current offsets into older
minutes. [Public calibration endpoint](https://external-api.kalshi.com/trade-api/v2/live_data/weather/miami/calibrations).

## Access and clock semantics

| Source | Access established here | Timing and edition limitation |
| --- | --- | --- |
| Kalshi detailed index | Two anonymous GETs, raw 111433 / 111833 | Event minute and administrator station receipts are present; our HTTP receipt is a separate availability time. |
| Kalshi calibration history | Anonymous GET, raw 111435 | Both publication and effective times must pass the decision gate; actual receipt must also precede a prospective decision. |
| Direct Synoptic metadata | HTTP 401, raw 111616 | Documentation requires a token. No credential search, paid access or repeated anonymous data requests. |
| AWC station information / METAR | Anonymous GETs, raw 111606 / 111609 / 111836 | Observation, report and AWC receipt clocks are available, plus our actual HTTP receipt; the latter governs what our system had. |
| NWS observations | Anonymous GET, raw 111823 | Observation timestamps and variable quality fields exist; this response has no provider-receipt field. |
| IEM airport directory / downloads | Directory and documentation accessed | Historical availability and current publication lag differ across products; no historical observation files downloaded. |
| NCEI one-minute directory | Directory/readme accessed | The inspected directory lists annual data only through 2022. This does not establish recent station coverage. |

**Detailed index.** Use
`/trade-api/v2/live_data/weather/miami?last_sec=600&detailed=true`.
The response contains `city`, `config_version`, `units` and `timeseries`.
Points contain `t` in UTC milliseconds, `status`, optional `v`, optional
`contributors`, and `stations`. In these samples each station has
`station_id`, `source`, `code`, `received_at_ms` and `temp_f`.
The station receipt is the administrator's clock, not our download time or
a promised first-publication timestamp.
[Public detailed endpoint](https://external-api.kalshi.com/trade-api/v2/live_data/weather/miami?last_sec=600&detailed=true).

Both six-point samples contained four normal and two incomplete points, with
one **120-second gap** among otherwise 60-second timestamps. Therefore a
600-second request must not be interpreted as ten complete minute rows.
An absent timestamp differs from an explicit `unavailable` status; neither is
a license to interpolate a settlement label. Station codes were `ok` or
`pending`. Pending values are inputs that have not completed canonical source
processing. They must not become labels. Observed administrator receipt lags
were approximately **135–182 seconds** across the two small samples; the
latest event minute was about **181–191 seconds** old at our receipts.
These samples do not measure a guaranteed lead over the market.

The sampled detailed payload does **not** expose wind, cloud, raw provider
quality flags or all historical quality-control baselines. It can verify
published arithmetic conditional on provider decisions, not fully independently
reconstruct hidden source checks. Existing
[E005](../config/e005_pending_index.json) already investigated public pending
component reconstruction. That arithmetic is not a new hypothesis in this
round. The [alignment audit](HOURLY_ALIGNMENT_AUDIT.md) separately establishes
why publication, determination and cash release are different clocks.

**Direct Synoptic.** Metadata documentation identifies token requirements,
station IDs, network IDs, variable availability and periods of record. Those
are potential future metadata fields, not observations we obtained through
the denied request. Synoptic's latency service describes arrival at its own
ingestion servers relative to observation time, and explicitly allows negative
latency from clock/timestamp issues. Even access to that telemetry would not
recreate Kalshi's local receipt or this project's first receipt.
[Metadata specification](https://docs.synopticdata.com/services/metadata),
[latency specification](https://docs.synopticdata.com/services/latency).

**AWC.** The verified paths are `/api/data/stationinfo` and `/api/data/metar`,
with comma-separated `ids`, `format=json` and a two-hour METAR window.
The current documentation specifies up to thirty days of database history,
100 requests per minute, and a usual 400-entry response limit. Most METARs
are hourly; special reports can be more frequent. A once-per-minute cache
update does not make each airport a one-minute observation source.
[AWC API documentation](https://aviationweather.gov/data/api/).

Schema fields include `obsTime` (UTC Unix seconds), `reportTime`,
`receiptTime`, `icaoId`, `rawOb`, `metarType`, `temp` (°C), `dewp`,
`wdir` (degrees or `VRB`), `wspd` (knots), and cloud layers.
Use field presence and values, not schema example/default values, to determine
missingness. The response includes a numeric `qcField`; no verified bit
mapping was established here, so do not interpret nonzero as automatic failure
or zero as equivalence to the index's quality-control decision.
[Official OpenAPI schema](https://aviationweather.gov/data/schema/openapi.yaml).

The first two-hour query returned **69 reports from 15/17 stations**: 39
METAR and 30 SPECI. Three temperatures, eight wind directions, eight wind
speeds and three cloud fields were null. Observation-to-AWC-receipt delay
ranged from **69 to 1,027 seconds**, median **221 seconds**; our own receipt
was later still. The second query returned 70 reports. The 68 shared
`(icaoId, obsTime, reportTime, metarType)` groups had unchanged raw messages;
there were no duplicate identity groups in the first response and no `COR`
markers. This brief sample establishes neither an absence of corrections nor
a permanent revision log. Preserve raw messages, changed editions and first
actual receipts rather than overwriting repeated observation timestamps.

**NWS.** The current service documentation warns of observations delayed by
up to twenty minutes during upstream MADIS quality processing. The KMIA
canary's newest observation was 19:00 UTC; our receipt was 19:16:57 UTC.
Nine of ten rows had an empty `rawMessage`; each temperature supplied
`unitCode`, `value` and quality code `V`, but no provider-receipt timestamp.
These are useful source-state fields, not proof that the result is the exact
genuine METAR fallback the index administrator used. Preserve unit codes and
nulls, and use our receipt for prospective availability.
[NWS API documentation](https://www.weather.gov/documentation/services-web-api),
[tested observation endpoint](https://api.weather.gov/stations/KMIA/observations?limit=10).

**Historical upstream training.** IEM's minute-download page currently says
its archive is available through **2 September 2026**, and advertises
temperature, dew point, wind and other minute fields. This is a promising
bounded training-data acquisition, not a verified per-station coverage result
or a current trading feed. Its separate routine-report download is refreshed
from real-time ingestion every ten minutes and mixes source/report types.
Missing `M` can mean absent, rejected or unreported data; optional trace
representations must not be converted into zero. Generated five-minute
HFMETAR can be requested there, but cannot silently become an index fallback.
[IEM minute interface](https://mesonet.agron.iastate.edu/request/asos/1min.phtml),
[routine-report documentation](https://mesonet.agron.iastate.edu/request/download.phtml).

The inspected NCEI directory documents station-month DSI 6405/6406 files.
It listed years 2000–2022, with no demonstrated current 2026 station files in
that location. We did not fetch any historical weather body, including protected
2025 dates. Neither NCEI filenames nor an IEM retrospective download can supply
missing historical first-publication or administrator-receipt evidence.
[NCEI directory](https://www.ncei.noaa.gov/pub/data/asos-onemin/),
[format overview](https://www.ncei.noaa.gov/pub/data/asos-onemin/readme.txt).

## Geographic census: candidates, not a selected winning region

The IEM Florida ASOS directory had 113 entries. A haversine distance filter
using the fixed KMIA coordinates found twenty within 150 km: seventeen marked
online and three inactive (`EGC`, `MBF`, `OCR`). All seventeen proposed ICAO
IDs were confirmed by AWC station metadata, rather than assuming that prefixing
an IEM code with `K` always works. Distances below use the returned AWC
coordinates and its KMIA coordinate as the anchor. Radius from KMIA does not
guarantee the same radius from every northern index component.
[IEM directory](https://mesonet.agron.iastate.edu/geojson/network/FL_ASOS.geojson),
AWC raw **111606**.

| ICAO ID | Distance from KMIA, km | Reports in first two-hour sample | Role |
| --- | ---: | ---: | --- |
| KMIA | 0.0 | 3 | Exact member's METAR identity |
| KOPF | 14.0 | 10 | Exact member's METAR identity |
| KTMB | 20.0 | 3 | Explanatory candidate; excluded index member |
| KHWO | 24.7 | 10 | Explanatory candidate; excluded index member |
| KHST | 33.7 | 7 | Explanatory candidate |
| KFLL | 35.8 | 4 | Exact member's METAR identity |
| KFXE | 47.8 | 3 | Exact member's METAR identity |
| KK70 | 52.0 | 5 | Explanatory candidate |
| KPMP | 55.0 | 4 | Exact member's METAR identity |
| KBCT | 69.0 | 2 | Explanatory candidate |
| KLNA | 92.4 | 5 | Explanatory candidate |
| KDJT | 102.1 | 2 | Explanatory candidate; index spare only |
| KF45 | 117.5 | 6 | Explanatory candidate |
| K2IS | 128.7 | 0 | Metadata present; observations missing in query |
| KIMM | 130.3 | 0 | Metadata present; observations missing in query |
| KMKY | 137.8 | 3 | Explanatory candidate |
| KMTH | 139.1 | 2 | Explanatory candidate |

“Upstream” is conditional on wind at decision time, not a permanent station
label. Variable/missing wind gives no reliable transport direction. A spatial
model must also distinguish airports from their surrounding grid cells,
account for coastal circulations, and retain both missing stations above.
Modern spatial weather models such as
[GraphCast](https://arxiv.org/abs/2212.12794) are prior art; their results do
not validate a local METAR transport predictor.

## Three minimal proposed experiments

These are proposals for separate registration. They do not change E024's
annual selector or activate a new live strategy. The ongoing E027 collector
can supply prerequisites if its frozen scope includes these sources; avoid
launching a competing duplicate collector.

**F1 — Receipt and edition preflight, no forecasting.** Freeze all five
components and all seventeen neighboring metadata identities. Over one
prospective 24-hour window, record detailed index snapshots and two-hour AWC
report windows once per minute, with bounded retries and gaps retained.
Record calibrations at startup and hourly. Use event time, actual HTTP receipt
and source-reported receipt as distinct columns. The two feeds require 2,880
requests; twenty-five calibration checks give 2,905 total before retries.
Enforce a separately declared total-attempt ceiling. At the sampled payload sizes this
is roughly 60 MB/day uncompressed, an estimate rather than a storage guarantee.

Report per-source missing intervals, received editions, clock anomalies,
provider-to-project delay and the number of decisions with usable inputs.
Do not calculate forecast errors or trading profit. Reject any pipeline that
overwrites a changed edition, fills a missing minute silently, or uses a later
receipt as if known earlier. This tests whether a causal dataset exists; the
current two canaries cannot establish its completeness.

**F2 — Three-neighbor wind-conditioned residual, before a graph model.**
Use the three nearest nonmember airports fixed by the census: KTMB, KHWO and
KHST, with the exact index as target. First establish training coverage and
source comparability; historical IEM values remain conditional retrospective
features until prospective receipts exist. Freeze one 60-minute horizon and
two candidates: the existing direct-index baseline, and that baseline plus
one regularized linear correction from upwind temperature differences,
cloud indicators and their ages. No neural training or tuning grid.

For a transport feature, convert meteorological wind **from** direction into
motion **toward** the target. Use only observations already received by the
decision; a requested upstream time `target − travel_time` after the decision
is unavailable, not a future observation to interpolate. Register treatment
of variable/calm wind, source age and missing neighbors. Fit only on the fixed
training artifact; calibrate and evaluate on the separate future 14+28-day
development grid in [the queue](INNOVATION_QUEUE.md), with a ten-minute fit
budget. Include reversed-wind and fixed shuffled-neighbor diagnostic features
without selecting a better placebo by performance. Reject improvements caused
by future receipts, reduced case coverage or only one storm day. This tests
whether upstream information warrants a later small spatial model.

**F3 — Source-state uncertainty on a fixed mean forecast.** Keep the current
mean model and target clock unchanged. Compare its pooled residual scale with
one fixed linear log-scale correction using actual input age, component-count
availability and normal/degraded/pending state. Do not use inaccessible raw
provider flags or pretend to reproduce full canonical quality control.
Predeclare unknown-state fallback, support thresholds and the same future
14+28-day development calendar. Cap fitting at ten minutes.

Assess interval coverage and proper distribution losses on every registered
case, plus fixed one-/three-minute delayed-input replays. Delays remove inputs
that were not yet available; they never move the target. This extends E005's
existing arithmetic by testing the uncertainty of source states, rather than
claiming pending readings are certain. Time gaps and masks are established
ideas, including [GRU-D](https://arxiv.org/abs/1606.01865); its original results
do not prove weather skill. Any apparent gain still needs settlement-target
calibration, book depth, fees, adverse-fill and capital tests before trading.

## Exact request manifest and checks

All times below are successful-response or error-response **receipts on
6 September 2026, UTC**. Full microseconds, source URLs and body SHA256 values
remain in the linked machine-readable manifest. No redirects or retries
occurred, so eighteen logical requests equal eighteen physical GETs.

| Request | Raw ID | HTTP | Receipt UTC | SHA256 prefix |
| --- | ---: | ---: | --- | --- |
| Miami detailed index | 111433 | 200 | 19:14:11.114970 | bb15c55ebcc4 |
| Miami calibrations | 111435 | 200 | 19:14:11.828431 | 148e0b48624b |
| AWC API documentation | 111438 | 200 | 19:14:12.975331 | f13e038f7a6f |
| Synoptic metadata documentation | 111443 | 200 | 19:14:14.083924 | fc12b0c8b88a |
| IEM Florida station directory | 111446 | 200 | 19:14:15.484827 | 521e42c3a023 |
| AWC OpenAPI specification | 111602 | 200 | 19:15:23.968892 | d7a6f291187d |
| AWC seventeen station identities | 111606 | 200 | 19:15:24.754850 | 913d78cf95de |
| AWC two-hour reports | 111609 | 200 | 19:15:25.707542 | 44f1e9fcd620 |
| Synoptic latency documentation | 111612 | 200 | 19:15:26.768469 | d109d4f63ce0 |
| Synoptic anonymous metadata | 111616 | 401 | 19:15:28.014564 | 7a3578da72c9 |
| IEM routine-report documentation | 111620 | 200 | 19:15:29.096316 | 832f5caff6f6 |
| NCEI minute directory | 111623 | 200 | 19:15:30.351998 | d9e15cf7f104 |
| NWS API documentation | 111820 | 200 | 19:16:56.674994 | 6e147aa56472 |
| NWS KMIA observation canary | 111823 | 200 | 19:16:57.458618 | bd62131c0c21 |
| IEM minute documentation | 111827 | 200 | 19:16:58.985134 | f036524c5788 |
| NCEI minute readme | 111830 | 200 | 19:16:59.658927 | 14dc778ce76e |
| Miami detailed index repeat | 111833 | 200 | 19:17:00.717455 | 54030649c437 |
| AWC reports repeat | 111836 | 200 | 19:17:01.699001 | ca7e7e584c31 |

Actual acquisition validation:

```text
physical GETs: 18 / 20
HTTP statuses: 17 x 200, 1 x 401
minimum actual request-start spacing: 1.000164 seconds
response bodies hash-verified: 18
total response bytes: 603672
weather scores: 0
trading scores: 0
```

The [bounded helper](probes/innovation_sources.py) records attempts before
network access and enforces the total GET limit, including redirects/retries.
Original acquisition helper bytes are preserved as source **112314**, matching
protocol 111428's source hash. After acquisition, its exception handler was
narrowed from all exceptions to HTTP exceptions and formatting was applied;
unexpected failures now stop the helper. No further requests followed that
change. Current Ruff checks passed.

A separate brief review of the unfrozen E024 generator found no blocking
mismatch with the normalized dataset schema: time gates precede price/outcome
access; missing and limit-failed entry intents are retained; late actual
receipts remain distinct from hypothetical historical availability. Actual
synthetic verification this session was **46 passed in 0.40s** for
`tests/test_e024_annual_replay.py` and `tests/test_bankroll_dataset.py`.
No normalization, registration, annual scoring or E024 edits were performed
by this scout. Final historical metadata still does not establish original
decision-time publication of rules, open/close times or prices.
