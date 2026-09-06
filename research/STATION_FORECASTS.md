# E022 — A small transformer against station forecasting baselines

Chronos-2-small reduces average absolute temperature error from 2.1501°F for
the strongest ridge baseline to 1.8872°F after one fixed supervised fit, a 12.23%
reduction on this development panel. The pretrained checkpoint already reaches
1.9072°F; adaptation adds only 1.05% relative improvement. This is a useful
forecasting lead, with no trading-profit or untouched-validation claim.

Registration **105314** precedes all eight candidate executions and scores.
Report **105811** retains every candidate; independent audit **106588** preserves
the separate score reconstruction. [Full report](../evidence/E022_station_forecasts.json)
and [audit](../evidence/E022_audit.json) include exact hashes and limitations.

## Data and chronology

The source contains 50,771 hourly observations from 31 stations, acquired on
September 6. All 37 directory stations remain in coverage accounting; 20 satisfy
the training and context requirements. Empty May downloads are retained. No
missing observation is interpolated and no station is chosen by forecast error.

| Stage | Fixed period | Eligible support |
|---|---|---|
| Training | Targets from May 11 with assumed label availability before July 6; available observations start in June | 20 station sequences; 6,995 ridge cases |
| Quantile calibration | July 6–19, with decisions after the training cutoff | 3,271 cases |
| Development scoring | July 20–August 16, with decisions after calibration becomes available | 6,599 cases on 28 UTC days |

Each forecast uses 168 hours of past context, at least 120 observed hours, and
a latest input no more than two hours old. Targets are 00/06/12/18 UTC at fixed
one-, three- and six-hour decision horizons. The 15-minute assumed publication
lag moves the final hourly context endpoint back one grid hour; the model must
therefore predict grid steps two, four and seven. The exact timestamps, missing
values and boundary abstentions are preserved in the common case manifest.

Historical publication times and original revisions are **not verified**. Actual
September receipts are retained separately from the 15-minute sensitivity
assumption. These station temperatures are not the five-station Miami hourly
settlement index, daily maxima, or an inferred Kalshi contract label. The model
checkpoint predates the evaluated dates, but its full pretraining-data history
is not independently established. No observations on or after August 17 enter
features, fitting, calibration or scores.

## Models and all results

Persistence repeats the latest eligible observation. The seasonal baseline uses
the same UTC target hour one day earlier, with a declared persistence fallback.
The blend weights those two equally. Ridge regression predicts a correction to
the latest temperature from recent changes, prior-day temperature, local clock,
input age, missing-data flags and station indicators. Its three fixed penalties
are all reported.

The neural candidates use the same pinned 27,934,624-parameter Chronos-2-small
checkpoint: one pretrained model and one supervised adaptation. The latter
completes exactly 200 AdamW steps, batch size 16, learning rate 0.00001, seed
62027, and a seven-hour prediction length. Fit and checkpoint saving take
34.52 seconds within a 300-second limit. No evaluation labels are supplied to
fitting, no checkpoint is selected by score, and no training retry or RL occurs.

| Candidate | Absolute error, °F | Root mean squared error, °F | Quantile loss, °F | 80% coverage | 80% interval width, °F |
|---|---:|---:|---:|---:|---:|
| Persistence | 5.1183 | 6.8903 | 1.4852 | 79.17% | 15.9387 |
| Previous day | 3.1517 | 4.5435 | 1.0056 | 81.91% | 10.2000 |
| Equal blend | 3.2853 | 4.4153 | 0.9826 | 80.09% | 10.5528 |
| Ridge, penalty 1 | 2.1518 | 3.0286 | 0.6507 | 82.13% | 6.8329 |
| Ridge, penalty 10 | 2.1503 | 3.0277 | 0.6510 | 82.02% | 6.8362 |
| Ridge, penalty 100 | 2.1501 | 3.0350 | 0.6558 | 82.08% | 6.8806 |
| Chronos pretrained | 1.9072 | 2.8108 | 0.5855 | 81.69% | 6.2517 |
| Chronos, fixed adaptation | 1.8872 | 2.8089 | 0.5723 | 81.82% | 6.0930 |

Every metric first averages cases within each UTC day, then gives each day
equal weight. Root mean squared error takes the square root after averaging
squared errors. Point scores use raw baseline predictions or neural medians;
distribution scores use the separate earlier calibration period. Quantile loss
penalizes inaccurate probability boundaries; lower is better. Coverage is the
fraction of outcomes inside an interval, while width measures its uncertainty.

The adapted model's quantile loss is 12.05% lower than the best ridge quantile
loss. Its calibrated 90% interval contains 91.66% of development outcomes and
has average width 8.2814°F. These aggregate results do not establish calibration
conditional on an actual trade or a particular station and weather regime.

## Where the gain occurs

A separately declared diagnostic, registration 107159 and report 107195, retains
all 480 model/station/horizon rows. Against ridge with penalty 100, the adapted
model has lower absolute error at 19 of 20 stations, all three horizons, 52 of
60 station/horizon combinations and 25 of 28 days. Its gain is weighted toward
the three- and six-hour horizons. The best five station contributions account
for about 60% of the aggregate improvement.

The adaptation-only improvement is weaker: it beats the pretrained checkpoint
at 10 of 20 stations and 30 of 60 station/horizon combinations. One-hour error
gets worse. A shared seven-day block bootstrap, which keeps adjacent days
together and adjusts across four declared comparisons, gives an exploratory
95% interval of −0.0427°F to +0.0027°F for adapted-minus-pretrained absolute
error. That interval includes no improvement. The corresponding comparison
against ridge with penalty 100 is −0.3403°F to −0.1855°F.

These are post-result development diagnostics, with earlier access to aggregate
and daily scores disclosed. They do not turn the panel into untouched evidence.
The next useful benchmark is therefore the pretrained model against physical
forecasts, with the adapted model retained as an alternative. See the
[complete diagnostic](STATION_FORECAST_DIAGNOSTICS.md) and
[all slices](../evidence/E022_station_diagnostics.json).

## Independent evidence and limitations

The independent auditor imports neither the production scorer nor a model. It
reconstructs calibration and score arithmetic from saved predictions and raw
observations, verifies all case IDs and hashes, and reconciles 310 neural batch
artifacts with the saved checkpoint lineage. All eight candidates agree within
1.42e−14, below the fixed 1e−10 tolerance. Its four arithmetic fixtures also
pass. It checks the original run's retained deterministic-replay evidence; it
does **not** independently rerun neural forward passes.

The 6,599 forecasts share weather conditions and 28 days. Neither forecasts nor
eight candidate models are independent trials. This report does not compare
against a modern physical forecast such as NBM or HRRR on the same targets,
convert station quantiles into settlement-index probabilities, estimate market
profit, or satisfy the project's 90-day untouched/forward validation gates.
The earlier E021 hourly-index experiment remains a separate result: its overall
error was worse than persistence. A different station target does not overturn
that finding.

Next, retain every station/horizon slice in a declared diagnostic, test against
physical forecasts on identical targets with publication evidence, and validate
the exact settlement mapping before joining model probabilities to executable
prices. Further fitting against these development scores would require a new
experiment and new validation data.

## Reproduction

Code: [runner](../research/experiments/e022_run.py),
[preparation](../research/experiments/e022_prepare.py),
[baselines](../weatherpred/station_forecasts.py),
[model adapter](../research/probes/station_transformer.py),
[independent auditor](../research/experiments/e022_audit.py).
The [configuration](../config/e022_station_forecasts.json) was originally a
preparation specification; its frozen `preparation_only` field is retained.
Registration 105314 separately authorizes and pins the completed execution.

The optional model environment, checkpoint, raw archive and prediction files
are local artifacts. GitHub includes code and report evidence, not an implicit
claim that these inputs are bundled. A fresh model run requires a new output
directory and registration; never refit a completed registered attempt.

```sh
uv run python -m research.experiments.e022_audit --self-check
uv run python -m research.experiments.e022_audit --run-record-id 105314
```

The full audit requires the local archive and saved artifacts. Its command
does not load a neural model, train, execute inference, or access the network.
