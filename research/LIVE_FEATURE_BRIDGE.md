# From archived training observations to available live features

Research specification, **6 September 2026**. The main problem is an input
mismatch: IEM's delayed NCEI minute records, the index's primary component
reports and public METARs are different products. A model trained on dense
minute wind and temperatures cannot be deployed as though all those inputs
arrive live at minute cadence.

The concrete next hypothesis is a small state-space model that updates an
estimated temperature state when a report actually arrives, while retaining
that report's observation time, product and precision. Test it against local
ridge regression and the same state model with reversed wind. **No new model
was fitted, no weather or trading score was computed, no measurement history
was fetched, and no collector or frozen source file was changed in this round.**
This is a proposed registration, not an experiment already registered or run.

## What is available, and what is still a prerequisite

E027 v1 registration **116258** lists twenty E022 airports plus KMDW. Among the
eight airports in the [IEM proposal](MINUTE_OBSERVATION_PROPOSAL.md), its METAR
list contains **only KMIA**. Its separate detailed index request captures the
five primary component temperatures. Thus v1 has neither the other four
component airports' METAR wind/dew point nor the three nonmember airports'
METARs. A common KMIA wind could be an explicitly approximate driver, but it
would not test the proposed three-neighbor mechanism.
[Frozen v1 configuration](../config/e027_prospective_capture.json),
[collector schema](experiments/e027_prospective_capture.py).

During this review, root registered E027 **v2 as 122996**, with 28 METAR
stations: v1 plus KFLL, KFXE, KOPF, KPMP, KTMB, KHWO and KHST. That includes
the eight required airport identities in one bulk request, but requested scope
is not complete observed coverage. Its progress metadata at slot 7 records
72 METAR reports and **KTMB missing**; no measurement values were inspected
for this review. These remain irregular METARs, not a continuous minute feed.
Require successful required-station coverage before the proposed test; do not
silently substitute v1 or remove KTMB. This weather study uses no book
selection or prices. [V2 registration](../evidence/E027v2_registration.json),
[frozen v2 configuration](../config/e027_prospective_capture_v2.json).

Root has also registered the three IEM **acquisition-only** canaries as E028
**119562**. Their measurement bodies were not inspected here. The independent
[schema/coverage assessment](MINUTE_CANARY_ASSESSMENT.md), using responses
**122584/122593/122602** and report **122603**, finds that both required
seven-station canaries **fail**: January is header-only; August has 360 rows
for six airports, with **MIA entirely absent** and all 240 TMB variable fields
literal `M`. Five other airports have numeric-syntax fields. HST's separate
January response is also header-only. Absent rows and explicit missing fields
are distinct failures; the cause of January's empty response is unresolved.
Thus there is no complete eight-airport IEM training panel, and no confirmed
CSV-to-live equivalence. Live KMIA coverage does not repair missing historical
MIA. The original larger minute acquisition gate fails; do not acquire it or
drop MIA/TMB to make the original proposal appear feasible.

## Variable and observation mapping

Preserve original raw HTTP bytes. Model parsing should retain exact decimal
values before converting to floating-point arithmetic; serialization digits
alone do not prove instrument resolution. Original source-row references are
necessary because the collector's parsed `raw_version` is not the original
numeric lexeme.

| Quantity | Proposed IEM training field | Actual E027 source | Unit / precision handling | Missingness and timing |
| --- | --- | --- | --- | --- |
| Five component temperatures | `tmpf`, using MIA/OPF/FLL/FXE/PMP | Detailed index `point.stations[].temp_f`, with exact `*1M` identity | Fahrenheit; convert to °C as `(F−32)×5/9` without premature rounding. Do not infer primary sensor resolution from the decimal places. | Require eligible source/code and valid event time. Administrator `received_at_ms` is separate from our first receipt. |
| Three nonmember temperatures | `tmpf` for TMB/HWO/HST; TMB canary all `M`, HST absent | No v1 coverage; registered v2 METAR `temp`, with KTMB missing in inspected progress metadata | METAR temperature is °C. Parsed precision may differ from IEM and primary ASOS. Preserve `rawOb` and the native representation. | No synthetic minute values between METAR reports. Requested scope is not observed coverage. |
| Component-airport METAR temperature | Same-airport `tmpf`, different product; MIA absent from both canaries | `temp`; KMIA in v1, all five requested in v2 | Useful as a separate measurement channel, not an interchangeable label. | Avoid double-counting a primary ASOS and METAR observation from the same instrument/time. |
| Dew point | `dwpf`, °F | METAR `dewp`, °C | Convert units explicitly; do not use dew point as a fabricated cloud observation. | Absent from detailed index. Same v1/v2 station limitations as METAR temperature. |
| Wind speed | `sknt`, knots | METAR `wspd`, knots | Convert to m/s using `knots×1852/3600` when computing transport time. | Irregular report age must be retained. Missing is not calm. |
| Wind direction | `drct` | METAR `wdir`, numeric degrees or `VRB` | Convert a meteorological **from** direction into a **toward** vector. | Missing/variable direction creates no directional edge. Do not replace it with the best-performing direction. |
| Cloud layers | Not advertised in the IEM minute variable list | METAR `clouds` | Preserve reported cover and base fields without converting an empty/missing field into clear sky. | Excluded from the minimal three-model comparison because no training bridge is established. |
| Primary quality/source state | No demonstrated equivalent | `source`, `code`, point `status`, component presence | `pending` is not canonical quality approval. Raw provider flags and the complete source QC baseline are not exposed here. | Keep status transitions; do not infer hidden flags from forecast error. |
| Observation valid time | Requested `tz=UTC`, CSV format still to be audited | Index `point.t` in ms; METAR `obsTime` in seconds | Normalize to UTC; local display time is derived separately. | A valid time is not a publication time. |
| Provider/administrator receipt | No demonstrated historical equivalent | Component `received_at_ms`; METAR `receiptTime` | Distinct provider clocks; validate against our local receipt, but never substitute them for it. | Negative or inconsistent delays are explicit source errors, not negative-age inputs. |
| Project availability | Actual future IEM download time | `first_local_received_at`, linked to raw `actual_received_at` | First successful local receipt of the exact version governs eligibility. | Startup/backfill flags prohibit treating already-old reports as newly observed weather news. |
| Canonical index label | **Not available from IEM** | Initial canonical `point.v`, normal/degraded, correct configuration | Published °F value; its cent rounding does not establish cent-level component measurements. | A future label is read only after the declared label gate. Latent estimates never replace it. |

Source definitions and observed payload limitations are in the
[source-feasibility report](INNOVATION_SOURCE_FEASIBILITY.md), with raw
**111433/111833** (components), **111609/111836** (METAR), **111602** (AWC
OpenAPI) and **113778** (IEM product help). The
[AWC schema](https://aviationweather.gov/data/schema/openapi.yaml) defines
temperature, wind and time fields; the
[IEM help](https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?help)
identifies its separate delayed NCEI product.

## The estimated state is not an observed label

Let `z_i(t)` be an estimated continuous temperature for airport `i`, in °C,
and `v_i(t)` its estimated local rate of change. Neither is a directly observed
truth. A sixteen-dimensional state can cover the five component airports and
the three fixed nonmembers:

`x(t) = [z_1(t), …, z_8(t), v_1(t), …, v_8(t)]`.

A report from source product `s`, valid at `t` but first received locally at
`a`, supplies a measurement of that state:

`y_s(t) = H_s x(t) + b_s + measurement_error_s`.

`H_s` identifies the airport and, when known, the product's measurement window;
`b_s` is a source offset. Different instruments, temporal averages, calibration
and rounding can produce different observations of the same modeled state.
Do not learn unrestricted offsets and unrestricted latent levels together:
they are not identifiable from those reports alone. Anchor the five component
levels to their primary product. The minimal experiment does not estimate an
IEM-to-live bias without paired observations.

When a source's rounding step `q_s` is **documented and verified**, a reported
value corresponds to a measurement interval, not an infinitely precise point.
For Gaussian measurement error with scale `sigma_s`, its likelihood is

`Phi((y+q/2-Hx-b)/sigma) - Phi((y-q/2-Hx-b)/sigma)`.

`Phi` is the standard normal cumulative probability. A simpler Gaussian
approximation adds `q²/12` to the measurement variance, provided that uniform
quantization is explicitly an approximation. When `q_s` is unknown, flag it;
do not guess a fine resolution from the returned JSON. A conservative
source-noise floor can be fixed before the experiment, then tested for coverage.
More decimal places in the model's posterior mean are an estimate, not added
sensor precision.

The state evolves between irregular reports. A concrete small model is

`d z / dt = v - kappa * L(w) * z`

`d v / dt = -v / tau + process_noise`.

`L(w)` is a directed graph matrix whose weights use fixed station geometry
and currently available wind. `tau` controls how long a local trend persists;
`kappa` controls weak spatial mixing. Missing/stale wind switches off the
affected directional weights; it does not remove the forecast case. The
estimated covariance increases while the model receives no new information.
This makes uncertainty respond to source age instead of filling gaps with
apparently fresh temperatures.

This is a conventional state-space hypothesis, not a claim of a new class of
forecasting. Kalman's original paper develops state-transition filtering and
its estimation-error covariance. Delayed observations are an established
estimation problem, including Bar-Shalom's out-of-sequence measurement work.
[Kalman, 1960](https://doi.org/10.1115/1.3662552),
[Bar-Shalom, 2002](https://doi.org/10.1109/TAES.2002.1039398).

## Availability, delayed reports and repeated versions

At decision `D`, only source versions whose **actual first local receipt is
at or before D** can enter the estimate. Query and gate receipt/timestamp
metadata before reading that version's measurement values. Keep the original
forecast and its input-record manifest immutable.

When a newly received report describes a past time, use a bounded fixed-lag
buffer: update the state at that observation time, then propagate to `D` using
only messages already received by `D`. This is a new estimate made now; it
does not revise what the system claimed to know at an earlier decision. Reports
outside the fixed buffer remain recorded but cannot silently trigger an
unbounded history replay. A minimal implementation can use a three-hour
buffer and retain every exclusion.

There are two particularly important duplicate rules:

- E027 preserves a new full-report version when any field changes. A change to
  an unrelated rainfall field does not create a second independent temperature
  measurement. Derive a variable-level measurement identity while retaining
  the full version's provenance.
- A point changing from pending to canonical can contain the same five
  component temperatures. A quality-state change can replace its earlier
  likelihood in a current fixed-lag replay, but must not multiply the same
  temperature evidence twice. The canonical index derived from those same
  components is also not an independent sixth thermometer.

For the first experiment, use primary temperatures for the five component
airports and METAR temperatures only for the three nonmembers. METAR wind is
an explanatory input. This deliberately avoids assuming independent errors
between simultaneous same-airport primary and METAR temperatures. Keep those
unused METAR temperature channels available for a later paired-source audit.
Do not convert nonmember METARs into primary index contributors.

Initial/backfilled reports can initialize a state after their actual receipt,
with their ages visible. They cannot create pre-receipt forecasts or count as
evidence that the system caught a new release. Provider clock anomalies,
missing IDs and unknown source/code combinations are explicit input failures.
The collector's first-version links support this audit; source freshness is
not inferred from the timestamp of the last polling request.

## One feasible minimal experiment, with three candidates

**Purpose:** test whether publication-aware state estimation improves a small
source-matched forecast and whether its wind direction matters. This is an
engineering/development pilot. It is not financial validation, and a single
day cannot establish a weather strategy's statistical edge.

First require a registered E027 v2 with successful source identity checks and
at least twenty hours left before its unchanged stop time. Use its existing
collector; do not create another one or extend a frozen deadline. If the
prerequisite fails, report insufficient prospective coverage and keep this
experiment pending. Do not replace missing stations with v1's narrower scope.

Register model code, sources, exact forecast times and these limits before any
model fitting or forecast evaluation. Let `T0` be the first whole UTC hour
after model registration. Use `[T0,T0+6h)` only to fit the fixed small
models/calibration parameters from reports and labels already received by that
cutoff. Target labels not yet received at the cutoff are ineligible for fitting.
Complete and archive all fits by `T0+7h`; no late fit may be backdated.
The twelve evaluation targets are `T0+8h` through `T0+19h`, each with a forecast
decision exactly sixty minutes earlier. Require the collector's stop to be
later than the last target plus its five-minute publication interval.

| Candidate | Fixed specification |
| --- | --- |
| B0: local ridge | Penalty 10; fixed airport/time-of-day indicators, each component's last eligible temperature, actual age and fifteen-minute change with an explicit missing-change indicator. No upstream inputs. |
| B1: state model, actual wind | Sixteen states above; fixed trend time constant 60 minutes, mixing cap 0.2 per hour, three-hour delayed-report buffer. Primary component temperatures plus nonmember METAR temperatures; actual available airport wind drives the fixed graph. |
| B2: state model, reversed wind | Identical B1 construction, timestamps, speeds, sources, dimensionality and fit budget; wind direction is rotated exactly 180 degrees before constructing graph weights. |

Use the same source availability gate and hourly target grid for all three.
For B1/B2, use only the fixed eight airports, edges within 150 km of each target
airport, and wind at most 75 minutes old. Calm, missing or variable wind gives
zero directed transport. Normalize positive source-to-target projections with
distance attenuation; keep the same maximum mixing cap for actual and reversed
wind. Freeze these mechanics in code rather than choosing distances or station
subsets after errors. No clouds, NWP, transformer or additional candidate
belongs in this first test.

Fit only a single shared process-noise scale for each state model using the
same one-pass optimizer, initialization and bounded parameter interval frozen
in the model registration. Fix source-noise floors and precision handling from
the audited source schema. Neither those numerical bounds nor unresolved
precision assumptions may be selected by evaluation errors; an unresolved
required parameter blocks registration. Total fit CPU is capped at ten minutes,
memory at 512 MiB, and each forecast publication at ten seconds after its
decision. A late/failed forecast stays failed. Do not use six hours to fit a
large parameter set or claim robust calibration.

Forecast the distribution of the **observed canonical index at the exact
target minute**, applying the correct source/configuration mapping. Keep
estimated component states and their covariance separate from that observable
projection. Calibrate residual uncertainty using only the six-hour permitted
window. Future degraded/source-missing states remain uncertainty and can cause
forecast error; never filter the evaluation to hours later found normal.
Use the first eligible canonical value for the exact minute as the label.
If it is not observed, record a missing target; do not substitute a latent
estimate or an older index minute. This exact-minute diagnostic is narrower
than the full contract fallback rule and is not a contract-profit backtest.

**Freeze these fail criteria before the pilot:**

1. Any pre-receipt input, future-informed smoothing feature, overwritten
   forecast, duplicate-assimilation violation or invented label fails the
   experiment's integrity gate. Synthetic tests must also show that appending
   later receipts cannot change an earlier archived forecast.
2. Missing required station coverage, an unfinished fit, any unavailable
   required forecast/label, or a run beyond the fixed budget yields an
   incomplete twelve-target comparison. Retain it; do not select another
   start/end window.
3. With a complete comparison, B1 must improve mean CRPS by at least **5%**
   over B0 and have strictly lower mean CRPS than B2 to justify a longer
   mechanism study. Otherwise this pilot supplies no reason to advance the
   added transport model. Retain all MAE, interval-coverage and per-hour losses,
   including a better reversed-wind result. Do not promote B2 instead.

CRPS is a proper loss for a predictive distribution; it penalizes both misplaced
forecasts and unhelpfully broad uncertainty. The 5% threshold is a predeclared
practical screen, not a significance claim. Passing would only justify a
longer test across independent weather days and seasons. None of the
[objective's](../docs/REQUIREMENTS.md) financial validation requirements is waived.

This experiment intentionally fits from **the same live product types** it
will use in evaluation. IEM measurements do not enter its fit. That provides
a feasible baseline while the NCEI-to-live comparison is unresolved, instead
of calling an unaudited data substitution a bridge.

## Where a small transformer could fit later

The preferred later hypothesis is to predict a **residual from available
physical guidance**, rather than asking a small transformer to relearn the
entire temperature process from short station histories:

`observed_target = eligible_NWP_forecast + learned_local_residual + observation_error`.

Use filtered residual states, their uncertainty, actual input ages and
source-quality masks as past context. Known future covariates may include a
weather-model forecast **already published and received** by the decision;
they may not include future realized wind, cloud or station temperature.
Supervise against observed targets through their measurement definition.
A full-sequence smoothed latent path is not ground truth and must not become
a training feature that contains later evaluation observations.

Forecast-cycle changes need explicit handling. If a new physical forecast
changes the baseline from `m_old` to `m_new`, preserve the same temperature
belief with

`residual_new = residual_old + m_old - m_new`.

Otherwise the model may learn artificial jumps caused by new guidance editions.
Retain the guidance cycle, valid time and actual receipt for every residual;
do not recompute earlier inputs against the newest forecast file.

Chronos-2 already supports related series and covariates, and statistical
postprocessing of weather ensembles is established prior art. Neither fact
proves a residual transformer will help here.
[Chronos-2](https://arxiv.org/abs/2510.15821),
[ensemble calibration and dependence](https://arxiv.org/abs/1302.7149).
The [physical-baseline work](PHYSICAL_STATION_BASELINE.md) must first establish
the eligible guidance and comparable target. E027 does not currently collect
that guidance as a prospective model input. A transformer therefore remains
**outside** the three-candidate minimal experiment, pending the source bridge
and a simple residual baseline it can plausibly beat.

## Evidence, limitations and next scout task

This specification used frozen E027 source/configuration and existing IEM,
AWC and index documentation, plus E028's independent schema/coverage assessment
and E027 v2 registration/progress metadata. It did not inspect E028 measurement
bodies or compute E027 weather errors. Full required v2 station coverage
remains a prerequisite: the inspected metadata reports KTMB missing. No new
live collector or source file was changed.

Three documentation GETs were made under an eight-GET cap. An old academic
Kalman PDF link returned 404, preserved as raw **119776**. Publisher-deposited
bibliographic metadata was then verified through Crossref: **120788** contains
Kalman's title, DOI and abstract; **120795** identifies Bar-Shalom's title and
DOI. The latter is a bibliographic verification, not a claim to have inspected
the full article. No measurement endpoint was called.
[Documentation receipts](../evidence/live_bridge_documentation.json).

Next bounded scout task: specify a paired-product comparison for a **fixed
future UTC day already captured by E027**, once the delayed NCEI product for
that exact day becomes available. Enumerate matching airports, valid-time and
rounding tolerances, all nonmatches, and a source-offset identifiability test
before any paired temperatures are inspected. This would directly test whether
IEM can train the same measurement process, without redesigning the model
around whichever source happens to look best.
