# Can operational guidance improve the transformer's forecast directly?

E031 tests a specific limitation of the successful fixed blends: their neural
forecast never sees the physical forecast before producing its own prediction.
The new candidate supplies the already-issued NBH temperature path as a known
future input. Its comparison includes a matched univariate transformer, the
original physical baseline and all three fixed E029 blends. No model weights
are fitted in this experiment. No trading claim follows from its scores.

## Input construction

For decision time D and target D+h, with h in {1,3,6} hours, the target history
is the original 168-hour grid ending at D−1 hour. The model predicts seven
steps; output steps 2,4,7 correspond to the three target horizons. A missing
latest measurement does not move that grid backward. Every numerical history
lookup is checked against the current decision's permitted past.

The guided candidate adds one named variable. Its past is a duplicate of the
eligible observed-temperature history. Its future is the current NBH run's
issued path from D through D+h; remaining future slots are missing. The run is
D−2 hours, so the required raw NBH leads are 2 through h+2. This variable is
explicitly **observed-then-guided temperature**, not a historical sequence of
NBH forecasts. The duplication gives the input the scale of observed
temperature. A naive input with only one historical NBH point had near-zero
variance and failed the package preflight before any model evaluation.

The matched candidate has the same checkpoint, history, prediction length and
quantile grid, with no guidance variable. Missing guidance must retain the
original case and copy its matched prediction exactly. No future observed
temperature is a model input.

## Fixed evidence and evaluation

Extraction registration **166222** precedes the new temperature-cell reads.
Artifact **166248** retains all 498 original objects and 9,870 cases, including
42,762 required cells. Every case has usable guidance. Each cell keeps its
original raw token, byte location, station, valid hour, source hashes and
actual September receipt. Storage modification time supplies only conditional
historical eligibility; it does not establish a contemporaneous receipt.

The split remains 3,271 calibration cases on July 6–19 and 6,599 development
cases on July 20–August 16. Scores weight each of the 28 development days
equally. The original additive quantile correction is estimated separately
for each candidate from calibration data only.

The primary measure is calibrated pinball loss, which penalizes quantiles
according to how far and on which side their observations fall. Five paired
comparisons subtract each reference's daily loss from the guided candidate's
loss. They share 10,000 seven-day block resamples with seed 6203101 and a
maximum standardized statistic for simultaneous descriptive 95% intervals.
Point error, bias, coverage and interval width remain visible. The comparison
is development research after E022/E026/E029 results were known.

A candidate advances only to further prospective research if guidance covers
at least 90% of the complete original panel and pinball loss improves by at
least 5% against **every** reference, including the matched transformer and
all three fixed blends. This rule is not the project's profitability gate.

## Execution and integrity

The fixed checkpoint runs on CPU in evaluation mode with unchanged parameters
and cross-learning disabled. Batches contain at most 64 series, so guided
cases use batches of at most 32. The finite plan allows 19,740 main case
predictions and 48 additional integrity predictions. Exact repeats must match;
appending later origins or reversing case order may change outputs by at most
0.00001°F. Failed checks keep their evidence and stop evaluation.

An external supervisor records logs, watches process-group memory, and stops
the single attempt at 1,200 seconds or above 4 GiB. Memory sampling is not an
OS sandbox. No network requests, weight downloads, fits or orders are allowed.
Predictions and integrity evidence must enter the append-only archive before
scoring labels are accessed.

Model registration **166868** fixes this implementation before execution.
Report **167394** completes the supervised attempt in 70.56 seconds, with
19,740 main case predictions, 48 integrity predictions, no fits and no network.
Peak sampled memory is 1.52 GB. All four frozen reference scores reproduce.
[Registered protocol](../evidence/E031_model_registration.json),
[completed extraction](../evidence/E031_trajectory_extraction.json).

[Package preflight](CHRONOS_COVARIATE_PREFLIGHT.md),
[fixed blend results](HYBRID_FORECASTS.md),
[model configuration](../config/e031_guided_forecasts.json),
[extraction configuration](../config/e031_extract_trajectories.json).

## Result: input guidance helps, but the fixed blend remains stronger

| Candidate | Mean absolute error, °F | Calibrated pinball loss, °F | 90% coverage | 90% width, °F |
|---|---:|---:|---:|---:|
| Matched univariate transformer | 1.9072 | 0.585459 | 91.09% | 8.6547 |
| Observed-then-guided transformer | 1.6974 | 0.515162 | 90.56% | 7.6690 |
| Original NBH | 1.8345 | 0.569464 | 92.42% | 8.4666 |
| 25% transformer blend | 1.6608 | 0.509024 | 90.44% | 7.4837 |
| 50% transformer blend | 1.6023 | 0.489782 | 90.90% | 7.2533 |
| 75% transformer blend | 1.6866 | 0.516451 | 90.99% | 7.6432 |

Guidance lowers quantile loss by 12.01% relative to the matched transformer
and 9.54% relative to NBH. Its loss is 5.18% higher than the 50/50 blend.
It therefore **fails the fixed advancement rule**; neither the references nor
the required 5% improvement are changed after this result.

| Guided minus reference | Mean pinball difference, °F | Simultaneous descriptive 95% interval |
|---|---:|---:|
| Matched transformer | −0.070297 | [−0.111743, −0.028852] |
| NBH | −0.054302 | [−0.072091, −0.036514] |
| 25% transformer blend | +0.006138 | [−0.004049, +0.016325] |
| 50% transformer blend | +0.025380 | [+0.014059, +0.036701] |
| 75% transformer blend | −0.001290 | [−0.027280, +0.024700] |

All six candidates' scores are also reconstructed by the original independent
E022 scorer inside the run. A separately registered result audit is being
prepared. This is reused development evidence, with no untouched validation,
settlement-probability result or trading return.
[All registered results](../evidence/E031_guided_forecasts.json).
