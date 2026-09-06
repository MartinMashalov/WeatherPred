# Combining physical guidance and a small transformer

Three fixed combinations of NOAA weather guidance and pretrained Chronos
improve station-temperature forecasting on this development panel. Their
mean absolute errors are **1.6608°F, 1.6023°F and 1.6866°F**, compared with
**1.8345°F** for NOAA guidance alone and **1.9072°F** for pretrained Chronos.
The equal-weight combination has the lowest observed error of the three;
this experiment does not select it for production or establish trading profit.

The work tests a straightforward idea: forecasts from different sources can
make different errors, so combining them can improve accuracy. It also shows
the engineering needed to make that comparison credible: fixed candidates,
chronological calibration, identical targets, preserved source bytes, and
uncertainty calculations that keep related days together. No claim of a newly
invented blending method is needed.

[All twelve candidates and twelve comparisons](../evidence/E029_fixed_combinations.json)
come from E029 report **131549**, execution registration **129863**. The
[physical-guidance comparison](PHYSICAL_FORECAST_COMPARISON.md) explains NBH;
the [original station study](STATION_FORECASTS.md) explains the eight existing
baselines and transformer forecasts.

## What was fixed before the results

Scientific design **124922**, archived September 6 at **19:50:32 UTC**, fixes
three neural weights: 25%, 50% and 75%. It also fixes all twelve comparisons,
calibration dates, metrics and bootstrap settings. This precedes the first
NBH scoring attempt at **19:59:25 UTC** and its report at **19:59:48 UTC**.
The earlier E022 results were already known when the design was written.

The implementation was registered separately at **20:04:58 UTC**, after the
NBH results were known; that knowledge is explicitly acknowledged in its
protocol. The three weights and methods did not change. The complete set of
498 source-file attempts was bound before reading their forecast bodies.
All twelve raw prediction arrays were archived as record **131498** before
observation labels were decoded for scoring. The experiment performed **zero
new model fits, zero neural inferences and zero network requests**; it reused
saved predictions. The adapted Chronos reference had been fitted once in E022.

All **9,870 original eligible cases** have physical forecasts, so there is no
missing-forecast subset in this result. That comprises **3,271 calibration
cases across July 6–19**, a fourteen-day period, and **6,599 development cases
across July 20–August 16**, all twenty-eight UTC days. The original eligible
panel covers twenty stations and one-, three- and six-hour decision horizons.
These counts do not imply that every station or hour outside the original
eligibility rules was observed.

## The point forecast and probability boundaries

Let $p_i$ be NOAA NBH's temperature forecast for case $i$, and let $m_i$
be the saved pretrained Chronos point forecast. For a fixed neural weight
$w$, the hybrid point forecast is

$$
\widehat y_i^{(w)}=(1-w)p_i+w m_i,
\qquad w\in\{0.25,\;0.50,\;0.75\}.
$$

Thus the 25% neural candidate uses 75% NBH and 25% Chronos. The blends use the
**pretrained** model; the separately adapted model is a comparison baseline.
No regression, optimizer or reinforcement learning chooses the weights.

Chronos also provides thirteen raw quantiles. A quantile is a predicted
probability boundary: for example, a calibrated 90th percentile aims to have
90% of observations below it. At each of the original levels
$\tau\in\{.01,.05,.10,.20,.30,.40,.50,.60,.70,.80,.90,.95,.99\}$, the raw
hybrid boundary is

$$
q_{i,\tau}^{(w),\mathrm{raw}}=(1-w)p_i+w q_{i,\tau}^{\mathrm{Chronos}}.
$$

This shifts and scales the neural boundaries toward the physical point.
NBH contributes the same point at every raw quantile level; its spread field
is not used to invent a Gaussian distribution.

Every candidate then receives the original E022 calibration. For each horizon
$h$ and level $\tau$, calculate an additive correction from the earlier
calibration cases $C_h$:

$$
a_{h,\tau}
=\operatorname{EmpiricalQuantile}_{\tau}
\left(\{y_j-q_{j,\tau}^{\mathrm{raw}}:j\in C_h\}\right).
$$

The empirical quantile uses the registered linear interpolation rule. Add these
corrections to later raw boundaries and sort them into increasing order so
probability boundaries cannot cross. July 20–August 16 outcomes do not enter
these corrections. Boundary cases are excluded unless their decision occurs
after the relevant fitting cutoff and their calibration label is available
under the original timing assumption. The point forecast remains unchanged;
calibration affects the probability boundaries and intervals only.

## Results on exactly the same cases

| Forecast | MAE, °F | RMSE, °F | Calibrated quantile loss, °F | 90% coverage | 90% width, °F |
| --- | ---: | ---: | ---: | ---: | ---: |
| Original NBH | 1.8345 | 2.5317 | 0.5695 | 92.42% | 8.4666 |
| Pretrained Chronos | 1.9072 | 2.8108 | 0.5855 | 91.09% | 8.6547 |
| Fixed adapted Chronos | 1.8872 | 2.8089 | 0.5723 | 91.66% | 8.2814 |
| Ridge regression, penalty 100 | 2.1501 | 3.0350 | 0.6558 | 91.38% | 9.7643 |
| 75% NBH + 25% pretrained Chronos | 1.6608 | 2.3067 | 0.5090 | 90.44% | 7.4837 |
| 50% NBH + 50% pretrained Chronos | 1.6023 | 2.2818 | 0.4898 | 90.90% | 7.2533 |
| 25% NBH + 75% pretrained Chronos | 1.6866 | 2.4631 | 0.5165 | 90.99% | 7.6432 |

The full report retains the other five original baselines as well. No original
candidate or unfavorable metric was removed. All eight original full-panel
E022 score summaries reproduce their prior report.

**MAE**, mean absolute error, measures the average size of a point error.
**RMSE**, root mean squared error, gives larger errors more weight. Cases are
first averaged within each UTC target day, and then all twenty-eight days
receive equal weight. With $D=28$, $n_d$ cases on day $d$, and point
error $e_i=\widehat y_i-y_i$, the definitions are

$$
\mathrm{MAE}=\frac1D\sum_d\frac1{n_d}\sum_{i\in d}|e_i|,
\qquad
\mathrm{RMSE}=\sqrt{\frac1D\sum_d\frac1{n_d}\sum_{i\in d}e_i^2}.
$$

Quantile loss, also called pinball loss, measures the accuracy of probability
boundaries. For residual $u=y-q_\tau$, its penalty is
$\rho_\tau(u)=\max(\tau u,(\tau-1)u)$. The report averages this over the
thirteen calibrated boundaries, then uses the same case-within-day and
equal-day weighting. Lower error and loss are better.

The 90% interval runs from the calibrated 5th to 95th percentile. Coverage
measures how often observations fall inside it; width measures how wide it is.
Coverage near 90% and narrower intervals can be useful together. Maximizing
coverage alone can merely reward excessively wide intervals. These aggregate
coverage figures do not establish calibration for a particular trade.

## Every fixed comparison and its uncertainty

Each row below is **hybrid MAE minus reference MAE**. Negative values favor
the hybrid. All twelve reported simultaneous descriptive 95% intervals are
below zero; the 75% neural blend against NBH has the closest upper endpoint
to zero, at approximately −0.0045°F.

| Neural weight | Reference | MAE difference, °F | Simultaneous descriptive 95% interval, °F |
| --- | --- | ---: | --- |
| 25% | NBH | −0.1738 | [−0.2116, −0.1360] |
| 25% | Pretrained Chronos | −0.2465 | [−0.4132, −0.0798] |
| 25% | Adapted Chronos | −0.2264 | [−0.4001, −0.0528] |
| 25% | Ridge 100 | −0.4893 | [−0.6083, −0.3704] |
| 50% | NBH | −0.2323 | [−0.3196, −0.1449] |
| 50% | Pretrained Chronos | −0.3050 | [−0.4218, −0.1881] |
| 50% | Adapted Chronos | −0.2850 | [−0.4097, −0.1602] |
| 50% | Ridge 100 | −0.5478 | [−0.6185, −0.4771] |
| 75% | NBH | −0.1480 | [−0.2914, −0.0045] |
| 75% | Pretrained Chronos | −0.2207 | [−0.2784, −0.1629] |
| 75% | Adapted Chronos | −0.2007 | [−0.2689, −0.1324] |
| 75% | Ridge 100 | −0.4635 | [−0.5051, −0.4220] |

The bootstrap resamples adjacent **seven-day blocks**, wrapping at the end of
the twenty-eight-day calendar, for **10,000 samples with seed 6202901**. All
twelve comparisons use the same sampled days. This preserves their relationship
and some short-term weather dependence. It does not treat 6,599 forecasts as
6,599 independent weather events.

For comparison $k$, let $\bar\Delta_k$ be its mean daily difference,
$s_k$ the standard deviation of its bootstrap means, and
$\bar\Delta^*_{b,k}$ the mean in sample $b$. The shared critical value is
the empirical 95th percentile of

$$
\max_{k=1,\ldots,12}
\frac{|\bar\Delta^*_{b,k}-\bar\Delta_k|}{s_k}.
$$

The reported interval is $\bar\Delta_k\pm c s_k$, using the same critical
value $c$ for all twelve comparisons. This adjusts the uncertainty calculation
for the declared family of twelve. It does not adjust for every earlier
experiment in the project or convert reused development dates into an
untouched test. Twenty-eight days remain a short sample; the seven-day block
choice cannot establish behavior across seasons or future market conditions.

## What the evidence establishes, and what remains untested

The registered report provides evidence that fixed combinations improve
station forecast error on these specific development cases. No additional
subgroup search, fitted combination weight or newly computed statistic is
used in this explanation. The 50/50 result is the lowest observed error among
the three predeclared alternatives, with no claim that it is an optimal or
selected production policy.

Actual source downloads occurred in September. NBH object storage timestamps
precede the hypothetical decisions, but do not prove that a trader received
those precise forecast editions at the time. Station history also uses an
assumed publication lag rather than verified first receipts. The pretrained
checkpoint predates the evaluated dates; its complete pretraining-data history
is not independently established. These limitations carry over from E022 and
E026.

The target is an individual station's hourly temperature. It is not the
contractual five-station Miami hourly index, a daily high, or a Kalshi payout.
No executable market prices, fees, spreads, available depth, fills or cash
settlement enter this experiment. Better temperature error therefore does not
establish profitable trades. Settlement-compatible targets, contemporaneous
source receipts and a separately registered execution study are still needed.

The runner invokes the separate E022 arithmetic auditor for all twelve score
summaries; the retained maximum disagreement is **1.42×10⁻¹⁴**, below its
1×10⁻¹⁰ tolerance. That check is part of the existing registered result. The
additional E029 audit **151341** reconstructs every raw blend exactly, checks
prediction-before-score chronology, and reproduces all twelve model summaries
and twelve bootstrap comparisons, including both random-draw hashes. It shares
the disclosed source/card validator and uses separate blend and bootstrap
implementations. Neither arithmetic check independently reruns a neural model
or establishes historical public availability.
[Full audit evidence](../evidence/E029_result_audit.json).

For a job application, the concrete contribution is a reproducible forecasting
research system: combine physical guidance with a small time-series
transformer, calibrate uncertainty using only earlier observations, compare
all fixed alternatives on identical cases, and preserve the full experiment
and data lineage. The defensible achievement is the measured development
forecast improvement and the controls that make it reviewable.

[Fixed scientific design](../evidence/E029_design.json),
[execution configuration](../config/e029_fixed_combinations.json),
[runner](experiments/e029_fixed_combinations.py),
[full result evidence](../evidence/E029_fixed_combinations.json),
[physical forecast study](PHYSICAL_FORECAST_COMPARISON.md).
