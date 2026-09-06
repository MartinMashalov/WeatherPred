# A fixed Austin hourly settlement-mapping audit

**Draft, 6 September 2026. No registration, collection, forecasts or orders.**
The proposed audit compares eight future KAUS hourly TWC observations with
the corresponding Kalshi expiration values and contract results. The
[configuration](../config/kaus_hourly_mapping_design.json) and its
[closed JSON Schema](../config/kaus_hourly_mapping.schema.json) fix the calendar,
sources, comparison and missing-data rules. A separately registered shared
collector must implement them before any new measurement or outcome access.

Eight matching values would demonstrate consistency in eight observed cases.
They cannot establish an undocumented measurement definition, rounding rule,
publication cutoff, correction policy or future failure rate. Paper admission
requires both the operational check and separate authoritative confirmation
of those semantics. Every case remains in the denominator, including a
missing listing, unavailable observation or delayed settlement.

## What is established, and what remains unresolved

The [existing metadata projection](../evidence/forecast_to_trade_metadata.json)
records catalog **155283** and Austin market page **156595**. The Austin
hourly family is `KXTEMPAUSH`; its primary listing names **KAUS** and The
Weather Company. The examined event title used Eastern Daylight Time, opened
one hour before its target and closed exactly at that target. These are
identity and listing observations; no numeric expiration values were read
for this proposal. Future listings must independently confirm them.

E022's station labels came from the TWC weekly
`https://weather.com/kalshi/api/metar?primary=true&weekStart=YYYY-MM-DD`
product. Its [existing parser](probes/station_history.py) exposes station
`icaoId`/`timezone`, hourly `reportTimeUTC`, `localDate`, `localHour`, `tempF`
and the provider's `pending`/`settled` status. A matching station identifier
does not prove that this product is the contractual hourly statistic. The
old parser also has a historical date cutoff; it must not be patched or
reused as a prospective loader without a new, separately frozen adapter.

The listing links [NHIGHD terms](https://assets.kalshi.com/contract_terms/NHIGHD.pdf),
archived record **1743**, SHA256
`2b515c2cf521ffffa895f306bad589b026a7f916fa375c7d097448c660e02590`.
The generic document has maximum/minimum/average and time-period placeholders.
It uses the designated source's full precision, allows qualifying corrections
before expiration and excludes subsequent revisions. It also permits certain
contingent resolutions if source information is unavailable. Its generic
clock and expiration clauses do not themselves identify the exact hourly
endpoint, measurement window or effective expiration for this listing.

Authoritative evidence must therefore resolve five separate questions:

1. Is this exact TWC endpoint and station row the designated settlement product?
2. Does its UTC hour represent an instantaneous reading, an aggregation or
   another reporting convention, and what interval does that value describe?
3. Which Fahrenheit precision or explicit issuance-time rounding rule applies?
4. What do `pending` and `settled` mean for this product's first official release?
5. What is the effective expiration cutoff, and which corrected publication
   version can govern before it?

The numerical audit must not answer these by selecting the version, offset or
rounding rule that happens to match an outcome. Unresolved semantics remain
unresolved even after eight exact equalities. A discrepancy likewise is not
automatically an exchange error: our assumed product, clock or version could
be wrong.

## The eight targets cannot move

Registration and source readiness must be established by **7 September 2026,
22:00 UTC**. Otherwise this calendar is aborted, without replacement targets.
The dates provide setup time; no weather values or forecasts were consulted
to select them. `S` is the target and `D=S−1h` the corresponding prospective
forecast decision, though this mapping audit makes no forecasts.

| S, UTC | D, UTC | Austin local time, CDT | Expected event ticker |
| --- | --- | --- | --- |
| Sep 8 00:00 | Sep 7 23:00 | Sep 7 19:00 | KXTEMPAUSH-26SEP0720 |
| Sep 8 06:00 | Sep 8 05:00 | Sep 8 01:00 | KXTEMPAUSH-26SEP0802 |
| Sep 8 12:00 | Sep 8 11:00 | Sep 8 07:00 | KXTEMPAUSH-26SEP0808 |
| Sep 8 18:00 | Sep 8 17:00 | Sep 8 13:00 | KXTEMPAUSH-26SEP0814 |
| Sep 9 00:00 | Sep 8 23:00 | Sep 8 19:00 | KXTEMPAUSH-26SEP0820 |
| Sep 9 06:00 | Sep 9 05:00 | Sep 9 01:00 | KXTEMPAUSH-26SEP0902 |
| Sep 9 12:00 | Sep 9 11:00 | Sep 9 07:00 | KXTEMPAUSH-26SEP0908 |
| Sep 9 18:00 | Sep 9 17:00 | Sep 9 13:00 | KXTEMPAUSH-26SEP0914 |

The event suffix is derived from `America/New_York`, not the station's
`America/Chicago` clock. The exact `close_time`, any `strike_date`, rule
timezone and station row must independently bind to `S`; ticker inference is
insufficient. All eight station-local dates belong to week starting
**2026-09-07**. There is no daylight-saving transition in this calendar;
explicit UTC conversion and `fold=0` are nevertheless recorded.

Freeze each complete event's sorted contract census at the fixed listing
slot, **S−3,570 seconds**, or D+30 seconds. There must be 1–64 unique nested
contracts, all belonging to the expected event and series, with no cursor or
truncation. Do not assume the old sample's ten strikes will recur. Missing
listing evidence blocks the case; do not replace it with a later favorable
census. Subsequent additions, removals or rule/strike/source/time changes
are `census_changed`, with all versions retained. All contracts must agree;
ten strikes still represent one weather realization.

## Bounded receipts and fields

Use one separately registered collector shared with the source-readiness
study. Combine coincident requests for the same URL and parameters into one
physical GET, recording every logical purpose. Do not run another collector
for this audit. The request ceilings below cover this mapping component,
before any deduplication; context/NBH acquisition needs its own combined cap.

For each S, pair the current-week TWC request with the exact event endpoint at
these offsets, in seconds:

`−60, 0, 30, 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400,
21600, 43200, 86400`.

Add the event-only listing request at S−3570. This is
**8 × (1 + 2 × 16) = 264 nominal requests**, at most **792 attempts** with
two bounded retries. Require at least one second between requests, a
10-second request timeout, a 30-second start deadline for each original
slot, 4 MiB per TWC body, 1 MiB per event body and 1 GiB overall. Honor
`Retry-After`; a retry that misses its original slot deadline remains missed.
Retain failed attempts, HTTP statuses and transport errors. Stop on 401/403,
the byte cap or **2026-09-10 18:10 UTC**; do not extend the calendar to recover
failures. No books, market prices, private endpoints or orders are requested.

| Source layer | Fields retained | Clock, precision and missingness rule |
| --- | --- | --- |
| HTTP/archive | URL, query, request-start, actual receipt, status, raw body hash, archive record/hash, headers | Receipt is local availability; provider or exchange time never backdates it. Keep each retry and late response. |
| TWC envelope | `weekStart`, `weekEnd`, `dates`, `source`, `fetchedAt`, `totalObservations` | Require requested calendar and existing `source=live` identity. Schema/source change or impossible clocks block admission. `fetchedAt` is distinct from measurement publication and local receipt. |
| TWC station | `icaoId`, `stationName`, `timezone` | Exactly one KAUS station object, `America/Chicago`; no alias or neighboring station. |
| TWC target row | `icaoId`, `reportTimeUTC`, `reportTimeLocal`, `localDate`, `localHour`, `tempF`, `tempC`, `status` | Exact S and local-calendar consistency. Retain numeric lexemes and pointers. `tempC` is provenance, never a replacement value or precision upgrade. |
| Event listing | Event/series/ticker identifiers, title, station/source rules, strike type/bounds, open/close, strike date, expected/maximum expiration, settlement timer | Listing times must bind to S. Unknown operators, pagination, missing fields or metadata changes are retained failures. |
| Exchange maturity | `status`, `result`, `expiration_value`, `settlement_ts`, `updated_time`, first local determined/finalized receipts | Normalize target outcome fields only after S. Require ordinary yes/no finalization for the numeric check. Expected expiration and `updated_time` do not prove the effective legal cutoff. |

The shared raw weekly body contains other hours and stations. This audit
normalizes only the eight exact KAUS target observations. Before S it may
inspect metadata/status, but cannot expose a future-valid numerical row or
outcome as an observed label. The separate readiness adapter may normalize
its registered historical context only under that study's own time gates.

Use exact finite decimal parsing directly from raw numeric tokens/strings;
retain JSON pointer or byte span, source row index, original representation,
first-seen receipt and all subsequent receipt IDs. Boolean, null, malformed
and nonfinite values are not numbers. Do not pass through binary floating
point before making the exact comparison. Identical relevant duplicates
collapse with every source index retained; conflicting duplicates in one
payload are quarantined, without choosing a warmer value or preferred status.

## Fixed comparisons, maturity and revisions

The primary operational TWC candidate is the **first locally received unique
exact-hour row with `status=settled`**, no later than the first locally
observed `determined` or `finalized` exchange state in any contract in the
frozen census. That cutoff limits the claim to a source version we actually
saw before discovering the exchange decision; it is not the legal expiration
time. If no such row exists, the primary comparison is missing. A row first
obtained after discovering the outcome cannot retrospectively pass it.

For each contract, take its first observed `finalized` ordinary yes/no
version, and retain every later version. Require all numeric expiration
values to agree. Compare that value with `tempF` using exact decimal equality,
**zero tolerance and no inferred rounding**. For the supported
`strike_type=greater`, also require

`(expiration_value > floor_strike) == (result == "yes")`.

Compare the TWC candidate against every ordinary binary predicate as well.
This distinguishes value disagreement, strike-boundary disagreement and a
case where different source numbers happen to produce the same binary
outcomes. For example, a subtitle does not turn strict `Y > 96.99` into a
different arithmetic rule.

If any contract lacks a numeric expiration value, preserve available numeric
comparisons but do not classify the event as an exact numeric match. YES at K
implies `Y ∈ (K,+∞)`; NO implies `Y ∈ (−∞,K]`. Their intersection is the
binary-implied interval, with exact open/closed endpoints. An empty
intersection or a TWC candidate outside it is a predicate conflict. Otherwise
it is only `predicate_only_consistent`, with no invented midpoint label.
Unknown, reviewed, fair-price or nonbinary resolutions cannot supply ordinary
temperature equality evidence.

Do not stop polling when a match appears. A `pending → settled` transition
with the same value is one observed state transition, not two successes.
Changing only `fetchedAt` does not create another independent observation.
Representation changes such as `70.0 → 70.00` are retained but are numerically
equal. A changed settled TWC number or a changed finalized exchange value or
result is `revision_sensitive`, blocks paper admission, and retains the
original comparison. No retrospectively chosen matching version replaces it.

Record expected expiration, maximum expiration, settlement timestamp and
first local determination/finalization separately. Later authoritative
evidence might establish a legitimate correction before effective expiration
or a change afterward that cannot govern settlement. Preserve that evidence
without overwriting the failed original diagnostic. Revisions beyond the
fixed +24h window are unobserved, not proved absent. The window can also end
before delayed finalization; such cases remain pending and incomplete.

Report independent identity, numeric/predicate, revision, maturity and
semantic statuses. Numeric conflict overrides a missing-only summary, but
retain all missing reasons. Operational success requires **8/8** confirmed
identities, exact numeric and predicate agreement, unchanged complete censuses,
ordinary finalization, and no captured numeric revisions or unresolved gaps.
No conditional success percentage drops difficult cases.

The last +24h slot is **10 September 18:00 UTC**. Preliminary agreement
before it cannot satisfy the full revision-window gate. Any paper decisions
before the complete audit and semantic approval remain blocked on their
original calendar; they are not shifted or issued after learning outcomes.
The next target on this four-times-daily grid after the hard stop is
**11 September 00:00 UTC**. A paper study still requires its own declaration.

Eight samples across two weather days cannot identify rare corrections or
seasonal failure. Even under an illustrative independent, identical mismatch
rate assumption, observing zero failures in eight would leave a one-sided
95% upper limit of `1 − 0.05^(1/8) ≈ 31.2%`; dependence weakens that simple
interpretation. This is not a validated confidence bound for these cases.
Eight matches provide operational evidence, not a statistical guarantee.

After any later mapping failure, persist a **no-new-entry flag before the
next signal**. Cancel resting paper intentions, retain reserved cash until
their terminal state is reconciled, and leave held positions and losses in
the ledger under the original maturity rules. No automatic resume, source
substitution or rounding switch. A corrected mapping needs a new declaration
and prospective check rather than deleting the failure.

## Quantiles can preserve uncertainty about interval contracts

This audit makes no forecasts or scores. The following is a proposed
probability representation for a later declared study; it does not change
the frozen E029 forecasts. Let `Qα` be a coherent quantile function on the
available probability grid, `F(x)=P(Y≤x)` and `G(x)=P(Y<x)=F(x−)`.

The quantile grid supplies the outer bounds

```text
L_F(x) = max({α : Qα ≤ x} ∪ {0})
U_F(x) = min({α : Qα > x} ∪ {1})
L_G(x) = max({α : Qα < x} ∪ {0})
U_G(x) = min({α : Qα ≥ x} ∪ {1})
```

For a strict-above contract, `P(Y>K)` lies in
`[1−U_F(K), 1−L_F(K)]`. For an inclusive interval contract,
`P(a≤Y≤b)=F(b)−G(a)`, yielding the conservative outer interval

```text
[max(0, L_F(b) − U_G(a)), min(1, U_F(b) − L_G(a))].
```

These are bounds implied by the **forecast's quantile resolution**, not
statistical confidence bounds or guarantees about the true distribution.
The original fourteen calibration days do not certify each quantile level
as the true probability; grid or optimization bounds alone cannot establish
a profitable trade.
They preserve possible mass exactly at a strike, which rounding can create.
Crossing/nonfinite quantiles or inconsistent constraints fail the conversion;
do not repair them using eventual outcomes. Any model-defined monotone repair
must already belong to the frozen forecasting protocol.

For a purely synthetic grid `Q0.1=90`, `Q0.5=95`, `Q0.9=100`, strict
`Y>95` has resolution bounds `[0.1,0.5]`, while inclusive `95≤Y≤100`
has bounds `[0.4,0.9]`. The wide intervals honestly retain uncertainty that
three knots cannot resolve. They do not justify a smooth density or a finer
reporting grid. More generally, one coherent CDF constrained by all quantiles
and strikes can tighten interval bounds; this would be a deterministic
constraint calculation, not another fitted model or newly invented precision.

A later probability-scoring protocol can freeze one interpolation for its
point estimate and report these bounds alongside it. A conservative execution
screen should use the worst probability within its declared bounds before
spread, fees and fill stress; independently choosing favorable interpolation
for each strike would inflate apparent edge. Source precision still requires
authoritative identification. Eight values ending in the same decimal pattern
cannot establish that precision.

## Verification and handoff

This round used local archived metadata, source schemas and the already
reviewed terms. **Zero new public GETs**, zero new measurement/outcome reads,
zero registration records, zero forecasts and zero orders. The config's
source hashes identify the exact material used. No frozen artifact changed.

The [design-check evidence](../evidence/hourly_settlement_mapping_checks.json)
records this session's JSON Schema validation, UTC/local/ticker and request
budget assertions, source-hash verification, negative safety-config cases,
and synthetic quantile-bound checks including ties. These checks verify the
design and arithmetic; they do not verify the proposed source equivalence.
Reproduce the [offline checker](probes/hourly_mapping_design_check.py) with
`python3 research/probes/hourly_mapping_design_check.py` using the existing
system Python's `jsonschema` package. No dependency was added to the base
project environment, and the checker does not import a model runtime.

Handoff: combine this acquisition schedule with the KAUS source-readiness
proposal, implement exact decimal provenance and conservative status handling,
freeze the shared collector and register the exact calendar before accessing
its future observations. Keep paper admission blocked while any semantic or
operational gate is unresolved.
