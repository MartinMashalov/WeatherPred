# KAUS live input readiness

**Draft, 6 September 2026. No registration, weather requests, forecasts or
orders.** The new [pure adapter](../weatherpred/live_hybrid_inputs.py) and
[finite design](../config/live_hybrid_readiness_design.json) prepare the input
checks for the eight September 8–9 targets in the
[settlement-mapping proposal](HOURLY_SETTLEMENT_MAPPING.md). They do not start a
collector or authorize a paper trade. The next work should test whether the
sources are usable at decision time, before adding more model complexity.

There are two independent gates: **input readiness** and **contract target
equivalence**. Receiving a usable TWC history and NBH forecast does not establish
that the TWC row is the contract's legal settlement statistic. The generic
NHIGHD terms still leave the exact measurement definition, precision and
correction cutoff unresolved. Eight matching outcomes could establish observed
consistency, but cannot supply missing authoritative definitions.

## What the existing archive establishes

The existing E022 acquisition used
`https://weather.com/kalshi/api/metar?primary=true&weekStart=YYYY-MM-DD`.
I inspected the existing schema and receipt metadata, without inspecting new
temperature values or scores. Source **78077** is 2,282,752 bytes, SHA256
`d08a8469392535fe5623200023f5f23bf940f1061acd77965f3755a8ab108957`, received
`2026-09-06T17:09:19.493299+00:00`. It identifies source `live`, KAUS and
`America/Chicago`. Its hourly fields include `reportTimeUTC`, `localDate`,
`localHour`, `tempF`, `tempC` and `status`. Source **88232** is another
2,158,173-byte weekly response. A 2 MiB response limit would reject these
already known formats; the proposed ceiling is 4 MiB.

E022 accepted only the hourly rows marked `settled`. E027 v2 collects aviation
METAR observations and Miami components, which are different products. Neither
their cadence nor their temperatures can replace this context. The old
historical station parser and model adapter have frozen calendar cutoffs;
the new prospective adapter leaves them unchanged. No official documentation
GET was needed for this inventory, and none was made.

Original NOAA NBH object naming, version 5.0 cards and byte offsets were already
checked by E025 v2/E026/E031. The proposed live path requests the same original
product from `noaa-nbm-grib2-pds.s3.amazonaws.com`, with a newly archived actual
receipt. Old September acquisitions of July guidance cannot demonstrate live
availability for this new calendar.

## The decision-time input contract

For each target `S`, decision `D=S−1h`. The original 168-hour context grid ends
at `D−1h`, retains missing slots, requires at least 120 finite values and a
latest finite value no more than 120 minutes before D. The 15-minute minimum
observation lag remains, alongside the stronger prospective requirement that
the complete response actually arrived by D. A current-week snapshot received
within three minutes of D is also required. A dedicated request is scheduled
at D−2min; a late/missing response leaves readiness false.

Every row edition retains the exact JSON Fahrenheit number spelling, full row
revision hash, original response and record hashes, row indexes, provider
fetched time, UTC/local clocks and actual receipt. Number strings, nonfinite
numbers, changed station/source/timezone and mismatched local-hour identities
are rejected. Celsius and METAR are never substituted. UTC identifies the
hour; local dates are checked with the named timezone, including its fold.

At D select the latest **already received** row edition for each context
hour. It must already say `settled`; a later settled revision cannot repair
an earlier pending value. Conflicting revisions sharing the latest receipt
are missing, not selected by temperature. Repeated identical editions retain
their first local receipt and all as-of source IDs. Older history downloaded
at startup becomes available at its real receipt, with an explicit
`observation_predates_registration` flag; it does not acquire fictitious
earlier receipt times.

Physical guidance is fixed to run **D−2h**, with requests at **D−30min** and
**D−10min**. These are two predeclared opportunities to obtain the same cycle,
not permission to substitute a newer run. The latest complete original-cycle
edition received by D is used; a missing value in that edition cannot be
rescued by selecting an older favorable edition. Both raw editions remain.

The adapter checks original URL, full body/metadata hashes, ETag, Content-Length,
version, station, runtime and `runtime <= Last-Modified <= actual receipt <= D`.
Last-Modified remains storage metadata, separate from local availability.
Only TMP leads **2 and 3** are decoded: D and D+1 on the seven-slot future grid,
followed by five nulls. The frozen 50/50 blend would need only lead 3; retaining
lead 2 is input provenance, not another model experiment. Required-cell byte
offsets and raw lexemes are retained. Missing cards/cells yield unavailable;
malformed identities fail. The disclosed shared low-level extraction helper
is frozen E031 `cell_path`; the live receipt gates are new.

## One shared, finite collector is proposed

E027 v2 ends **September 7 at 19:48:04 UTC**. It cannot cover any of these eight
decisions. The new shared mapping/readiness collector is proposed for
**September 7 20:00 UTC through September 10 18:10 UTC**, with registration by
**September 7 19:59:30 UTC**. This earlier startup deadline is stricter than
the mapping proposal's 22:00 deadline. Missing it does not authorize rolling
the calendar or extending E027.

The pure `planned_requests(config)` function unions requests with identical
scheduled time, URL and parameters, retaining all logical purposes:

- One August 31 weekly snapshot supplies older context. Refresh the September
  7 week every ten minutes, with the eight extra D−2min requests.
- Add the mapping proposal's exact target-relative TWC/event polls through
  S+24h and its event listing poll at D+30s. Use the same TWC response whenever
  these coincide with a context refresh.
- Fetch each of the eight fixed NBH cycles twice, preserving all editions.

The resulting manifest has **638 distinct requests: 486 TWC, 16 NBH and 136
event requests**. Fifty-two requests serve multiple purposes. Three attempts
per request allow at most **1,914 attempts**, below the independent 2,400
hard ceiling. As fixed in the mapping proposal before registration, **all
already received matching TWC snapshots from this full union** enter its
first-seen/version ledger, including context-only requests. Logical purpose
tags do not allow the audit to ignore an earlier observed revision. The full
merged cadence and this selection rule must be pinned together.

The combined payload cap is **4 GiB**, with 4 MiB TWC, 1 MiB event and 40 MiB
NBH limits. One attempt at every per-response ceiling totals 2,852,126,720
bytes. Retries can exhaust the overall budget; full coverage is not promised.
Charge every retained byte and a reserved 16 KiB overflow-detection chunk;
interrupted requests conservatively retain their full reservation. Require
8 GiB free disk, one-second request spacing, ten-second request timeout,
30-second slot-start tolerance, a singleton lock and a stop file. Missed
slots remain missing; no backdated catch-up or automatic deadline extension.

The existing E025 bounded streaming pattern is reusable, but its client has
frozen experiment namespaces and prior-source accounting. It must not be
instantiated as the new collector. Generic `PublicClient` follows redirects
and has no streamed size ceiling, so it is insufficient unchanged. A new
small wrapper should reuse safe HTTP/archive primitives and the proven
reservation pattern, with new record kinds and no modifications to frozen
files. Root must review and separately register that wrapper, this design,
the mapping selector, source/test/transitive hashes, environment, exact
command and merged manifest before any requests. **That collector is not
implemented or started by this task.**

The readiness module accepts only original weather bytes and archive records;
it never consumes market responses. The separate mapping audit owns outcome
access after target time. No books, signals, model calls or orders belong to
this source-readiness stage. Even when both inputs are ready, the output
continues to say `settlement_product_equivalence_verified=false`.

## Checks performed

The synthetic suite covers exact number spelling, actual-receipt causality,
pending-to-settled revisions, first receipts, conflicting versions, missing
context, freshness, UTC/local identities, changed source products, source hash
tampering, exact NBH cycles/offsets/padding, missing latest editions and the
merged fixed calendar. Actual output: **20 passed in 0.19s**. Ruff reported
**All checks passed!**; both new Python files were formatted. No real weather
request, model execution, score, outcome normalization or archive write was
performed in these checks.
