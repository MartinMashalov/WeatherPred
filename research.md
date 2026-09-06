# Research evidence log

Retrieved 2026-09-06. Sources are primary unless explicitly labeled. This log
tracks hypotheses and results, not a claim of an investable strategy.

| Source | Finding | Hypothesis/experiment | Result and disposition |
|---|---|---|---|
| [Kalshi historical data](https://docs.kalshi.com/getting_started/historical_data) | Live and historical endpoints are partitioned by moving cutoffs; candles are price aggregates | E000: test both tiers and actual coverage | Live cutoff observed 2026-07-08; preserve response; depth history not established |
| [Weather help](https://help.kalshi.com/en/articles/13823837-weather-markets) | Describes NWS daily climate and Weather Company hourly settlement; NWS daily windows use standard time | E000: verify every contract against actual rules | Live daily KXLOWNY metadata instead names Weather Company; general help cannot resolve source for a specific contract |
| [Current fee PDF](https://kalshi.com/docs/kalshi-fee-schedule.pdf) | July 7, 2026 schedule uses quadratic fees and nonstandard multipliers; text and examples need rounding reconciliation | E000/E001: cost audit | Must consult API fee-rounding docs and series/event changes before simulation |
| [Legacy HIGH rules](https://kalshi-public-docs.s3.amazonaws.com/contract_terms/HIGH.pdf) | Old NYC maximum specifies Central Park NWS, revisions only before expiration | E000: version rules rather than use current source historically | Historical template, not proof of current contract conditions |

Research queue: NOAA NBM/HRRR/GEFS archive provenance; IEM raw product issuance
and receipt times; ECMWF real-time open-data archive limits; proper scoring rules;
ensemble output statistics; dependence-aware bootstrap; backtest selection bias;
Kelly with parameter uncertainty; queue-position and adverse-selection models.

| Source | Finding | Hypothesis/experiment | Result and disposition |
|---|---|---|---|
| [Fee rounding](https://docs.kalshi.com/getting_started/fee_rounding) | Direct balances use $0.0001 precision; model fee rounds to $0.000001, with per-order carry/rebates | E001: implement current arithmetic with explicit member precision | Implemented and tested; do not backdate this schedule to older contracts |
| [Batch order books](https://docs.kalshi.com/api-reference/market/get-multiple-market-orderbooks) | Opposite-side bids define asks and identical liquidity | E001: sweep displayed depth instead of midpoints | Public probe succeeded; repeated `tickers` query parameters required. Comma-joined ticker was interpreted as one unknown ticker and returned an empty book; response membership now validated |
| [Kalshi Miami index](https://docs.kalshi.com/api-reference/live-data/get-weather-index) | Canonical, incomplete and historical-backfill points have different meanings; detailed responses include station receipt times | E004: test observation latency | Current data accessible; capture enabled; no edge tested |
| [Index calibrations](https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations) | Separate publication/effective times and versioned offsets | E000/E004: audit backdated rule use | Actual August 18 change is backdated. Implementation checks both timestamps; do not apply a subsequently published configuration to earlier decisions |
| [Current Miami terms](https://assets.kalshi.com/contract_terms/TEMPH.pdf) | Index settlement uses final-output precision, waits 300 seconds, and permits a bounded fallback | E004: implement label selection with source flags | Selector tests exclude backfilled/pending/future points and enforce the inclusive 60-minute boundary; numerical index reconstruction and full label lineage remain pending |
| [NOAA HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/) | As-issued model forecast files are publicly archived | E000/E003: verify station forecast provenance | July 1 index retrieved; publication/GRIB version validation and numeric extraction pending |
| [NOAA NBM archive](https://registry.opendata.aws/noaa-nbm/) | Calibrated blended guidance with hourly products | E003: strong probabilistic baseline | July 1 object listing retrieved; distribution product extraction pending |
| [IEM MOS](https://mesonet.agron.iastate.edu/mos/) | Station guidance archive includes GFS, NAM and NBM; NBM cycle schedule changed May 5, 2026 | E003: low-cost station baselines | GFS/NBS historical rows retrieved; do not infer availability from runtime. Schedule changes must be modeled |
| [IEM MOS variables](https://mesonet.agron.iastate.edu/mos/fe.phtml) | Guidance includes temperature standard deviations and extrema with specific windows | E003: distribution, rather than point-only prediction | NBS `tsd` observed; daily/calendar window mapping still required |
| [IEM text archive](https://mesonet.agron.iastate.edu/nws/text.php) | Issued text products and correction identifiers are archived | E000: retain raw CLI editions rather than a corrected daily summary | Correct July 2 CLINYC discovered after guessed identifier failed; issuance alone is not verified receipt time |
| [ECMWF open data](https://www.ecmwf.int/en/forecasts/datasets/open-data) | Real-time portal retains the latest 12 runs; long history requires separate archival access | E003: assess ENS/AIFS feasibility | Retain research candidate; no historical data downloaded or fee paid |
| [Gneiting et al. 2005 EMOS](https://sites.stat.washington.edu/people/raftery/Research/PDF/gneiting2005.pdf) | Ensemble spread and bias can be calibrated into predictive distributions with proper scoring | E003: distributional baseline | Method queued; not implemented or empirically validated yet |
| [TWC settlement site](https://weather.com/kalshi) | Public frontend requests daily station reports and hourly observations; explicit preliminary/official/missing states | E000: obtain current source and station mapping | Daily API returned 37 station mappings. Sampled issue times blank, so current raw records cannot establish pre-outcome availability historically |

Source conflict retained: actual September daily market rules specify TWC, while
the linked `GLOBALTEMPERATURE.pdf` retrieved this session still names NWS. The
template also specifies full source precision, whereas displayed brackets form
integer partitions with gaps on the continuous number line. These are unresolved
conditions, not reasons to round values to make a basket appear exhaustive.

## Follow-through, 2026-09-06

| Primary source | Finding used | Implemented experiment | Observed result |
|---|---|---|---|
| [Gneiting and Raftery, 2007](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf) | Proper scores evaluate probability distributions without rewarding dishonest concentration; CRPS compares a distribution with an observation | E004 Gaussian and empirical CRPS, Brier and log loss; tests against numerical integration and pairwise empirical formula | All four simple baselines trail the market on paired Brier/log loss at all three horizons; retain scoring tools, no strategy promotion |
| [Forecasting: Principles and Practice, distribution accuracy](https://otexts.com/fpp3/distaccuracy.html) and [time-series cross-validation](https://otexts.com/fpp3/tscv.html) | Compare forecasts chronologically and use distribution scores in addition to point errors | E004 chronological fit, training-residual distributions and interval coverage | Persistence beats linear trend on temperature distribution error here, but neither beats market probabilities |
| [NWS observations and climate FAQ](https://www.weather.gov/lot/weather_observations_faq) | Daily climate windows remain on local standard time across daylight saving; observations can miss intrahour extremes | E002 standard-time window map, separate market-close gate; no METAR-max substitution | Numerical close timestamps and some historical secondary text conflict; retained explicitly. Weather-feature station joins still require independent verification |
| [Kalshi historical market data](https://docs.kalshi.com/getting_started/historical_data) | Metadata and candles must be obtained from the correct historical tier | E002 both-tier census plus source/predicate audit | 101,497 market rows; 11,610 development contracts. All 188 absent structured predicates were winners and were recovered from explicit primary rules rather than dropped |
| [Kalshi batch candles](https://docs.kalshi.com/api-reference/market/batch-get-market-candlesticks) | Candle closes can supply a timestamped predictive benchmark but no queue/depth | E004 118 validation-event requests, matched event-weighted comparisons | Only 1,219/3,510 candidate contract/horizon quotes eligible; this quote-conditioned sample is disclosed |

E004 binding constraints: simple forecasts add no measured information relative
to market probabilities on the observed validation sample; timing history remains
unverified and the sample contains only five independent days at most. New
freshness/nowcasting hypotheses must use new registered forward observations.
The frozen original models are retained in the prospective logger to measure
actual availability and avoid selecting a model after seeing forward results.

## Additional experiments, 2026-09-06 11:28 UTC

| Primary source | Method used | Experiment and status |
|---|---|---|
| [Platt, 1999](https://home.cs.colorado.edu/~mozer/Teaching/syllabi/6622/papers/Platt1999.pdf) | Fit a sigmoid mapping from scores to probabilities | E002 fits log-odds intercept/slope with fixed regularization and day/event weights. Analytic gradient checked numerically; exact known-frequency fixture recovers identity. Actual market results await complete acquisition |
| [Circular block bootstrap, author documentation](https://arch.readthedocs.io/en/latest/bootstrap/generated/arch.bootstrap.CircularBlockBootstrap.html) | Resample fixed-length adjacent blocks with endpoint wrap; preserve dependence within blocks | E002 uses paired calendar-day means, fixed 1/7/14-day block lengths and seed. Tests retain a constant paired difference and show wider intervals for a serially dependent fixture. Stationarity and finite-sample limits remain explicit |
| [Kalshi Miami methodology, Appendix B](https://assets.kalshi.com/contract_terms/TEMPH.pdf) | Offset-adjusted weighted temperature, full-roster reference and receipt deadline | E005 conditionally reconstructs 24,511/24,512 canonical points; one fallback precision discrepancy retained. Pending input estimates matched 53 later values, but only three distinct values and one day |
| [Published calibration timeline](https://docs.kalshi.com/api-reference/live-data/get-weather-index-calibrations) | Effective and published times both govern configuration availability | E005 uses only configurations actually archived by each live receipt; no later calibration is silently applied to a past pending point |

The first-seen provisional lead was about three minutes in this polling channel.
That is not an informational lead over the market. The mechanism can supply a
fresher pre-close input for later settlement minutes; it cannot trade an already
closed market merely because that market's final index publication comes later.
Source QC and fallback precision remain explicit uncertainty sources.

## Updated forecasts and execution evidence, 2026-09-06 13:52 UTC

E010 completed all 546 original 07/13 UTC development cycles. The six updated
weather methods still trail market probabilities on the same 2,015 quotes across
92 days. Three grid baselines improve over their fixed-01 UTC versions, but the
best updated Brier score is 0.207857 versus market 0.145231. Independent raw replay
reconstructed every updated card, time-gated feature and all 28,210 scores with no
refit. Later guidance is a better weather input here, not an established trading
edge. See `EXPERIMENTS.md` and `reports/E010_updated_models.json` for the complete
retained comparison and its development-only interpretation.

| Primary source | Finding used | Experiment and disposition |
|---|---|---|
| [Cont, Kukanov and Stoikov, order-book events](https://arxiv.org/abs/1011.6402) | Their equity-market study relates short-interval price movement to order-flow imbalance and depth; it does not establish the same relation for weather markets | E009 separates spread/fees from later bid movement and collects future observations. No order-flow predictor or causal impact coefficient fitted |
| [Cont and Kukanov, order placement](https://arxiv.org/abs/1210.1625) | Queue size, flow and transaction fees affect the choice between passive and aggressive execution | E009 now has raw-tape evidence for six partial maker fills from two trades across three alternative accounts. Timing, queue consumption, fees and cash independently replayed; no maker profitability claim |
| [NOAA MADIS METAR processing](https://madis.ncep.noaa.gov/madis_metar.shtml) | Files can receive late observations in 1/7/35-day recovery processing; observation time alone is insufficient for historical availability | E011 source audit registered before any nowcast fitting. Original July 2025 directory returned 403; no observations downloaded or historical-availability claim |
| [NOAA METAR variables](https://madis.ncep.noaa.gov/sfc_metar_variable_list.shtml) | Includes a correction flag, raw report and temperatures in tenths of a degree Celsius | E011 seeks original versions and receipt times before imposing an observed-temperature bound on daily maxima. Source and rounding reconciliation remain required |

Execution diagnostic v3 retains the earlier v1/v2 reports and corrects maker
zero-horizon timing: the old arrival book cannot serve as the later post-fill
mark. Future quotes, including sale fees under the entry-verified schedule, are
descriptive individual-order valuations, not exit fills or portfolio returns.

## Forecast-source evidence, 2026-09-06 11:54 UTC

| Primary source | Finding and implemented consequence |
|---|---|
| [NOAA original January 1 NBS object](https://noaa-nbm-grib2-pds.s3.amazonaws.com/blend.20250101/01/text/blend_nbstx.t01z) | Original file with LastModified 02:10:17 for the 01 UTC run. E003 archives conditional byte ranges with exact station/runtime/version checks. Full seed ETag equals its MD5. Storage time is retained separately from initialization and our actual receipt; historical public-access permissions are not independently proven |
| [IEM MOS archive](https://mesonet.agron.iastate.edu/mos/) | Runtime/forecast times and encoded station guidance are available. Exact source comparison: 115 KNYC temperature/uncertainty/time fields agree with the original NOAA object. The bulk endpoint's GFS sample includes a run at the requested end boundary, reinforcing explicit row-time filtering |
| [NOAA NBM v4.2 card specification](https://vlab.noaa.gov/web/mdl/nbm-textcard-v4.2) | NBS TXN has 18-hour windows; TMP is sampled every three hours. Neither is silently renamed the NWS contract's 24-hour daily maximum. A registered target calibration and source reconciliation are still needed |

E006 separately freezes a five-minute-input model on the original training dates
and compares only new registered future snapshots with the pinned ten-minute
baseline. This isolates a timing hypothesis without reusing the five examined
E004 validation days. The original models and their recorded predictions remain
unchanged. No strategy is promoted and no trading-size recommendation is made.

## Daily comparison results, 2026-09-06 12:30 UTC

| Primary source | Finding, experiment and decision |
|---|---|
| [Historical candle schema](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks) | Historical bid/ask `close` is a fixed-dollar string; live `close_dollars` differs. E002's initial empty fit was a schema error. Strict adapter added, failed attempt retained, original timing and quote gates unchanged |
| [IEM original NWS report retrieval](https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py?help) | ZIP entries can share filenames across corrected versions; archive filename may retain an earlier issue time. E003 keeps entry index, hash, archive index time, and later explicitly corrected WMO time. Unmarked timestamp discrepancies are not silently accepted |
| [Gneiting et al., 2005, calibrated probabilistic forecasting](https://sites.stat.washington.edu/people/raftery/Research/PDF/gneiting2005.pdf) | Implemented a fixed linear location and positive spread calibration using Gaussian likelihood, with analytic gradient verification. E003's regression reaches 95.78% coverage for a nominal 95% interval, but loses to market probabilities and the native proxy's point-error score. Retained as a benchmark, not promoted |
| [Circular block bootstrap documentation](https://arch.readthedocs.io/en/latest/bootstrap/generated/arch.bootstrap.CircularBlockBootstrap.html) | Calendar gaps must not be silently compressed. Original E002 estimator rejected them. Report recovery uses the pinned model and original data hash, leaving late-horizon confidence unavailable and withholding a reduced-family Holm correction |

Six NBM-based daily models all trail market probabilities on the 92-day development
validation quarter. Market-only calibration gains are tiny and their seven-day-block
intervals include zero. This rejects promotion of these standalone baselines; it
does not reject conditional information, updated forecasts, observations, other
weather products, or execution mechanisms that have not yet been tested.

The source audit also exposes real timing and label issues: May 27's 01 UTC NBM
object was stored at 18:59:54 and is excluded at day start. Two one-degree numeric
settlement/source discrepancies remain; neither changes the listed binary payouts.
No historical trading returns, capacity or bankroll probabilities are inferred.

## Probability combinations and paper execution, 2026-09-06 13:13 UTC

| Primary source | Finding used | Experiment and decision |
|---|---|---|
| [Gneiting and Ranjan, Combining Predictive Distributions](https://arxiv.org/abs/1106.1638) | Combining distributions changes calibration and dispersion; a valid probability partition alone does not prove calibrated forecasts | E007 tests six fixed-regularization logarithmic pools against normalized and calibrated market benchmarks. Chronological monthly training forecasts avoid fitting both stages to the same outcomes. Native proxy gives a small descriptive gain; four calibrated variants receive zero weight. No confidence or promotion claim |
| [Current fee schedule](https://kalshi.com/docs/kalshi-fee-schedule.pdf) | Fees depend on price and schedule; current conditions cannot establish past fee history | E008 explicitly treats2026fees as assumptions. Native pool conditional P&L turns from+$1.43 gross to-$2.0155 withfees/1cent slippage. No fills verified; all81adjusted diagnostics fail to establish improvement |
| [Public trades](https://docs.kalshi.com/api-reference/market/get-trades) | Public tape reports fixed-point size, timestamps, identifiers and block-trade flags | E009 maker simulation uses non-block, after-arrival trades strictly through its bid, after displayed queue ahead, with25%volume participation. No price-touch fills or duplicate tape use |
| [Order direction](https://docs.kalshi.com/getting_started/order_direction) | Taker outcome yes corresponds to bid direction; no corresponds to ask direction. Direction is distinct from the complementary YES/NO price fields | Raw probes21332/21333 contain matching canonical and legacy direction aliases and complementary prices. Maker fills require the opposite taker direction and an exact unambiguous schema; guessed earlier ticker21293 returned empty and is not liquidity evidence |
| [Fee rounding](https://docs.kalshi.com/getting_started/fee_rounding) | Fee accumulation continues across partial fills and balances respect their precision grid | E009 ledger applies the existing exact fee accumulator across depth slices, reserves cash and releases only unfilled remainder. Tests verify independent cash/fee arithmetic, latency, queue, post-only rejection and full event replay |

E009 is an execution experiment using eight already-frozen hourly forecasts.
Its paper orders are allowed to investigate unvalidated signals; their existence
is not a recommendation to risk real funds. The daily NWS models cannot be applied
to current TWC daily markets without a separate source/window reconciliation.

E010 follows the fixed-forecast failure with original later-cycle data rather than
unregistered parameter searches. January1's07UTC NBS card begins at12UTC; earlier
source-day forecast points must retain their earlier versions. New12hour features
therefore use a publication-gated mosaic of01/07/13UTC forecasts, with all per-point
source IDs retained. No past forecast is relabeled as an observation. Six unchanged
calibration methods will be compared after complete acquisition; no updated errors
have yet been inspected. The paper model cohort stays frozen during this work.

## Trading research follow-up — 2026-09-06 14:26 UTC

The preceding E010 registration is superseded by its result: all six updated
weather baselines still lose to market scores. E013/E014 consequently test
net trading returns directly; overall probability accuracy is not a prerequisite.

| Primary source | Hypothesis and implemented consequence | Result |
|---|---|---|
| [Snowberg and Wolfers, favorite–longshot bias](https://www.nber.org/papers/w15923) | Behavioral price distortions can motivate selective favorite/longshot trades; evidence from other markets is not evidence of a Kalshi weather edge. E013tests both directions alongside momentum/reversal and timed exits | No policy survives the development search adjustment; earlier-month selection loses$4.45 |
| [Bailey et al., backtest overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659) | More configurations increase selection risk. Register finite batches, retain every failure, select chronologically, keep the final holdout sealed and resample shared calendar blocks | E013retains1,728cases and3.34million decisions; best-looking costed gain+$3.33 is not promoted |
| [IEM raw METAR archive](https://mesonet-longterm.agron.iastate.edu/archive/raw/sao/) and [owner collector configuration](https://github.com/akrherz/ldmconfig/blob/main/pqact.d/pqact_data.conf) | Files contain original bulletin streams, but the name is derived from bulletin day/hour. A filename is not original receipt evidence | Three raw files acquired; no unsupported receipt-time nowcast fitted |
| [Unidata filename substitution](https://docs.unidata.ucar.edu/ldm/current/basics/pqact.conf.html) | Captured bulletin timestamp substitution differs from product creation-time substitution | E011records the exact owner rule; retrospective publication remains conditional |
| [Weather Company domestic daily source](https://weather.com/kalshi) | Current domestic daily values redistribute first official NWS CLI/CF6 Fahrenheit figures; international methods differ | E012matches39finalizedevents across8stations/5days; one pending. No proof of historical first availability or revision equivalence |
| [NWS observation and climate-product FAQ](https://www.weather.gov/lot/weather_observations_faq) | Observation encodings, rounding and final climate products differ. Apparent physical constraints require source/correction error treatment | E014uses180training days and0/1/2°F margins, retaining source discrepancies.108casesyieldno positive costed result; the sole traded validation event has a2°F preliminary/final discrepancy |

Next mechanisms: prospective observation reaction at minute resolution, maker
spread capture with queue and adverse-selection evidence, and transient bracket
consistency. Historical hourly candles cannot establish these faster executions.
No result justifies a real-money trade or a$100-to-$10,000 return projection.

## Paired maker research — 2026-09-06

| Primary source | Finding and implemented experiment | Disposition |
|---|---|---|
| [Avellaneda and Stoikov (2008)](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf), archived record42627 | Inventory changes a dealer's preferred bid and ask. The stock diffusion/Poisson assumptions do not automatically describe binary weather markets. E015 implements a transparent one-cent-per-net-contract inventory skew, capped at three cents, alongside join/improve baselines; it does not claim to fit the paper's optimal-control model | Nine forward alternatives preregistered before decisions; results pending, not promoted |
| [Kalshi trade direction](https://docs.kalshi.com/getting_started/order_direction), record42624, and [public trades](https://docs.kalshi.com/api-reference/market/get-trades), record42626 | Canonical taker direction identifies which passive leg could have filled; block prints are excluded. E015 requires opposite direction, strict trade-through, depleted displayed queue, expiry-safe receipt, full pagination and deduplicated trade IDs | Implemented through the frozen paper execution primitives; opposite legs cannot both use one print |
| [Kalshi fee accumulation](https://docs.kalshi.com/getting_started/fee_rounding), record42625 | Per-order rounding persists through partial fills. A maker fee depends on the actual series/event schedule | E015 reads current schedules and overrides, rejects known changes during the resting window, and retains matched capital until finalized settlement |

E015's core economic test is portfolio return after unmatched inventory, not the
spread earned on completed pairs. A deterministic fixture earns $0.0914 on its
paired component but loses $0.7170 overall when the two additional YES contracts
lose. This is a test example, not empirical trading performance. The prior E009
maker quote diagnostics remain negative at 0/30/60/300 seconds wherever observed
at the 14:58 checkpoint; occasional positive taker quote differences are not
booked exits or statistically independent strategy returns.

## Same-contract netting correction — 2026-09-06 15:29 UTC

Kalshi's [own netting explanation](https://news.kalshi.com/p/collateral-return)
(raw45064) says YES and NO holdings in the same market automatically offset.
The current [V2 order interface](https://docs.kalshi.com/api-reference/orders/create-order-v2)
(raw45067) represents buying NO as selling YES at the complementary price; the
[current settlement documentation](https://docs.kalshi.com/getting_started/market_settlement)
(raw45065) settles only net positions. This differs from the optional event-level
[collateral-return setting](https://help.kalshi.com/en/articles/13823816-collateral-return)
(raw45066), whose eligibility locks at the first event order. No account setting
was changed or queried and no live order was sent.

E015's no-netting ledger is therefore an overcollateralized capital stress case,
not exact same-contract venue cash timing. E016 corrects this in a separate
registered cohort using the identical four tickers, nine quote/scenario rules
and conservative opening-order risk caps. Each completed opposite fill credits
one dollar per matched quantity, removes matched costs/quantities, and realizes
the net round-trip profit or loss atomically with the fill. No optional
cross-contract collateral return is assumed. Gross pre-trade caps can still
restrict risk-reducing hedges; this is an explicit strategy constraint.

An identical-fill projection through source journal45801 replays43E015 fill
records. Four partial offset records complete one contract in each of two
alternative LA accounts, returning$1cash and realizing$0.02 each. Their terminal
P&L bounds remain negative-to-positive because unmatched positions remain; no
extra trades are invented. This small paired gain is not portfolio profitability.
New E016 registration45913 precedes its first15:40UTC forward decision. Fresh
observed executions are required to measure any capital-reuse benefit.

## Original aviation observation receipts — E017

[NOAA Aviation Weather Center API](https://aviationweather.gov/data/api/), raw47868,
and its [OpenAPI schema](https://aviationweather.gov/data/schema/openapi.yaml),
raw47869, expose METAR observation time, provider receipt time, report time,
decoded meteorology and the original report. Limited eight-station requests once
per minute stay well below the documented100requests/minute ceiling. The existing
client sends a descriptive User-Agent. An initial four-station probe47225 returned
original Midway, Miami, Central Park and LAX reports with all three timestamps.

E017 registration47846 now preserves exact eight-station versions with books
immediately before/after each batch. First frame covers all8stations with17reports
and0errors. This supports later conditional warming/cooling and observation-
reaction tests, while retaining source precision, correction and actual receipt
limitations. No historical API timestamp is relabeled as our receipt, and no
trading signal or profitability result has been inferred from acquisition.


## Conditional hourly regression and receipt evidence — E018/E017, 2026-09-06

- [Gneiting et al. (2005)](https://sites.stat.washington.edu/MURI/PDF/gneiting2005.pdf),
  original PDF archived 59386: conditional predictive means and uncertainty
  calibration motivate a small distributional model. E018 implements ridge
  trend/daily-cycle means with Gaussian/t errors, not the paper's ensemble
  variance regression or minimum-CRPS estimator.
- [ECMWF near-surface bias investigation](https://www.ecmwf.int/en/newsletter/157/meteorology/addressing-biases-near-surface-forecasts),
  archived 59387: daily-cycle-dependent temperature errors motivate conditioning
  on known target hour. This is motivation, not evidence about Miami profitability.
- [Time-series EMOS study](https://arxiv.org/abs/2402.00555), archived 59388:
  temporal dependence is a reason to inspect recent forecast evolution. E018
  uses recent observed trend as a feature; it does not claim to reproduce that
  study's full time-series estimator or its results.
- E018 tests 12 fixed candidates on August-only expanding folds. Two harmonics,
  strong shrinkage and Student t(5) rank first. Selected development RMSE = 0.7748°F
  versus 0.8482°F persistence and 1.0215°F trend; selection days are not independent
  validation. Model 59490 frozen at 16:09 before new 16:30 paper orders, with 20 alternative
  netted accounts and identical raw snapshot inputs. Outcome/promotion pending.
- E017 audit shows eight first new observations but repeated receiptTime-only
  JSON versions. Treating each repeat as new weather news would inflate sample
  size and distort latency estimates. Original strings/temperatures and first
  own receipt remain the appropriate keys for subsequent reaction research.
- The enhanced E009/E018 raw execution audit recomputes fees without sharing the
  production accumulator and checks net costs/realized profit. Second E009
  settlement leaves 31/32 alternatives negative overall, with further open risk.
  No positive edge or bankroll-target probability is validated.

## Rain-calendar relative value — E019, September 6

The official [daily-rain terms](https://assets.kalshi.com/contract_terms/RAINHOLIDAY.pdf)
and [weekend-rain terms](https://assets.kalshi.com/contract_terms/RAINRANGE.pdf),
archived as 68872 and 68873, have identical extracted general text. They describe
reported precipitation, treatment of trace/missing values and settlement
contingencies. The current daily series additionally discloses material-error
review. Exact contract predicates name the same CLI station and date range;
different trading close times do not alone imply different weather periods.
These sources motivate a conditional identity, not a risk-free guarantee.

If Saturday's rain indicator is A and Sunday's is B, the weekend indicator is
A OR B under matching normal settlement. Known A=0 makes weekend and Sunday
equivalent. NYC's displayed pair cost survives the three explicit fee/depth/
slippage screens at the 16:30 snapshot; Houston's initial headline gap disappears
in the actual book. No historical midpoint is treated as an execution.

The complete recent settled census yields 40 matching binary outcomes over two
weekends; exact secondary-text checks exclude the 20 older cases from strict
comparability. Current source confirmation uses the official TWC table's CLI
station identifier, explicit numeric zero and finalized Saturday exchange result.
The one-cent source allowance is an arbitrary stress deduction, not a statistical
estimate. E019 freezes all 19 eligible cities before 17:00, then tests independent
two-leg arrivals, unequal fills, fees, capital lock and eventual settlement.
Retained as a new forward hypothesis; no profitable portfolio has been established.

## Execution and broader state constraints — E020 / September 6

- [Current Kalshi V2 orders](https://docs.kalshi.com/api-reference/orders/create-order-v2),
  raw 78716, motivate an unsent order-intent builder with YES-book side mapping,
  fixed limits, IOC/FOK/GTC and client IDs. A single-order FOK is not an atomic
  cross-market pair. [Order groups](https://docs.kalshi.com/getting_started/order_groups),
  raw 78719, provide group controls rather than a guaranteed joint fill.
- [Collateral return](https://help.kalshi.com/en/articles/13823816-collateral-return),
  raw 78756, describes eligible within-event collateral treatment. It does not
  establish early cash return across the separate daily/weekend rain events.
  E020 tests 300 quantity/cost rows, with six-pair capacity under strongest stress,
  and rejects the idea that current bid-side exits cheaply recycle the cash.
- [TWC weekly hourly observations](https://weather.com/kalshi/api/metar?primary=true&weekStart=2026-08-31),
  raw 78077, adds 5,443 hourly values across 37 stations. Exact contract rules reveal
  consecutive-day heat streaks. The state enumeration is informative but all
  current weekly opportunities fail 1¢ slippage. Four already-crossed monthly rain
  thresholds have no YES asks. These failures support measuring execution capacity
  before adding a more elaborate forecast.

## Small-model adaptation and bounded autoresearch — E021

- The official [Chronos-2-small card](https://huggingface.co/autogluon/chronos-2-small)
  documents a 28-million-parameter Apache-2.0 model. [Chronos implementation](https://github.com/amazon-science/chronos-forecasting)
  supports supervised full adaptation and quantile forecasts. The local measured
  model has 27,934,624 parameters. Two pretrained variants lose to persistence
  overall; one 100-step fit finishes in 22.55 seconds. Overall RMSE again loses,
  while an exploratory five-minute development subgroup improves about 16%.
  A saved-checkpoint replay catches dropout left active; the same weights are
  rescored in evaluation mode with exact replay and no retraining. Retain the
  short-horizon hypothesis for new dates, not model promotion.
- Google's [TimesFM 3.0 model card](https://huggingface.co/google/timesfm-3.0-pytorch)
  lists a distinct non-commercial weights license. The study retains Chronos
  as the practical local candidate and TimesFM 2.5 as a possible later comparator.
  Neither unrelated benchmark leadership nor model size proves weather skill.
- [PostTime](https://arxiv.org/abs/2605.29401) already studies supervised and RL
  forecast revision. [Verifiable Rewards for Calibrated Probabilistic Forecasting](https://arxiv.org/abs/2607.00164)
  examines noisy outcome rewards and alternative calibration rewards in football.
  These are motivation and precedents, not demonstrations of a weather-market
  edge. The current implementation trains supervised quantiles; execution RL
  remains a hypothesis requiring reliable fill rewards and new forward days.
- [Karpathy autoresearch](https://github.com/karpathy/autoresearch/tree/228791fb499afffb54b46200aca536f79142f117)
  motivates fixed short trials and a fixed evaluator. The adaptation adds
  registered candidates, input/source hashes, chronology, counted failures,
  shared process deadlines and preserved logs. Actual process verification uses
  synthetic inputs. Optimizing one validation period repeatedly remains
  development, so the runner never returns profitability promotion.

Detailed findings, exact versions, sources and retained results:
[model report](research/model_candidates.md),
[market expansion](research/MARKET_EXPANSION.md),
[execution/capital](research/EXECUTION_FINANCE.md),
[autoresearch design](research/AUTORESEARCH_ADAPTATION.md).

### $200 replay: execution accounting changes the question

The E023 account study evaluates 576 policies × four risk fractions × two cost
scenarios. It uses earlier released returns for selection, reserves cash for
unfilled orders and recalculates aggregate integer-order fees. A simultaneous
seven-day bootstrap leaves all 2,304 costed lower bounds nonpositive; the frozen
selector holds cash. The largest earlier training cash balance, $341.87, does not
supply an annual return. The independent account audit reproduces every one of
4,608 ledgers. [Full study](research/BANKROLL_REPLAY.md),
[all results](evidence/E023_training_results.csv).

The historical-data audit reveals the binding coverage constraint: the existing
seven-city daily panel supplies 25 of 365 requested dates and no historical
depth. Official Kalshi documentation separates older single-market candles from
recent batch candles. A safe January 2026 canary finds an empty event-level
response despite 29 existing individual-contract candles. New protocol 99015
therefore downloads the complete 1,736-event 2026 universe through the correct
tier, retaining errors and avoiding the sealed 2025 quarter. This will improve
conditional quote coverage; it cannot reconstruct unavailable queue/depth data.
[Acquisition and sources](research/BANKROLL_DATA_AUDIT.md).

The new station archive adds 50,771 hourly observations, with missing May weeks
and actual September receipts retained. Observation-reaction diagnostics find
no one-minute gross spread-crossing advantage in eight station-matched reports.
Later price movement needs a direction selected before the move and a separate
fill test. [History](research/STATION_HISTORY.md),
[reaction study](research/OBSERVATION_REACTION.md).

E023's apparent training winner is concentrated in Miami and May/June, with
asymmetric rare losses. Its profit changes nonlinearly with integer sizing,
shared capital caps and entry timing. The fixed16-account
[diagnostic](research/BANKROLL_DIAGNOSTICS.md) retains all cells and cannot
justify selecting the profitable city after observing it.

A separate [clock audit](research/HOURLY_ALIGNMENT_AUDIT.md) verifies the precise
Synoptic/Kalshi index minute against formal TEMPH terms, actual canonical
receipts and final payouts. The one-hour delay is settlement processing, not
an hour-later weather target. All3,456 original residuals reproduce. Live input
age is one minute larger than its common training assumption; test that in a
new prospective cohort rather than relabeling the losing one.
