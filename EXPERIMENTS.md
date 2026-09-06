# Experiment register

All entries below registered 2026-09-06 before strategy results. Every attempted
hypothesis, including failures and null findings, remains in this file. Results
must distinguish descriptive exploration from independent validation.

## E000 - Market/data and settlement-source audit

- Hypothesis: public sources provide enough provenance for an executable study.
- Rationale: a station or availability mismatch can produce a false edge.
- Data: full Kalshi series catalog, live weather contracts, contract PDFs, fee
  definitions/changes, historical cutoff and sample candles/trades/order books;
  official weather/forecast archival documentation and endpoint probes.
- Implementation: preserve raw GET responses and request/response timestamps;
  catalog sources and contract templates; probe representative market families.
- Parameters: no station filtering by outcome or return; full category discovery.
- Train/validation/test: none; infrastructure/descriptive study, no profitability claim.
- Robustness: compare live rules to help pages, current to archived definitions,
  API schema to documentation, empty depth to zero-valued quotes.
- Result/decision: pending.
- Next: E001; acquire missing data before any forecast P&L backtest.

### E000 observed evidence, 2026-09-06

- Universe: 13,839 total series, 367 in Climate and Weather; 5 title matches in
  other categories retained for review. 1,007 open contracts from all 367 series,
  no failed page requests in the census. This is the current catalog, not a
  reconstructed historical universe. 117/118 template URLs returned PDFs; the
  missing one is literally `/contract_terms/.pdf` and remains flagged.
- Historical samples: July 1 NYC through `/historical`, August 1, September 1/5
  NYC and September 5 07 EDT Miami hourly through `/markets`. Respectively
  1,924 / 1,141 / 1,295 / 1,437 / 5 minute candles for the alphabetically first
  contract. Sample trade pages return 10 / 10 / 10 / 10 / 1 trades (not total
  market volume). Actual raw market rules show NWS in July/August and TWC in
  September. All five events are exploratory and ineligible for final holdout.
- HRRR July 1 12 UTC forecast-hour-1 index and NBM bucket objects are accessible.
  HRRR index Last-Modified is 13:12:14 UTC, not initialization time 12:00.
  This is archive object metadata, not yet a verified original publication log.
- GFS and NBS station guidance for KNYC July 1 12 UTC returns 21 and 23 rows.
  Both carry runtime and forecast time, without an original availability field.
  NBS includes forecast standard deviation. Neither is silently admissible at
  runtime; daily max/min product windows must also be checked.
- A guessed CLI timestamp returned 404; metadata discovery then retrieved the
  actual CLINYC issued 06:19 UTC July 2 with July 1's maximum 93 F. Failed probe
  retained; correct metadata endpoint is `/json/nwstext_search.py`.
- TWC public site code disclosed the site's own `/kalshi/api/climate/primary`
  GET endpoint. Three date probes returned 37 station mappings, with explicit
  missing/preliminary/official flags. Issue times in sampled records are blank;
  today's retrieval is not historical availability evidence.
- Status: retain public data sources for continued research. Missing historical
  depth and source/publication provenance still prevent an executable backtest.
- Reproduction: `uv run python research/experiments/e000_data_availability.py`.
  Reports: `E000_data_availability.json`, `historical_data_probe.json`,
  `weather_data_probe.json`, `twc_source_probe.json`, `station_registry.json`.
  The rerun has five market probes and six weather probes with zero errors;
  the earlier incorrect URL attempts remain archived.

## E001 - Complete bracket basket consistency

- Hypothesis: exhaustive mutually exclusive outcomes can occasionally be bought
  below their guaranteed aggregate payout after fees and depth costs.
- Rationale: separately traded binary order books may be inconsistent.
- Data: full live event membership, rules and direct book snapshots, no midpoints.
- Implementation: validate integer bracket partition covers all possible values;
  price one YES on every bracket and one NO on every bracket. YES payout is $1;
  NO payout is k-1 for k brackets. Require all legs and retained depth.
- Parameters: synchronized snapshot window <=5 seconds, >=1 unit per leg;
  fees under current series/event schedule; slippage 0/1/2 cents per leg,
  depth retention 100/50/25%; sequential legs explicitly non-atomic.
- Train/validation/test: exploratory snapshot census only; independently collected
  subsequent observations required for latency-survival and execution validation.
- Robustness: reject missing/overlapping brackets, missing books, mismatched
  source/date, stale/closed markets; re-read profitable candidates after latency;
  no claim of fill or atomic arbitrage from a displayed basket.
- Result/decision: pending.
- Next: E002 and event-time replay if a candidate survives re-observation.

### E001 coverage amendment, 2026-09-06 10:31 UTC

Initial census classified `KXHIGHTLV` ("Max Daily Temperature") and `KXLOWTMIN`
("Low  temperature") as temperature_other, excluding them from E001. Repair title
normalization and include both, then repeat the entire census. This is a coverage
correction, not a performance-based city choice. Initial 47-event results remain
archived; 46 observed baskets had no positive conditional net result. The other
event was a nonexclusive year-end contract. Current contract PDFs do not prove
an integer settlement domain: report continuous-domain and integer-assumption
payout bounds separately, and do not treat a YES integer partition as guaranteed.

### E001 results, 2026-09-06 10:32 UTC

- Corrected census: 49 events considered, 48 daily events observed, 1 rejected
  (`KXSJCLOWT-26DEC31` is not a mutually exclusive daily partition).
- 149 side/scenario combinations had sufficient displayed depth for all legs.
  Zero positive net basket results under either continuous-domain bounds or the
  favorable integer-domain assumption. Best conditional integer net: -$0.010700.
- Mechanism diagnosis: near-break-even NO baskets are eroded by fees; other
  baskets also suffer spread/depth constraints. Best favorable NO basket before
  fees was +$0.03, fee $0.0407. No modeled fill was claimed.
- Robustness: all three preregistered depth/slippage scenarios, all daily cities
  after title normalization; original results retained. Offline replay reproduced
  all 48 events and 149 fully quoted scenarios from original response bytes.
- Decision: reject these observed baskets as trades. Do not reject the mechanism
  permanently from two morning cross-sections. No independent-day confidence
  interval, annualized result or bankroll simulation is justified.
- Next: repeated prospective capture and forecast/observation latency hypotheses.
  Current capture is market/observation data collection, not model shadow trading.
- Artifacts: `reports/E001_baskets_initial.json`, `reports/E001_baskets.json` and
  append-only `experiment_report` records. Replay: `uv run weatherpred replay-baskets`.

## E002 - Market-implied baseline and tail calibration

- Hypothesis: market probabilities have systematic tail calibration errors that
  remain after selection, spread, fees, and adverse selection.
- Data: historical settled contracts and bid/ask candles, archived rule versions;
  executions require captured depth and cannot be inferred from candles.
- Implementation: fixed decision offsets and chronological splits across all
  available weather stations; market scores first, returns only with fill evidence.
- Parameters: 24/12/6/3 hours before observation-period end; bins fixed at deciles;
  event/day clusters; no best-city filtering.
- Train/validation/test: assign and seal ranges after coverage audit, before
  reading outcomes. Downloaded exploratory outcomes excluded from final holdout.
- Robustness: both bid and ask score bounds, historical fee variants, shifted
  labels within seasonal blocks; change-point sensitivity for settlement sources.
- Result/decision: pending.
- Next: E003 after historical forecast availability is established.

## E003 - Forecast baselines and incremental information

- Hypothesis: station bias and forecast dispersion add predictive information to
  the market; intraday observations improve distributions conditionally.
- Data: as-issued HRRR/NBM/GFS/GEFS/ECMWF products and target-source observations.
- Implementation: market, individual/average forecast, bias correction, logistic
  calibration, ensemble output statistics, observation-conditioned distributions.
- Parameters: predefine grids inside training folds; no model complexity without
  validation gain; common event sample for all model comparisons.
- Train/validation/test: pending coverage; sealed chronological ranges before fits.
- Robustness: publication/revision/model-version audit, no reanalysis substituted
  for forecasts, weather-system block resampling, forecast-latency perturbations.
- Result/decision: pending.
- Next: separately register update latency, cross-city, maker and specialization
  experiments based on evidence; count them in the multiple-testing family.

## E004 - Miami index observation latency

- Hypothesis: station readings/partial index information add predictive value
  before an hourly market closes, and executable prices react with measurable delay.
- Rationale: the publicly documented index forms final minute values at a receipt
  deadline; weather persistence and cross-station observations may constrain the
  distribution before publication. The deadline itself does not grant a tradable
  post-close opportunity.
- Data: captured index responses (including pending states), five constituent
  stations with receipt times, calibration publication/effective times, hourly
  contracts and contemporaneous depth. Backfill with `receipt_basis` is excluded
  from settlement labels; final-published values cannot replace past pending states.
- Implementation: next-step replay of decision-time observations; persistence,
  linear trend, NBM and dispersion-aware calibrated baselines. Measure price
  response and markouts (subsequent executable prices) at 1/5/30-minute horizons.
- Parameters: decisions 30/15/5 minutes before close; labels selected under the
  5-minute publication deadline and inclusive 60-minute fallback rule. Per-day
  clusters, chronological fit and selection. Fit thresholds only within training.
- Train/validation/test: not assigned; coverage and source-version audit first.
  The already inspected August 18 backdated calibration is exploratory evidence.
- Robustness: late/missing observations, day/night and convection regimes,
  degraded/fallback states, unchanged-signal and delayed-signal placebos; taker
  prices plus fees. No maker fill without queue and aggressor-trade evidence.
- Result/decision: untested. Current capture creates necessary prospective data.
- Next: reconcile index finality/version history, obtain historical label coverage,
  preseal date ranges and compare baselines before any strategy selection.

## E002 dated implementation and coverage, 2026-09-06

- The date protocol was fixed at 10:44 UTC in
  `config/e002_market_baseline.json`: train January–June 2025, validation
  July–September, sealed final holdout October–December. No holdout scores or
  candle requests are permitted before a separately frozen candidate release.
- Census: 101,497 raw market rows from both API tiers across all 111 discovered
  daily temperature series, no failed page requests. Eight series have training
  coverage; Houston ends January 28 and remains in training/coverage. Seven
  series have all 92 validation dates. Current discoverability remains a caveat.
- Coverage initially labeled abbreviated `NWS` as unresolved. The source parser
  now recognizes that explicit abbreviation as well as the full agency name.
  The original coverage artifact is retained; this was a text-classification repair.
- `config/e002_source_windows.json` fixes city standard-time offsets based on
  primary contract text and NWS climate-report documentation. Observation-period
  end differs from exchange close. The comparison requires both the predefined
  source-period offset and recorded market opening/closing times. This is not
  authorization to join weather features to a guessed station.
- Initial metadata processing stopped on absent `strike_type`. Audit found
  **188 affected contracts, all winners**. Dropping them would selectively remove
  positive labels. Explicit numeric primary rules recover every predicate; all
  11,610 development/validation contracts reconcile with their available numeric
  settlement values. Recovery provenance is recorded, no result-based inference.
- Acquisition: one 60-minute candle request per development contract, covering
  all four whole-hour decision boundaries. No future candle, no synthetic fill;
  successful raw replies are reused. Scores await complete acquisition.
- Next: raw market versus frozen logistic calibration, event/day weighting,
  missing-quote audit, daily block confidence analysis. No profitability conclusion.

## E004-v1 baseline results, 2026-09-06

- Frozen before forecast errors: `config/e004_index_baselines.json`, registered
  10:44 UTC. Train August 20–31 (12 days); validation September 1–5 (five days).
  The first validation hour is excluded because decisions precede the fixed
  fitting cutoff. There are 288 training hours and 119 validation hours, each
  evaluated at 30/15/5 minutes: 864 training and 357 validation examples.
- Source audit: 24,512 minute points over 17 days plus a preceding hour; 28
  missing minutes. All 3,950 contracts in 395 listed events match both index
  values and binary settlement results. An endpoint-inclusive September 6
  midnight event falls outside the protocol and is excluded before analysis.
  Matching settlement does not prove initial-publication or revision provenance.
- Models: persistence and a 30-minute linear temperature trend, each with a
  training-residual Gaussian or empirical distribution. Gaussian bias and sample
  standard deviation are fitted on training only (0.05 F minimum scale).
  Empirical exceedance probabilities use Jeffreys smoothing. Both respect the
  0.01 F rounding grid. CRPS measures distribution error in degrees Fahrenheit;
  Brier and logarithmic loss measure binary probability accuracy.
- Historical features impose an **unverified ten-minute publication lag**;
  historical P&L is prohibited. Candle prices are retrospective benchmarks only.
  118 batch requests acquired validation quotes with zero errors. Of 3,510
  contract/horizon candidates, 1,219 have eligible two-sided quotes; 2,291 do not.
  Two validation hours lack a listed event. Every model comparison uses the same
  eligible contracts, averaged within event before comparison.

| Minutes before settlement | Paired events | Market Brier | Persistence empirical | Persistence Gaussian | Trend empirical | Trend Gaussian |
|---|---:|---:|---:|---:|---:|---:|
| 30 | 117 | 0.09923 | 0.13493 | 0.13937 | 0.14007 | 0.13950 |
| 15 | 116 | 0.09224 | 0.11387 | 0.11951 | 0.13641 | 0.13535 |
| 5 | 116 | 0.08873 | 0.11623 | 0.12251 | 0.13385 | 0.13133 |

- Lower scores are better. All four models also lose on log loss at every horizon.
  The best baseline loses to the market on all five days at 30 and 5 minutes,
  and on four of five days at 15 minutes. Do not treat hundreds of contracts as
  independent trials; five days cannot justify promotion or robust confidence.
- Full-validation empirical-persistence CRPS is 0.600 / 0.415 / 0.311 F for
  30/15/5-minute decisions; RMSE is 1.184 / 0.799 / 0.570 F. These weather scores
  do not establish incremental information relative to tradable prices.
- Validation diagnostics include event-weighted decile calibration and daily
  score differences. Offline replay from original bytes exactly reproduces
  scores and coverage: original report 3899, replay 3947, zero network requests.
- Decision: reject these frozen baselines as evidence for trading; retain them
  as benchmarks. The ten-minute feature lag and short, changing-weather sample
  are candidate constraints to investigate prospectively, not excuses to change
  the result. No model is selected for capital allocation.
- Next: compare fresh observations and forecast products using actual publication
  receipts, with any new model registered before new forward outcomes.

## E004-forward-v1 registration, 2026-09-06 10:58 UTC

- Pin original model artifact **3768**, published before historical validation
  scores. Offline reruns may create new identical artifacts but do not change
  the running logger's pinned model.
- Nine future slots: 11:30/11:45/11:55, 12:30/12:45/12:55 and
  13:30/13:45/13:55 UTC for the following hourly settlements. Missed slots are
  never backdated. Actual collection/computation delay must be at most 15 seconds.
- Preserve model and raw request lineage, feature timestamps, probabilities,
  current fees and displayed depth-cost scenarios before settlement. Quantity
  and recommended real-money size are **zero**; intended action is abstain.
  No confidence lower bound is invented and no fill is claimed.
- Separate OS lock and `data/STOP_SHADOW`; a bounded four-settlement-hour run.
  This is instrumentation and prospective forecast validation, not evidence of
  shadow profitability. First scheduled observation is 11:30 UTC.

## E002 scoring implementation, 2026-09-06 11:17 UTC

- `config/e002_scoring.json` fixes implementation details before inspecting any
  E002 probability scores. One intercept and slope per horizon calibrate clipped
  market log odds; L2 penalty 1 on slope, intercept unpenalized, analytic-gradient
  optimization with no parameter search. Each calendar day has equal weight;
  events share day weight, and eligible contracts share event weight.
- The recorded `settlement_ts` must precede the July 1 00 UTC fitting cutoff.
  A June event date alone does not make its outcome available then. Missing/late
  training labels are excluded explicitly; source-period end and numerical market
  opening/close gates remain separate. The historical revision caveat remains.
- Validate raw midpoint, bid, ask and calibrated probabilities on identical
  exact-boundary two-sided quotes. Report day-weighted Brier/log loss, calibration,
  and bracket probability sums. Independent per-contract calibration does not
  yet provide a coherent production distribution across all brackets.
- Paired circular day blocks of length 1/7/14, 10,000 resamples, fixed seed;
  approximate improvement p-values with Holm adjustment across four horizons
  per block length. These are development diagnostics, not global promotion tests.
- Exact source bytes and protocol are archived before the waiting runner starts.
  `e002_baselines.py --wait-seconds 7200` waits for complete successful acquisition
  accounting before fitting/scoring. It must not interpret partial downloads as
  complete evidence. The final holdout remains inaccessible.

## E000 source-transition map, 2026-09-06 11:25 UTC

- Across 15,088 daily events in all 111 discovered temperature series, 40 series
  transition from NWS primary-rule text on August 13, 2026 to TWC on August 14.
  No event has mixed source labels across its contracts in these retrieved records.
- `reports/E000_source_transitions.json` preserves every source run and raw
  record references. Outcome labels were not scored, including in the sealed
  period. This locates a transition in today's retrieved metadata, not proof of
  original listing text or absence of retrospective edits. Generic PDF conflicts
  and exact TWC observation-window/precision questions remain unresolved.

## E005-v1 — Pending index arithmetic and publication lead

- Hypothesis registered before errors/returns: the public API's incomplete
  points can contain all five pending station temperatures before the numeric
  canonical index is published. These may be useful forecast inputs.
- `config/e005_pending_index.json` requires all five primary members, eligible
  receipt times, hard numeric range and an effective calibration already archived
  by the decision. Apply the published weighted Celsius formula, then convert to
  Fahrenheit. Pending readings are explicitly provisional and have not completed
  source QC. They never replace settlement labels.
- The archived official methodology supplies station offsets and the full-roster
  city reference. Exact source precision is retained; nearest-cent half-up and
  half-even alternatives are both reported because tie handling is unspecified.
- Historical arithmetic audit: **24,511/24,512** canonical values reproduced.
  One four-station METAR fallback value remains a one-cent discrepancy: source
  `temp_f=84.91999999999999` yields `85.7749999999999975`, versus published 85.78.
  Inference: converted numeric precision near the half-cent boundary is the likely
  cause. Do not round the inputs or conceal the mismatch to force agreement.
- Live audit at 11:23:53 UTC: 104 captured responses, 56 eligible first-pending
  minutes; 53 matched later canonical publications and three remained censored.
  All 53 provisional estimates matched exactly. First-seen lead in our polling
  channel: median 181.14 seconds, range 60.24–227.28 seconds. No changed pending
  estimate or later canonical value observed in this small snapshot.
- There are only **three distinct values** among those 53 paired minutes and
  only one calendar day. This is operational reconstruction evidence, not 53
  independent trading trials. Others may obtain information earlier; market
  reaction was not measured. A settlement-minute reading arrives after that
  market has already closed, so this is not a post-close arbitrage.
- Decision: retain the provisional-input mechanism for prospective nowcasting
  research; no probability advantage, fill or profit claim. The unresolved
  fallback precision case remains excluded from claims of exact reconstruction.
- Next: compare fresher canonical/provisional inputs with the market using new
  registered forecasts, actual receipt lineage and realistic pre-close execution.
  `reports/E005_pending_index.json` and `E005_pending_pairs.jsonl` preserve results.

## First actual E004 prospective decision, 2026-09-06 11:30 UTC

- Archive record 9292, model 3768, ten contracts for KXTEMPMIAH-26SEP0608.
  Publication 11:30:01.925383 UTC precedes settlement at 12:00 UTC; actual
  collection/computation delay 1.925 seconds. No skip or claimed fill.
- Every contract records model probabilities, source lineage, current fee/depth
  cost scenarios, missing confidence bound, abstention and quantity zero.
- New `score-shadow` command checks immutable lineage, unchanged predicates,
  complete membership and finalized results before scoring. First actual run:
  one forecast, zero finalized/scored decisions, zero errors. Outcome pending.
- Initial 120-cycle collector finished normally at 11:31:29. A replacement
  720-cycle run began 11:32:09 to continue collecting through subsequent forecasts.
  Startup now refreshes listings immediately rather than waiting ten minutes.

## E006 — Canonical input freshness, registered 11:45 UTC

- Rationale: E004's four simple forecasts lost to market probabilities. Test
  whether five-minute canonical inputs improve on its ten-minute inputs using
  new prospective outcomes. Do not retune on the five examined validation days.
- Protocol: `config/e006_fresh_index.json`; model record **12323**, frozen at
  11:45:07 UTC before all six decisions, 12:30/45/55 and 13:30/45/55 UTC.
- Training: the same August 20–31 period; 864 examples, 288 per horizon. Date
  filtering precedes label use. Five-minute historical release timing remains an
  assumption for fitting; no new historical validation or P&L was calculated.
- Four models retained: persistence/trend × Gaussian/empirical errors. All four
  original model probabilities are retained as separate comparators. Both groups
  use the original logger's exact raw index response and quote snapshot time.
- Consumer publication must be within 20 seconds of the scheduled slot and before
  settlement. The model must have been archived before the scheduled decision.
  Later responses, invalidated parents, missing slots and stale inputs are rejected.
- No quantity, fills, confidence/promotion claims or model selection from the
  initial two-hour engineering run. The raw quote snapshot can be older than the
  consumer publication; it supports a paired probability comparison only.
- Tests cover future-point exclusion before fitting, unchanged original snapshot
  time, model/feature availability, late publication and invalidated parent records.
- Result: model registered and consumer waiting; no E006 future outcomes yet.

## E003 — Original NBM source acquisition, 11:52 UTC

- Protocol: `config/e003_nbm_acquisition.json`, archived before broad acquisition.
  January–September 2025 only; October–December remains sealed. One daily 01 UTC
  NBS forecast for KAUS, KMDW, KDEN, KHOU, KLAX, KMIA, KNYC and KPHL.
- Source breakthrough: NOAA public S3 retains original station text and object
  timestamps. January 1 object record **12809**, 29,853,302 bytes, MD5 matches its
  ETag. Runtime 01:00; LastModified **02:10:17 UTC**, a 4,217-second difference.
  Do not substitute runtime for release time. Historical public-access permissions
  and first dissemination are still not independently proven by object metadata.
- `reports/E003_source_audit.json`: eight exact station cards, 184 forecast rows;
  KNYC original text versus IEM parsed record 12531: **115** temperature,
  uncertainty and valid-time comparisons, **zero mismatches**. One date/product,
  not a forecast skill result. Audit script makes zero network requests.
- NBS columns are fixed width. Adjacent three-digit/negative numbers, missing
  sentinels and duplicate/truncated station cards are tested. Runtime plus forecast
  hour must agree with the UTC column. Product version is retained per station.
- Acquisition uses exact-object listings, ETag-conditional byte ranges and matched
  LastModified headers. Prior offsets are hints only; a moved station triggers
  full-object recovery. Missing days and failed hints remain in the evidence.
- Observed through January 31: 31 complete days, eight stations each, zero errors.
  Broad acquisition and model fitting remain in progress/pending respectively.
- Failed probes retained: IEM JSON searches returned empty arrays (12535/12538);
  direct pseudo-station text retrieval returned HTTP 200 error text (12737/12740).
  These responses are not mistaken for available forecasts.
- Methodological limit: TXN predicts an 18-hour extreme; 3-hourly TMP samples also
  do not equal the contract's continuously observed midnight-standard-time daily
  maximum. Register a forecast-to-target calibration and exact source/window audit
  before fitting, scoring or making any trading inference.

## E002 — Daily calibration results and retained failures, 12:21 UTC

- All 11,610 historical quote requests finished at 12:12:25, zero request errors.
- The first scorer stopped without fitting: historical candles have fixed-dollar
  `close` fields, while live candles use `close_dollars`. Official documentation
  and actual raw replies agree. A separate strict schema adapter now translates
  those fields, preserving the original exact-time/open/spread/fit-cutoff gates.
  The pinned Miami `forecasts.py` implementation was not changed.
- The corrected run froze model **20410** before validation, then stopped when
  the original block estimator rejected nonconsecutive eligible dates. The failed
  run and its model remain retained. `e002_recover_report.py` uses that exact model
  and verifies the original quote-dataset hash; it performs **no refit**.
- Recovery protocol **20466** leaves missing block confidence unavailable and
  does not shrink the four-horizon Holm family. This is incomplete inference,
  not permission to compress missing calendar days or replace the estimator.
- 15,904 eligible train/validation quote examples. Validation comparisons:

| Hours before source-day end | Eligible days | Events | Contracts | Raw market Brier | Logistic Brier |
|---|---:|---:|---:|---:|---:|
| 24 | 92 | 644 | 3,088 | 0.134380 | 0.134178 |
| 12 | 92 | 634 | 2,020 | 0.145044 | 0.144686 |
| 6 | 28 | 34 | 61 | 0.080686 | 0.080856 |
| 3 | 5 | 5 | 9 | 0.072085 | 0.072540 |

- Seven-day-block 95% intervals for logistic-minus-market Brier:
  24h -0.000202 [-0.000583, +0.000170]; 12h -0.000358
  [-0.001081, +0.000387]. Both include zero. Twelve comparisons at the 6/3-hour
  horizons lack block confidence because eligible dates are not consecutive.
- Decision: tiny early-horizon descriptive gains are not a validated edge.
  No historical P&L, fills or final-holdout evaluation. Later observations and
  conditional incremental information require separately registered experiments.

## E003 — NWS source reconciliation and daily forecast results, 12:19 UTC

- NBM acquisition finished at 12:13:06: all **273** development dates and **2,184**
  station cards, zero acquisition errors. One 01 UTC object, May 27, was stored
  only at **18:59:54 UTC**. The availability gate excludes all seven listed events
  that day instead of pretending the forecast existed at initialization.
- Original NWS reports were acquired from eight CLI products, with all versions
  preserved. First audit failed on duplicate ZIP filenames. Record 17708 contains
  an original Austin report and later CCA correction under the same filename.
  Protocol v2 reads individual ZIP entries, retains byte hashes and uses the later
  corrected WMO issue time when explicitly marked; ambiguous unmarked discrepancies
  remain errors. It never silently reads only the last duplicate name.
- `reports/E003_NWS_audit.json`: 1,935 events; **1,909 match**, 24 have no eligible
  complete report at recorded settlement, and two numeric values differ by one
  degree. Eleven source timestamp inconsistencies and five missing maxima retained.
  Denver June 20: exchange 98 vs NWS 99. LAX July 4: exchange 73 vs NWS 74.
  **Neither discrepancy changes any listed contract's binary payout**; both cases
  are excluded from continuous weather-model fitting pending reconciliation.
- Model protocol **19991** registered before temperature-error/model-score
  computation. Full scorer registration **20424**; model **20425** frozen before
  validation. Train January–June 2025; validate July–September; final holdout sealed.
  After source/availability gates: **1,254 training events across 179 days** and
  642 validation events across 92 days. The global 180-day minimum is not met;
  this is exploratory baseline evidence, not promotion evidence.
- Six models: native TXN/XND Gaussian proxy, pooled/local Gaussian grid errors,
  local empirical grid errors with one Gaussian prior observation, local TXN error
  correction, and linear mean/spread regression. TXN is explicitly an 18-hour
  proxy; the other models calibrate forecast features to verified 24-hour labels.
  The regression uses fixed regularization and analytic Gaussian-likelihood
  gradients; no hyperparameter search or complex ML.
- All probabilities integrate a single distribution over integer outcome cells.
  All full-event partitions agree with one to at most **2.22e-16** numerical error.
- All six weather models trail the same market quotes at every registered horizon:

| Model | Brier at 24 hours | Brier at 12 hours |
|---|---:|---:|
| Market midpoint | 0.134332 | 0.145231 |
| Native 18-hour Gaussian proxy | 0.143401 | 0.210730 |
| Pooled Gaussian grid errors | 0.155346 | 0.225606 |
| Local Gaussian grid errors | 0.155812 | 0.223109 |
| Local empirical grid errors | 0.153564 | 0.220625 |
| Local Gaussian TXN errors | 0.151823 | 0.220817 |
| Mean/spread regression | 0.146107 | 0.212194 |

- On the paired sample, 24h uses 3,078 contract quotes / 642 events; 12h 2,015 /
  632 events. Late horizons have only 61 and 9 quotes; 72 weather-model confidence
  comparisons remain unavailable. No reduced-family multiplicity claim.
- Seven-day-block native-minus-market Brier intervals: 24h +0.00907
  [+0.00504, +0.01297]; 12h +0.06550 [+0.05455, +0.07663]. These are development
  diagnostics, not globally adjusted claims ruling out every weather mechanism.
- Spread regression improves 95% interval coverage to 95.78% versus 80.36% for
  the native Gaussian proxy, but does not beat its MAE (1.55F vs 1.40F) or market
  probability scores. Better uncertainty calibration alone did not create an edge.
- Diagnosis: fixed 01 UTC forecasts lose information as the day progresses;
  market prices are stronger even at day start. Standalone replacement is not
  supported. Next test incremental weather information conditional on the market,
  and actual forecast/observation updates, with new registrations and corrections
  for all attempted candidates. Do not merely add model complexity.
- `e003_replay.py` reconstructs original NBM cards, selected NWS versions, frozen
  training lineage, complete score membership, probabilities and day/event
  aggregation with no refits/network. `reports/E003_replay.json` records its output.

## E006 — First actual paired forecast, 12:30 UTC

- Original forecast **20713** published 12:30:01.909333 UTC; paired five-minute
  forecast **20714** published 12:30:02.048101 UTC, both before the 13:00 event.
- Ten contracts; all four original and four new model probabilities retained.
  New model remains **12323**, frozen 11:45:07. Quantity zero; outcome pending.
  No skips for this slot and no execution or performance inference.

## E007 — Conditional weather information, 13:13 UTC

- Hypothesis: weather guidance can add information conditional on market prices
  even when it loses as a standalone forecast. This hypothesis follows inspected
  E003 development results; July–September remains development, never a new holdout.
- Protocol21015, scorer21029; model21035 frozen12:44:34.413404 before new scores.
  Six probability pools with market/weather exponents bounded0..3, fixed L2
  regularization toward market-only, and equal-day multinomial likelihood.
  Market distribution normalizes all six contemporaneous midpoints. Probabilities
  sum to one; normalized prices are forecasts, not executable purchase prices.
- Monthly chronological cross-fitting: February–June training forecasts come from
  weather models fitted only on earlier days with NWS issue and exchange settlement
  before the month boundary. Final weather parameters reuse E003 model20425.
  Five fold artifacts21030–21034 retain source hashes; no tuning on validation.
- Complete six-bracket quote requirement is unchanged: exact timestamp and
  0<bid<ask<1 for every member. After February1, 24h fitting has365events138days;
  12h has32events28days, below the preregistered30day minimum, and6/3h have none.
  Those horizons remain unavailable. Validation24h:160events68days960contracts.

| Forecast | Brier | Binary log loss | Multiclass log loss |
|---|---:|---:|---:|
| Raw market midpoint | 0.11529951 | 0.36431105 | Not a normalized partition |
| Normalized market | 0.11526981 | 0.36371086 | 1.36050378 |
| Fitted market-only temperature | 0.11517919 | 0.36315729 | 1.35730509 |
| Market + native weather proxy | 0.11393294 | 0.35737846 | 1.32794571 |
| Market + spread regression | 0.11610188 | 0.36392754 | 1.35835211 |

- Four other calibrated weather pools receive exactly zero weather weight and
  reproduce the fitted market-only benchmark. Native pool weights: market0.93003,
  weather0.18746; spread pool0.92649/0.42573. Market-only exponent1.13496.
- Native combination is a small descriptive improvement, not a validated edge.
  Calendar gaps prevent every registered block-confidence calculation; missing
  horizons and references do not permit a reduced multiple-testing family.
- Independent replay:5folds397training event/horizons2250weather distributions,
  all8640saved score rows and their event/day weights; max difference8.88e-16,
  zero refits/network. Original inputs reconstructed from pinned source records.
- Next: test whether that conditional probability gain survives purchase costs,
  then collect actual future fill evidence and investigate updated forecasts.

## E008 — Conditional quote-cost screen, 13:13 UTC

- Protocol21231 before selected-trade outcomes; full scorer21272. Parent21035.
  All9E007 forecasts ×3cost assumptions retained, no model selection/refitting.
- At each of160complete24h events choose at most one conditional YESatask or
  NOat1-bid with highest positive estimated net edge. The selection function
  receives no outcomes. One contract, held to final resolution. No reinvestment.
- These are conditional historical quote calculations, **not observed fills**.
  The2026quadratic fee schedule is explicitly a cost assumption; historical2025
  fees, available size, receipt-time latency and actual execution are unverified.

| Cost assumption | Native-weather pool: purchases / conditional P&L | Spread pool: conditional P&L |
|---|---:|---:|
| No fees/slippage |147 / +$1.4300|-$6.0000|
| Fee assumption +1cent slippage |92 / -$2.0155|-$5.8472|
| Cent-rounded fee +2cents |62 / -$2.8100|-$7.0300|

- Market-only temperature has+$1.2676 on14conditional purchases in the middle
  scenario and+$0.22 on3in the last. Raw midpoint never crosses its own spread.
  No candidate establishes a statistically credible gain: minimum Holm-adjusted
  p-value across81model/scenario/block diagnostics is1.0.
- For this trading policy, all92calendar dates are retained; no eligible event or
  no positive edge means an explicit no-purchase decision and zero conditional
  P&L. These zeros are not fabricated probabilities; E007's missing-date forecast
  confidence remains unavailable. No growth, drawdown, ruin or target-hit inference.
- Diagnosis: the small native probability improvement disappears under cost
  assumptions before verified fills. Retain the failed selection and all scenarios.

## E009 — Live paper broker, registered and running at13:10 UTC

- User explicitly requested paper fills and more quantitative model development.
  Protocol/configE009-live-paper-v1, registration21756, reused fresh parameter
  artifact21755. No refit; source model12323 and original3768 remain unchanged.
- Twelve future slots13:30/45/55 through16:30/45/55 UTC, bounded service until18:30.
  Consume existing original forecasts through13:55, then use separately registered
  later slots with the same frozen model. No past-slot fill reconstruction.
-32independent virtual$100accounts:8original/fresh hourly distributions×4execution
  assumptions. Takers:1s/full depth/no slippage,5s/half depth/1cent,
  30s/quarter depth/2cents. Maker:5sarrival,60secondexpiry, full queue ahead and25%
  participation in excess opposite-direction trade-through volume.
- Orders are permanently journaled before their delay. Only a subsequently
  requested fresh book can support taker fills. Partial depth fills consume cash,
  fees and position size; IOC residue is cancelled. Post-only crossing rejects.
  Maker touches/block trades/old trades/unknown direction never fill. Book
  cancellations do not advance queue. Trade IDs are deduplicated per order.
- Decision sizing:0.25fractionalKelly with a fixed3cent probability haircut,
  intended whole contracts,5%event/10%Miami cluster caps including pending cash,
  no overdrafts,20%marked drawdown halt. This haircut is not a statistical bound
  and all positions are virtual. No strategy recommendation or promotion.
- Actual contemporaneous fee metadata is archived and rechecked at arrival;
  per-order rounding accumulator persists through partial fills. Settlement waits
  for finalized unchanged contracts. At13:10 the12:00event was only determined,
  with3600second settlement timer and no settlement timestamp; cash is not released.
- Replayable ledger tracks orders/arrivals/queue/fills/cancels/cash/fees/positions/
  settlement. Restart cancels unobserved outstanding orders; STOP_PAPER cancels
  open orders while retaining positions. After bounded stop, --settle-only can
  service eventual finalized outcomes. Report/dashboard update every30seconds.
- First read-only audit13:13:12 reproduced224journal events and32accounts; zero
  orders/fills before first future slot. Actual live paper outcomes remain pending.
  Historical quote scenarios are separate from this prospective simulation.

## E010 — Original forecast updates, registered13:17–13:25 UTC

- Acquisition protocol22361: all273development dates,07/13UTC original NBS
  objects,8stations/cycle. Separate archive namespace preserves E00301UTC sources.
  Conditional ETag ranges, exact station/runtime/version checks, actual object
  storage times, missing requests and full-file recoveries retained. Bounded546
  objects,20fullrecoveries/20GiB free-space floor. No holdout objects requested.
- January1 07UTC raw source22373 was stored08:07:45; its first TMP valid time is
 12UTC. New cards omit early source-day hours, so a new forecast cannot silently
  replace the entire24hour target window. First changed station offset required
  one original full-file recovery. Acquisition remains running, not yet scored.
- Updated-model hypothesis23967; full scorer25120 before errors or fitting.
  At12hours before source-day end, choose the latest available cycle for each
  exact three-hour valid time; retain older forecasts for omitted early hours.
  These are forecasts, never invented observed temperatures. Latest next00UTC
  TXN/XND stays an explicitly18hour proxy; its target calibration remains empirical.
- Six unchanged E003 distribution methods, training labels settled/issued before
  July1; same chronological development quarter, quote gates and station sample.
  Compare updated distributions against their original frozen versions and market
  probabilities; retain all candidates, errors and fallback sources. No tuning.
- Before fitting, clarify that the day-start control intentionally uses01UTC only:
  a07UTC object could legitimately be stored before a western station's08UTC day
  starts. Two feature tests retain station/day/runtime identity, prior-hour coverage,
  and exclusion of unavailable later files. No existing assertion was weakened.
- Scorer waits for acquisition accounting. Updated-model scores, profitability,
  fills, final-holdout access and promotion remain absent at this registration.
# Forward execution checkpoint — 2026-09-06 13:35 UTC

E009 registration21756 produced its first scheduled paper round at13:30UTC:
32orders across32alternative $100 accounts,24simulated taker fills and8unfilled
maker orders cancelled at expiry. All comparisons concern one underlying hourly
event, so these are not independent observations. At13:33:05 provisional equity
spanned$98.5715–$100, with24open positions and no finalized settlements. The
read-only raw-data audit reproduced32accounts/orders,24future-book fills, cash
and fees, with no network requests; no maker execution was inferred from touches.
All13registered source hashes remained unchanged. Profitability remains unproven.

E010 source acquisition v1 stopped at295of546cycles after its20full-file recovery
limit; no acquisition errors. Every fallback followed a KAUS station byte-offset
change (largest successive change16500bytes). Before any updated model results,
resource-only v2 widened ranges to65536bytes either side and capped recoveries at
40total including20cached. It reused the existing records and restarted13:34UTC.
The546object census, station selection, information gates and model validation
rules are unchanged. This is a download continuation, not a hypothesis change.

## E010 result and independent replay — 2026-09-06 13:45 UTC

- Acquisition completed at 13:44:25: all 546 cycles and 4,368 station cards,
  zero errors. Wider byte ranges required no further full-object recoveries.
- Model 30313 was frozen at 13:44:32 before validation. Training comprises
  1,254 events over 179 days; the global 180-day minimum is still unmet.
- The same 2,015 eligible 12-hour contract quotes cover 632 events and all
  92 development days. All six updated methods lose both probability-error
  measures against market prices. The best updated Brier score, where lower
  is better, is 0.207857 for spread regression, versus market 0.145231 and
  calibrated market 0.144874. This is not an untouched final test.
- Updates improve the three grid baselines against their older versions:
  Brier differences are -0.010477, -0.008747 and -0.008449. The seven-day
  resampling diagnostics exclude zero and have adjusted p-values 0.001200.
  They remain materially worse than the market. Spread regression's improvement
  interval includes zero; no model shows a positive market-relative result.
- Every selected extrema proxy came from 07 UTC: later 13 UTC cards update
  afternoon grid temperatures but do not supply the same next-00 UTC extrema
  proxy fields. The 18-hour proxy remains distinct from the NWS daily maximum.
- `e010_replay.py` independently reconstructs raw NOAA object/listing identities,
  all 4,368 updated station cards, all 1,896 feature rows and their storage-time
  gates, training hash, complete 28,210-score membership and event/day averages.
  Maximum numerical difference 5.55e-16; zero refits/network requests.
  The separate E003 replay also reconstructed 2,184 original cards and 1,911
  selected NWS report versions this session. No holdout was opened.
- Disposition: retain updated inputs as a stronger weather baseline. Reject
  these six standalone methods as demonstrated market-beating strategies.
  Next hypothesis: combine forecasts with observations already received during
  the source day, subject to strict original-report and receipt-time evidence.

## E009 second round, partial maker fills and price diagnostics — 13:49 UTC

- Two scheduled rounds produced 61 orders and 51 simulated fill records:
  45 taker fills and six partial maker fills. Three additional decisions abstained.
  The maker records represent two public trades reused across three alternative
  accounts, not six independent opportunities. Each account bought 4.28 then
  1.25 NO contracts at $0.08 after the full queue ahead cleared; residual orders
  expired. Fees were zero for makers under the verified series schedule.
- Audit at 13:47:16 reproduced 2,568 journal events, 32 accounts, all 61 orders,
  45 future-book taker fills, six trade-through maker fills, cash and fees.
  There were no finalized paper settlements. All 13 live broker source hashes
  remain unchanged. The $100 accounts were provisionally worth $97.23–$100 in
  the displayed 13:48:52 snapshot, excluding exit fees; no realized return claim.
- Descriptive diagnostic v1 (29824) measures separate-order bid liquidation
  quotes at entry, then 30/60/300 seconds after the last fill, including assumed
  sale fees. It never books an exit or aggregates incompatible portfolio exits.
  First-round fast taker round-trip quotes immediately lost 2.74–4.50 cents per
  contract and lost 9.35–16.36 cents after five minutes. One underlying event
  only; missing timely observations were retained rather than carried forward.
- To obtain future 30/60-second measurements, a separate public-GET observer
  was registered at 13:44:49 (30355) before the second round. It collects after
  actual fill timestamps, with a 15-second deadline and 400-request/18:30 UTC
  bound. It changes neither forecasts nor orders. Observer v1 (30307) stopped
  before any observations to narrow its exception handling after lint rejected
  a broad catch; v2 passed lint before launch. No test was weakened.
- Diagnostic v2 (30303) added the observer namespace. V3 (30888) corrects the
  zero-horizon maker timing: an earlier arrival book is invalid as a later
  post-fill mark. V3 requires a fresh post-fill request for makers; old v2 maker
  zero-horizon rows are retained in the archive and superseded. Taker and later
  horizons are unchanged. Tests reject early/late maker marks and independently
  check sale fees plus unavailable depth. Latest full suite: 82 passed in 1.01s.

## E011 — Observation-source provenance audit

Protocol 30968, registered before new observation/model analysis, investigates
whether intraday station reports can constrain daily maxima. NOAA MADIS describes
five-minute processing and retrospective recovery at 1/7/35 days; a final file's
observation time is not sufficient evidence of what a trader could have received.
The July 1, 2025 public directory returned 403. A guessed variable URL returned
404; the correctly linked variable documentation was subsequently retrieved.
No observations or new model errors were acquired. Raw responses and failures
are retained in `reports/E011_source_audit.json`; alternative public sources and
prospective receipt collection remain open. No weather-edge rejection follows
from an unavailable source.

## E011–E012 source follow-up — 2026-09-06 14:26 UTC

E011 acquired three original hourly raw METAR archives (32161–32163). The
owner's collector configuration32642 groups files by WMO bulletin day/hour,
not local receipt. The general IEM hourly METAR endpoint32291 reconstructs
database observations by valid time. Neither filename hour is treated as an
independently verified publication time. No METAR-conditioned model was fitted.

E012 current US source matching, protocol32245, compares September1–5 at all
eight original stations. The rendered Weather Company domestic daily footer
specifies first official NWS CLI values in Fahrenheit, with CF6 backup; its
international METAR rules are different. Current catalog source32204 explicitly
maps the new Houston KXHIGHTHOU alias to the same CLIHOU station. The v1 alias
404s remain archived. Thirty-nine finalized events agree between the exchange,
current TWC table and original NWS report; LAX September5 was still unfinalized
at the 14:01 snapshot. All matched days had only one complete NWS edition, so
this does not validate the first-versus-corrected edition policy or original
trader receipt. `reports/E012_current_source.json` retains every source/exception.

## E013 — Trading-policy autoresearch

User steering: optimize executable trading returns rather than require superior
average probability scores. Protocol33420 registered 576 policies before their
returns: momentum/reversal, buying/fading favorites, buying/fading longshots;
fixed move/price/spread thresholds, four signal times, one/three-hour exits or
settlement. Three explicit cost/entry-delay scenarios give 1,728 comparisons.
One conditional unit per event, future exact bid/ask endpoints, prior entry
limits, both trading fees, locked cash and conservative cluster limits apply.
No depth, midpoint fills, historical maker fills or verified2025fee schedule
is inferred from candles. Missing scheduled exits follow a registered hold-to-
settlement fallback. Positions pending at a reporting cutoff stay at cost;
drawdown is realized-only and cannot qualify for promotion.

The batch uses 11,610 contracts and1,935events, January–June training and July–
September development validation. October–December remains sealed. Every policy
and cost scenario is retained. Monthly selection uses only releases before that
month, requires30trade days, a positive mean log-growth criterion penalized by
two seven-day-block standard errors, and positive stressed training growth.
Shared seven-day resampling controls the maximum statistic across all1,728cases;
it is an approximate development diagnostic, not global promotion evidence.

Results:64frictionless,30costed and48stressed policies have positive validation
P&L. The best costed exploratory policy fades an eight-cent one-hour move at the
24-hour decision, allows an eight-cent spread, and holds to settlement: +$3.33
over31trades/30days, but training lost$1.89. Its adjusted diagnostic p-value is
0.999700; the minimum across the entire batch is0.952905. The independently
timed monthly selector stays in cash except August, when an earlier-selected
favorite policy loses$4.45. No candidate qualifies. More stressed entries can
show higher P&L because their delay changes which orders satisfy the fixed
limit; this is not proof that execution costs help.

Engineering replay35387 freezes input queries at registration receipt and saves
all3,343,680 decisions, including abstentions. It exactly reproduces all1,728
v1summaries and monthly choices. It is not another independent search. The raw
audit independently reconstructs253,227alternative hypothetical entries,
100,170quoted exits and153,057raw settlement outcomes/timestamps; all fee/price
differences are at most1.11e-16. These trades reuse events across policies and
are not independent evidence. No network requests or actual fills in this audit.

Disposition: reject promotion of these broad price-behavior policies. Reversal
is an exploratory lead only; the training/selection failures preclude choosing
it based on its validation result. Continue mechanism-based observation and
maker/relative-value research. `e013_autoresearch.py --run-record-id35387`
resumes the frozen batch; STOP_AUTORESEARCH stops between candidates.

## E014 — Trade observed daily-high constraints

Protocol37584 and model37585 test a different mechanism using preliminary NWS
CLI reports already in the raw archive. There are3,238eligible reports; eleven
ambiguous issue timestamps and one missing maximum remain explicit errors.
Margins0/1/2°F, publication allowances15/60/120minutes, four maximum NO prices
and three execution scenarios give108comparisons. Only lower brackets that
cannot win if the observed bound holds can generate a NO signal; entry selection
uses current/prior sources only, then the exact future quote and prior limit.

Bound uncertainty is fitted before validation from180training days, counting a
day as failed if any station's preliminary maximum exceeds final exchange value
after the margin. Four training days fail at0°F; none fail at1/2°F. A one-sided
95%binomial upper error allowance yields lower probabilities0.949872/0.983495/
0.983495. Independence and conditional-calibration limits remain; these are not
guaranteed probabilities or verified real-time availability.

There are no positive costed results. Twenty-four alternative-case trades reduce
to one Chicago event, August20, across8settings in each scenario. Its preliminary
79°F report conflicted with the final77°F exchange value; the cheap NO lost.
The traded costed cases lose$0.06 each and the other cases abstain. The no-cost
and stressed losses are$0.04/$0.07. Independent source diagnostics cover1,264
training and643validation events: four training and two validation events have
preliminary/final discrepancies, with a maximum2°F. No margins were retuned.

Disposition: no evidence of an executable observation-bound edge at hourly
sampling and the registered conservative gates. The failures motivate explicit
correction risk and faster prospective receipts; they do not rule out all
intraday strategies. Both final holdout and real-money trading remain untouched.

## E015 — Paired passive quotes with inventory risk

Hypothesis: buying both outcomes passively can earn a spread even if an independent
weather forecast does not beat the market. The constraint is adverse selection:
only the losing side may fill, while cash remains locked. This tests a different
mechanism from E009's one-sided forecast-selected orders.

Config `config/e015_market_making.json` freezes three quoting policies (join,
improve one cent, and inventory skew) crossed with three execution cases. The
latter require minimum delays of 1/5/15 seconds, displayed queue multipliers
1/1/2, and excess printed-volume participation 100%/25%/10%. Actual network receipt
adds latency; these are minimum delays, not promises of exact arrival times.

Before the first scheduled decision at15:20UTC, the registration selects a fixed
panel from Miami hourly temperature, Miami/NYC/Chicago/LA daily highs and NYC/Boston
daily lows. For each series it freezes the earliest eligible event and its
highest-volume eligible contract, preserving the entire raw census and all
exclusions. No outcome-based substitution or historical final holdout access.
Two-minute rounds run through18:20UTC, with expiry60seconds after scheduled order
arrival, and a bounded service stop18:30UTC. Missed slots do not invent orders.

Each of nine alternative accounts starts with$100. Quotes are one whole contract
per side. The unchanged E009 ledger reserves cash and fees, limits event cost to
5% of cost-based equity, and caps the whole panel at10% as one weather cluster.
Matched contracts do not create spendable cash before finalized settlement.
The panel priority rotates deterministically between rounds. Both legs pass the
budget together but their post-only arrivals and future fills are independent.

Execution requires fresh post-delay books, non-block opposite-taker prints
strictly through the quote, conservative queue depletion, and original receipt
before expiry. Touches and cancellations never advance the queue. Pagination
windows are processed only after all pages arrive. Unknown direction, stale
arrival evidence, changed predicates/fees and missing tape cancel the affected
orders. Restart cancels open orders and does not reconstruct unseen gap fills.

No model fitting or parameter selection occurs in this experiment. This is an
initial forward feasibility panel, not training, validation, or final proof.
Report every account, actual arrival latency, fill/cancellation counts, paired
and unmatched inventory, tied capital, terminal payout bounds and realized
settlements. The count of accounts/orders is not the number of independent
weather events. A positive paired component alone cannot pass promotion.
A separate preregistered multi-day extension and the existing profitability
criteria are required before any claim of edge or ruin/target-return projections.

Verification before registration:92tests passed; three new tests cover actual
fee thresholds, inventory skew, complementary fill direction, duplicate/touch/
late print rejection, unmatched losses, locked cash, replay, and changed rules.
Initial results are pending. Do not infer success or failure from zero fills
before the first scheduled observation.

Registration42731 at15:08UTC froze four eligible contracts: Miami hourly12EDT
T89.99, Miami daily-high B92.5, Chicago B77.5, and LA B78.5. NYC high and NYC/Boston
low series had no contract satisfying the fixed metadata price/spread gate;
these exclusions remain in the registration and are not replaced after inspection.
Service started15:08:37, awaiting first15:20decisions. Frozen source hashes include
the new runner/core/config and all reused ledger/fee/book/archive dependencies.

### E009 first realized settlement — 2026-09-06 15:11 UTC

Raw source43062 finalizes KXTEMPMIAH-26SEP0610 at84.56°F, received15:11:11.719685UTC.
The original paper audit at15:12 independently reconstructs8,301journal events,
170orders,127taker and34maker fills, and57settled positions using36raw sources.
All32alternative accounts lose on this first underlying event, ranging from
$0.4800 to$4.1605 per initial$100. Other event positions remain open, so available
cash is not total realized return. No cash-based claim of additional losses is made.

All86fill records belonging to the first event lose:39buy-NO fills at82.99,
44buy-NO at83.99 and3buy-YES at84.99. Across alternative accounts, their combined
cost is$77.6517, including$3.5013fees. These sums are a decomposition of alternatives,
not a combined fund or independent samples. Since payout is zero, directional
losses dominate; eliminating fees would not rescue any of these filled positions.

At13:30 the fresher persistence feature was82.04°F and its linear trend extrapolation
83.36°F. By13:55 these were83.84°F and84.45°F, versus final84.56°F. The models became
closer as the event approached, yet their probability/price disagreements still
selected losing tail bets. This one-event diagnostic motivates conditional
morning-heating and horizon uncertainty research; it is not evidence that simply
reversing every model trade would have a repeatable edge. Original paper models
and settings remain unchanged; E015 was already registered before this settlement.

### E015 first forward observations and capital-model correction

At15:27, independent audit reproduces747journal events,180orders,175arrivals,
70queue updates,43partial fill records, and149cancellations using177raw records.
The first round's11fills were all unmatched Miami daily-high NO inventory.
Subsequent fills cover Chicago/LA as well. The optimistic scenario's observed
arrival delays range1.87–10.01seconds, realistic6.62–10.51, pessimistic15.96–23.48;
actual serial collection latency is retained, not replaced by configured minima.
No quote, queue, fee or cash audit failures at this checkpoint.

A primary-source review at15:21 establishes that same-contract YES/NO positions
normally offset with immediate cash return. The explicitly no-netting E015
registration remains intact as an overcollateralized stress case. Its results
cannot alone reject capital-efficient maker strategies. Optional collateral
return across different event contracts is a separate setting/mechanism.

## E016 — Same-contract offsetting and netted maker cohort

Purpose: correct E015's cash timing without changing its already observed orders
or declaring a trading edge. New module `weatherpred/netted_paper.py` wraps the
unchanged paper reducer and atomically applies same-contract netting after each
fill. The net quantity receives final settlement; matched legs return cash early.
Average matched costs determine realized round-trip P&L, including losses.
No double settlement or duplicate-fill cash release is permitted.

Config `config/e016_netted_maker.json`, registration45913 at15:28UTC, clones the
parent42731 four-contract panel and all9policy/scenario alternatives. First new
decisions15:40UTC; two-minute rounds through18:20; bounded stop18:30. New fresh
$100accounts and actual later receipt times mean this cohort is not an identical-
fill causal comparison with E015. All outcomes and failures remain in the journal.

Opening-order cash is still fully reserved. The existing gross cost risk caps
remain5%per event and10%total. These conservative strategy limits may block a
risk-reducing hedge near a cap; they are not venue requirements. No optional
cross-contract netting, margin, credit, or phantom trades are assumed.

A separate read-only identical-fill projection uses the E015 journal through45801:
43fills,4partial offsets completing one contract each in2alternative LA accounts.
Each returns$1cash and realizes$0.02, while overall terminal P&L can still lose
because of unmatched positions. Exact lower/upper terminal payout bounds are
unchanged by the accounting transformation. Additional orders/fills: zero.
This diagnoses cash timing; it does not estimate the benefit of future recycling.

Before registration95tests passed. New tests verify immediate cash return,
remaining inventory/cost, positive and negative offsets, both eventual outcomes,
no double credit, atomic failure and exact replay. An independent source audit
now supports both journal types and reconstructs netted cash, fees, costs and
realized P&L independently. Forward E016 results are pending; no promotion.

## E017 — Exact-station observation receipts and adjacent market books

This is a data-acquisition experiment, not another tested trading policy.
Registration47846 precedes first collection15:36:59UTC. Once per60seconds, through
18:30UTC or180cycles, it records a book batch, original NOAA AWC METAR reports for
KAUS/KDEN/KHOU/KLAX/KMDW/KMIA/KNYC/KPHL, then another book batch. The four-market
panel remains the one frozen by E015; the hourly contract is dropped after close.
A dedicated lock and STOP_STATION_RECEIPTS prevent duplicate runs and stop cleanly.

Preserve observation time, provider receiptTime, reportTime, raw METAR and all
raw decoded fields alongside actual HTTP request/receipt and first-seen version
records. The schema describes three distinct times; reportTime can be later than
receiptTime and is not used as publication. An initial two-hour backfill and
records whose provider receipt predates registration are explicitly labeled.
Chicago uses KMDW (Midway), fixing the station coverage gap left by the older
collector's KORD requests without changing that frozen process.

First frame:17reports, all8stations present,17initial version records,0errors,
book sources47847/47866 and METAR source47848. No future observation/provider
receipt is accepted; missing stations, response-limit suspicion and errors remain
visible. METAR does not substitute for official CLI daily or Synoptic hourly
settlement. Provider receipt is not verified public first availability, and
60second sampling cannot establish subminute reaction or fills. No model, size,
forecast or real-money order is changed by this collector.


## E018 — Conditional hourly mean, heavy tails and netted forward execution

- Mechanism: unconditional residuals pool warming and cooling hours. Test recent
  trend plus one/two Fourier harmonics of known Miami target hour, with ridge
  penalties 1/10/100 and Gaussian or fixed Student t(5) errors: 12 candidates.
- Registration 59488 precedes candidate fitting. Model 59490 published
  2026-09-06 16:09:28 UTC, before 16:30/16:45/16:55 forward decisions. Training
  rows are the existing E006 August-only record 12322, parent model 12323.
- Fit August 20–31 only. Four expanding folds start August 24/26/28/30 and
  evaluate the following two days, excluding decisions before each fit cutoff.
  Equal-day mean continuous negative log density chooses one candidate across
  30/15/5-minute horizons. All candidate scores retained. Refit selected means
  on 12 August days and scale on selected earlier-fold errors; no September
  validation or 2025 final holdout is read. No historical profit inference.
- Selected h2_ridge100_student_t5. August selection diagnostic: mean negative
  log density 1.0241 versus persistence Gaussian 1.1989 and trend Gaussian
  1.3692; RMSE .7748 F versus .8482 and 1.0215 F. Selection benefited from these
  same eight days. This is not independent generalization or promotion evidence.
- Offline replay at 16:14 rebuilt864 training rows from original index bytes,
  reproduced6,768 earlier-fold predictions (564 per candidate), coefficients,
  scales and selection exactly, with0 network requests. Final t scales for
  30/15/5 minutes are .71166/.51239/.40203 F; t scale is not standard deviation.
- Forward runner uses the unchanged original snapshot generated by E009, then
  builds fresh five-minute-lag features from that exact receipt. Twenty new $100
  alternative accounts compare selected model plus four fresh baselines across
  the four E009 execution scenarios. Frozen E009 order/arrival/queue/fee/mark/
  settlement helpers plus atomic same-contract netting; no original run altered.
- Own lock e018.lock, stop STOP_E018, bounded 18:30 UTC. Resume only with run 59488
  and model 59490. Three slots share one hourly event; this cannot establish
  profitability. Failed or late slots are retained, never backfilled.
- Five new tests exercise future-label/feature gates, later-outcome invariance
  of earlier-fold predictions, synthetic later-day generalization, monotone
  threshold probabilities, and the original-receipt adapter's timing gates.
  Pre-registration check: 100 passed in0.92s; archive 59,381 records verified.
- The directional audit now separately supports E018 netted records and
  recomputes quadratic fees/rounding/rebates without the production accumulator,
  plus position costs and realized P&L. Initial E018 audit confirms 20 accounts;
  future fill evidence remains pending at registration.

## E009 — Second forward event finalized, September 6 at 16:11 UTC

KXTEMPMIAH-26SEP0611 finalized at 87.80°F, raw source 60077 first received
16:11:13.731589 UTC, about 71 minutes after its 15:00 close. Forty-one positions
settle across 28 accounts: 8 positive, 20 negative; individual event results range
−$4.3723 to +$2.7589. Four alternatives had no position in this event.
Across the first two events, 31 of 32 alternatives have realized losses; one
(original ten-minute persistence empirical, fastest taker) is +$0.6371. Cumulative
range −$7.3825 to +$0.6371. Third-event positions remain open; one winner selected
after outcomes is not a tradable selection rule. The two hours share one day.

Expanded audit16:12:25 independently reproduces 195 taker and 57 maker fills, 98 settled
positions, 60 raw sources, all cash/fees/net positions and realized P&L. E018 was
already registered and frozen; no parameters were changed after this update.

## E017 — First receipt audit, September 6 at 16:17 UTC

41 complete frames, 34 first-seen JSON versions and 199 raw/source records verified;
zero frame errors. Eight fresh station observations were received after
registration. Their later JSON versions change receiptTime only; all eight raw
METAR strings and temperatures are unchanged. These repetitions are not new
weather information. First provider-to-own receipt differences range 5.445 to
52.806 seconds and do not prove first publication or a market lead. Initial
backfill is excluded from that count. The before/after books bracket our fetch,
not necessarily the original public release. Collector source remains frozen.

## E019 — Conditional daily/weekend rain pairs

- Hypothesis: after a Saturday officially resolves to zero rain, Sunday's rain
  indicator and the weekend rain indicator should agree under unchanged normal
  settlement conventions. Opposite positions across those two contracts can
  have a cost below their conditional $1 combined payout.
- Discovery scanned all 23 precipitation series; 12 had active contracts.
  Daily KXRAIN and weekly KXRAINWKND cover 22 cities. Different cities are not
  mutually exclusive brackets. Chicago rain uses O'Hare, unlike the Midway
  daily-high contract. Exact station, date and source matching are required.
- At 16:30 UTC, raw report 66285 retained every quoted case. NYC's one-pair costs
  were $0.9271 / $0.9473 / $0.9673 for full / half / quarter displayed depth with
  0 / 1 / 2 cents extra slippage per leg. Houston's apparent metadata gap became
  a $1.1915 order-book cost. DC and Seattle's tiny gaps failed added slippage.
  These are exploratory snapshots, not fills or earned profit.
- General terms PDFs RAINHOLIDAY and RAINRANGE (sources 68872/68873) have equal
  extracted text but different file hashes. Material-error review and other
  exchange contingencies prevent treating cross-series equivalence as a guarantee.
- Source consistency report 72006 checks all 40 available finalized city/weekends
  across August 22–23 and August 29–30. All 40 binary outcomes obey the relation.
  Twenty pass strict identical rule-text checks; twenty are excluded because the
  Sunday secondary wording changed. Seventeen of the strict matches had dry
  Saturdays. This uses final labels after the fact, not historical trade-time
  availability; two weekends do not quantify future source failure risk.
- Registration **72680** precedes first 17:00 UTC decisions on September 6.
  Nineteen cities were frozen from all cities with finalized Saturday zero and
  matching official numeric-zero TWC station reports. Atlanta, Miami and New
  Orleans are excluded because Saturday had rain. No selection of NYC alone.
- Three separate $100 accounts compare 1-second/full-depth, 5-second/half-depth
  plus 1 cent and 30-second/quarter-depth plus 2 cents. The weekend leg is delayed
  a further 2 / 2 / 5 seconds. Both intents are published before later books;
  separate market/fee/book receipts support each independent partial IOC fill.
  An IOC order cancels quantity not immediately filled. No rescue hedge, fake
  simultaneous fill or cross-contract cash offset is assumed.
- At each five-minute decision through 18:20, test both equivalent opposite-side
  directions and quantities 1–5. Require at least 2 cents per pair after a fixed
  1-cent source-risk deduction. This deduction is not a fitted failure probability.
  A pair reserves at most 5% of equity; existing 5% per-event / 10% all-weather
  gross limits also apply. At most one attempt per city/account in the entire run.
- A separately replayable ledger records unmatched exposure, fees, reserved cash,
  normal conditional payout bounds and the loss if both sides fail because the
  settlement relation breaks. Cash stays tied up across the different contracts.
  Stop at 18:30; later `--settle-only` reconciles actually finalized held contracts
  without submitting new orders. Initial audit verifies three empty accounts.
- Tests: 105 passed in 1.01s before registration, including five new tests for
  exact source/date gates, unreleased Saturday labels, fee/depth constraints,
  partial first-leg fill with failed second leg, and atomic intent reservations.
  Initial independent audit is deliberately not fill evidence. Forward results
  and sufficient independent events remain pending; profitability unproven.

### E019 first prospective fills — 17:00 UTC

Decision 75359 was published at 17:00:03.398157 before six independent order
arrivals. Only NYC qualified; 54 other city/scenario combinations were rejected.
Eight fill slices completed both legs in all three alternative accounts:

| Scenario | Matched quantity | Cost including fees | Conditional settlement profit | Loss if the source relation breaks |
|---|---:|---:|---:|---:|
| Full depth, shortest delay | 5 | $4.5887 | $0.4113 | −$4.5887 |
| Half depth, +1 cent | 5 | $4.7127 | $0.2873 | −$4.7127 |
| Quarter depth, +2 cents | 4 | $3.8310 | $0.1690 | −$3.8310 |

Actual daily/weekend receipt delays were 2.200/4.398, 6.350/8.305 and
31.321/36.323 seconds. Each leg used a separate book requested after its intended
arrival. The strongest stress improved slightly relative to its decision cost
because the later book changed. The simulator retained its original limit.

Audit at 17:00:41 independently reproduces 24 journal records, all six orders,
six arrivals, eight fill slices, decision costs, actual fees and cash/positions
from 35 raw sources. Snapshot 75819 is published as
[first-fill evidence](evidence/E019_first_fills.json), alongside the
[execution audit](evidence/E019_audit.json). Realized P&L is still zero; cash is
locked pending actual finalized settlement. This is one NYC weekend across
three alternative scenarios, not three independent events or proven profitability.
