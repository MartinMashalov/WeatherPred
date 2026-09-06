# Where the E022 forecast improvement comes from

**The transformer’s advantage over ridge is broad in this development panel. The additional benefit from our one supervised fine-tuning run is small and uneven.** These are different findings: the pretrained model supplies most of the improvement.

This separate diagnostic was declared as archive **107159** after E022’s aggregate results were known, but before calculating these station/horizon summaries. The earlier score audit had already accessed raw forecasts and daily scores. This is post-result development diagnosis, not untouched validation. [Frozen diagnostic configuration](../config/e022_station_diagnostics.json) records that prior access and every comparison.

The panel remains **6,599 cases, 20 eligible stations, three horizons and 28 UTC days**. All eight models are retained. MAE is mean absolute error in °F: first average cases within each target day, then give each day equal weight. [Complete evidence](../evidence/E022_station_diagnostics.json) retains all **480 model–station–horizon rows**, 160 model–station rows, 24 model–horizon rows, 240 paired station–horizon comparisons and 112 paired daily comparisons, including every unfavorable result and its case/day counts.

| Fixed fit compared with | MAE difference °F, lower favors fixed fit | Relative reduction | Simultaneous descriptive interval °F | Stations better / worse | Station–horizon slices better / worse | Days better / worse |
| --- | ---: | ---: | --- | --- | --- | --- |
| Ridge 1 | −0.26458 | 12.30% | [−0.33899, −0.19016] | 20 / 0 | 54 / 6 | 26 / 2 |
| Ridge 10 | −0.26313 | 12.24% | [−0.33782, −0.18845] | 20 / 0 | 53 / 7 | 26 / 2 |
| Ridge 100 | −0.26289 | 12.23% | [−0.34029, −0.18548] | 19 / 1 | 52 / 8 | 25 / 3 |
| Pretrained Chronos | −0.02002 | 1.05% | [−0.04270, +0.00266] | 10 / 10 | 30 / 30 | 17 / 11 |

There are no exact ties. Ridge 100 had the lowest aggregate MAE among the three already reported ridge candidates; it was not chosen separately for each subgroup.

## Horizons and concentration

Every row below uses the same **2,200 one-hour, 2,200 three-hour and 2,199 six-hour cases**, spanning all 28 days. Values are day-weighted MAE in °F, using raw point forecasts before distribution calibration.

| Model | One hour | Three hours | Six hours |
| --- | ---: | ---: | ---: |
| Persistence | 2.57781 | 4.87057 | 7.90778 |
| Previous-day same hour | 3.14454 | 3.15427 | 3.15626 |
| Equal persistence/seasonal blend | 2.19444 | 3.13955 | 4.52257 |
| Ridge 1 | 1.46799 | 2.20160 | 2.78617 |
| Ridge 10 | 1.46785 | 2.20035 | 2.78324 |
| Ridge 100 | 1.47132 | 2.20075 | 2.77862 |
| Pretrained Chronos | 1.38726 | 1.92719 | 2.40770 |
| Fixed-fit Chronos | 1.40378 | 1.89467 | 2.36361 |

Against ridge 100, the fitted model improves mean errors by **0.06754, 0.30608 and 0.41501°F** at one, three and six hours. Those groups contribute **8.56%, 38.81% and 52.63%** of the total MAE reduction. Station contributions are positive at 19 stations; KLAX supplies 13.75% of the net reduction, and KLAX/KAUS/KOKC/KDFW/KSAT together supply 59.97%. The gain is therefore spread across stations but larger in some places and at longer horizons.

KBOS is the one worse station mean versus ridge 100, by **0.00192°F**. The eight worse station–horizon slices are KATL/6h, KBOS/6h, KDCA/1h, KMSP/1h, KNYC/1h, KORD/1h, KPHL/1h and KPHL/3h. The largest of these losses is **0.19816°F at KORD/1h**. The three worse aggregate days are July 22–24. None were excluded.

Against pretrained Chronos, fine-tuning **worsens the one-hour mean by 0.01652°F**, while improving three/six hours by 0.03252/0.04409°F. Half the stations and half the 60 station–horizon slices worsen. KPHX alone contributes **76.64% of the small net aggregate gain**; gains elsewhere are partly offset by losses. The comparison interval includes zero. This study does not establish a reliable general benefit from this additional fine-tuning.

Contributions preserve the aggregate’s weighting: for each day, sum the reference-minus-fitted absolute errors for a station or horizon and divide by the entire panel’s case count that day; then average across the 28 days. Consequently all station contributions, including negative ones, sum to the reported total gain. Their percentages are shares of the net gain, not station win rates.

## Uncertainty and reproducibility

The frozen procedure uses **10,000 shared circular block draws, seven days per block, seed 6202202**. Each draw samples four block starts, wrapping at day 28; exactly the same sampled days feed all four comparisons. For each comparison, the standard deviation of resampled means supplies its standard error. The per-draw statistic is the largest absolute standardized deviation across the four comparisons. Its 95th percentile, using the higher order statistic, is **2.1347152880518667**; each interval is its observed mean difference plus or minus that multiplier times its standard error.

These are descriptive development intervals. Four weeks provide limited evidence; the shared blocks preserve some weather persistence but cannot establish that future regimes resemble this panel. The simultaneous adjustment covers these four comparisons only, not the earlier research search. No subgroup confidence intervals, p-values, significance labels, model promotion or profitability claim are produced. This target is TWC station temperature, not a Miami index contract or an executable trading return.

Archive **107194** retains every sampled-day index, all 40,000 resampled comparison means and all maximum statistics. Report **107195** retains the complete diagnostic. Exact replay from the pinned source and data returned:

```text
report_record_id: 107195
exact_report_replay: true
exact_bootstrap_replay: true
model_fits: 0
model_inferences: 0
```

Run `uv run python -m research.experiments.e022_diagnostics --verify-report-id 107195` to reproduce the report without appending another result. Three shared-block arithmetic fixtures passed, as did lint and formatting. No original E022 source, configuration, checkpoint or prediction changed.
