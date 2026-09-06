# E026 output audit checklist

Prepared before reading E026 forecast errors. This is a review plan, not a passed
result audit. The comparison is registered as **117527**, acquisition v2 as
**113137**, and parent E022 as **105314** (report **105811**). No fits, inference,
network requests, or strategy scores are authorized by this checklist.

1. **Registration and completion.** Verify the registration, source/config/input
   hashes and record chain; bind the eventual `e026_started` census to exactly
   498 distinct acquisition object records. Every object must have an attempted
   outcome before evaluation. Preserve the original E025 failure and v2 lineage.
   A partial acquisition batch must not produce a comparison.
2. **Exact source identity and clocks.** Independently reconstruct NOAA bucket,
   object key, request signature, request-start record, response status, lengths,
   ETag, Last-Modified and range headers. Reused v1 responses must match every
   registered source pin; their actual September receipts must stay unchanged.
   Verify request-before-receipt and storage-before-decision gates. Storage time
   is conditional availability evidence, not proof of historical public receipt.
3. **All target bindings.** Require the exact 9,870 original case IDs, station,
   split, UTC decision and target. Run is decision minus two hours; leads are
   3/5/8 hours for the 1/3/6-hour tasks. Verify UTC rollover from the raw card
   header and hourly UTC column, with no interpolation or alternate run choice.
   Extract TMP independently from retained bytes and check card hashes/offsets.
   Accept only the registered optional single UTC trailing space; retain blank,
   absent TMP and -99 as missing, without substituting zero or another station.
4. **Missingness before labels.** Determine the common case set solely from
   physical forecast availability. Account for every excluded ID/reason and
   report counts by split, station, horizon and UTC day. If any forecast is
   missing, label the full physical panel incomplete. Compare all nine models
   on identical available IDs; never compare a subset score with an old
   full-panel score. Retain the original calibration support thresholds.
5. **Raw forecasts and calibration.** Confirm all eight E022 prediction hashes
   are unchanged and that no model was rerun. NBH raw point is TMP in Fahrenheit;
   its 13 raw quantiles equal that point, without a TSD Gaussian assumption.
   Recompute additive per-horizon residual quantiles from July 6–19 cases only,
   then monotonic rearrangement. Recompute every model's calibration on the
   common set when incomplete; do not tune station offsets, lags or blend weights.
6. **Independent score arithmetic.** Reconstruct each case's raw point error,
   calibrated pinball loss, 80%/90% coverage and width. Average case errors within
   UTC target day, then weight days equally. RMSE is the square root of the
   day-weighted mean squared error. Check all daily counts, offsets and aggregate
   values against the existing independent E022 arithmetic auditor. If the panel
   is complete, all eight unchanged scores must also reproduce report 105811.
7. **Eight simultaneous comparisons.** Rebuild the 28-by-8 paired daily MAE
   matrix as NBH minus each existing model. Reproduce the one shared circular
   seven-day bootstrap: 10,000 draws, seed 6202601, four start indices per draw,
   eight comparisons, centered maximum absolute standardized statistic, sample
   standard deviations and conservative `higher` 95% quantile. Verify the index
   and resampled-mean hashes and every interval. Constant/degenerate comparisons
   receive no interval. If any original development day is missing, bootstrap
   must report unsupported rather than concatenate the remaining days.
8. **Publication evidence.** Verify local JSON equals the archived gzip report
   except its added report ID; retain report hash, source binding, counts, all
   nine metrics, all eight signed differences, intervals, missingness and
   independent maximum arithmetic discrepancy. Include both favorable and
   unfavorable results. Check model fits/inferences/network/trades are all zero.
   These are retrospective development diagnostics designed after E022 results,
   not untouched validation, settlement forecasts, demonstrated profitability,
   or evidence that a transformer is superior to physical weather forecasting.

Expected output: `reports/E026_comparison_117527.json` and its linked
`e026_report_gzip` archive record. Actual result inspection waits for completion.
