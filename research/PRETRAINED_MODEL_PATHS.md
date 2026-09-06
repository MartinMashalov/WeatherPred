# Two bounded pretrained-model paths after the physical baseline

Research recommendation, **6 September 2026**. Prefer an information change
inside the already installed Chronos-2-small, followed by a much smaller
mixing-network comparator. No weights were downloaded, models imported or
fitted, forecasts generated, or new weather/trading scores inspected in this
round. These are **two proposed candidates**, not registered experiments or
promoted trading models.

The existing [model study](model_candidates.md) establishes local feasibility
for Chronos, not profitable settlement forecasts. The
[live-feature bridge](LIVE_FEATURE_BRIDGE.md) identifies missingness and
publication timing as unresolved inputs. E029's three fixed output blends are
already declared under **124922**; this proposal neither adds weights to that
search nor changes its calibration or comparison family.
[Frozen E029 design](../config/e029_fixed_combinations_design.json).

## What the current families actually provide

| Family checked | Mechanism and practical constraint | Decision for this round |
| --- | --- | --- |
| Chronos-2-small | Existing pinned 27,934,624-parameter model. Its group attention can condition on related series and known future covariates. Same installed model can test new information without another weight download. | **Candidate C1:** one fixed publication-gated NBH covariate path. |
| IBM TTM R2.1 | A compact mixing network, not a transformer. The selected 90→30 checkpoint's metadata reports **805,280 parameters**; architecture/configuration are described below. | **Candidate C2:** fixed short-context point-forecast comparator, testing whether a smaller architecture supplies comparable skill per CPU second. |
| TimesFM 2.5 | 200M base parameters, with an optional 30M continuous-quantile head. Its covariate route uses external XReg regression; that differs from native multivariate attention. | **Deferred:** more memory and another runtime, without a first-round information advantage over C1 plus the existing ridge baseline. |
| TimesFM 3.0 | Native multivariate and past/future covariate support. Official repository distinguishes its non-commercial/non-production weights from Apache-2.0 code and older weights. | **Not a trading-deployment nominee** under the published weight terms. No installation. |
| Moirai 2.0-R-small | 11.4M-parameter decoder, quantile loss, multi-token prediction and missing-value-aware patch representation. The official card specifies CC-BY-NC-4.0 and research use. | **Not a trading-deployment nominee** under the published weight terms. Useful prior art for explicit missingness representation. |

Sources: [Chronos-2 paper](https://arxiv.org/abs/2510.15821),
[TTM model card](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2),
[TimesFM repository](https://github.com/google-research/timesfm),
[Moirai model card](https://huggingface.co/Salesforce/moirai-2.0-R-small).
This records source statements and our scope decision; benchmark rankings on
unrelated data do not establish local weather or trading skill.

Moirai's paper itself reports that increasing parameter count eventually
stopped helping in its experiments. TTM studies compact pretrained mixing and
adaptation; Chronos studies multivariate/covariate forecasting. Consequently,
small weather models, missingness-aware models and physical/statistical
combinations all have substantial prior art. No unprecedented-model claim is
appropriate. [Moirai 2.0](https://arxiv.org/abs/2511.11698),
[TTM paper](https://arxiv.org/abs/2401.03955).

## C1: condition Chronos on the available NBH path

**Question:** can the same pretrained model use the *shape* of an already
issued physical forecast, beyond what a constant combination of two final
predictions supplies? This differs from E029, whose output weights are fixed.
Do not expand C1 into a search over variables, residual definitions or blend
weights.

Use the installed checkpoint
`autogluon/chronos-2-small@ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a`.
Retain E022's exact 168-hour target context, observation-age gates, station
identities and target definition. Add **one** named covariate, `nbh_tmp_f`.
At each decision `D`, use exactly E026's source rule: cycle `c=D−2h` and the
original NBH object edition with its explicit eligibility/provenance checks.
Do not select a later cycle because it predicts that target better.
[Physical baseline](PHYSICAL_STATION_BASELINE.md),
[E026 source rule](../config/e026_nbh_comparison.json).

The corresponding input is:

```text
target: last 168 hourly station observations eligible at D
past_covariates.nbh_tmp_f:
    current eligible cycle's TMP at matching past valid hours; otherwise NaN
future_covariates.nbh_tmp_f:
    same cycle's TMP at every forecast-grid hour through the target
```

This is deliberately a *current-cycle* covariate, not an invented 168-hour
archive of historical physical residuals. NBH's first valid hour can overlap
the last eligible station observation, but this usually supplies only a short
past covariate overlap. That sparsity is a real risk to zero-shot conditioning.
At least one finite past overlap and the full required future path are required
to use C1. If absent, emit the frozen univariate Chronos result for that case
and record a covariate-unavailable fallback. Keep all original cases and report
how often guidance was actually used. Never label the fallback as successful
physical conditioning.

The installed API requires future-covariate keys to also exist in the past
dictionary. A synthetic adapter check must establish how this exact version
handles sparse/NaN past covariates before registration; an all-finite toy
example is insufficient. Do not replace NaNs with future realized temperatures
or backfill the current forecast into hours before that cycle's coverage.
The [official pipeline](https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos2/pipeline.py)
and locally inspected version define the input contract.

Set `cross_learning=False`. Keep each decision independent. Enabling joint
learning across a batch of different forecast origins can expose an earlier
case to later cases' contexts, even if each individual dictionary was valid.
Within-case covariates still work without sharing information across unrelated
origins. Synthetic checks must show that appending future-origin cases,
changing future source editions and permuting batch order leave an earlier
forecast unchanged.

**Fixed future budget:** zero neural fits; at most 9,870 decision forecasts;
1,200 seconds total execution; two CPU threads; 4 GiB peak process memory;
zero network calls or weight downloads during evaluation. Keep the original
3,271 calibration and 6,599 development case IDs if root registers this as a
retrospective development comparison. No station/date substitutions. Use the
original per-horizon additive residual-quantile calibration on July 6–19;
July 20–August 16 remains reused development data, not a fresh final test.
Future registration must pin source hashes, fallback counts/rules and all
comparison code before reading any candidate score.

**Advance criterion:** at least 90% of the fixed development cases must receive
the covariate, every case must receive its declared forecast/fallback, and
day-weighted calibrated quantile loss must improve at least 5% against **both**
frozen univariate Chronos and the calibrated NBH baseline. Retain raw MAE,
bias, coverage, widths and all daily losses. Failure, budget exhaustion or
weaker conditioning produces no model promotion. Even a pass authorizes only
a new prospective test of the same information path.

Why not train residuals immediately? A true residual history requires the
right guidance edition at each historical decision and a matched observed
target. The current-cycle path above is not that dataset. The
[bridge's residual-state proposal](LIVE_FEATURE_BRIDGE.md) is preferable once
those pairs and their delays are available, but changing the learning target
would require its own later declaration. Preserve guidance-cycle transitions;
never subtract the newest forecast retrospectively from old observations.

## C2: one tiny, explicit short-context comparator

Pin `ibm-granite/granite-timeseries-ttm-r2`, branch
`90-30-ft-l1-r2.1`, commit
`cd2ad2a54ba5531fbcf6ba3b7a763a6e14223680`.
Official metadata reports **805,280 F32 parameters**, approximately 3.22 MB
of raw parameter storage, not total process memory. The recorded commit's
last-modified date is **26 February 2025**; that does not independently audit
its training corpus, but predates the 2026 comparison observations.
[Pinned checkpoint metadata](https://huggingface.co/api/models/ibm-granite/granite-timeseries-ttm-r2/revision/cd2ad2a54ba5531fbcf6ba3b7a763a6e14223680).

Its configuration has context **90**, output **30**, MAE training loss,
frequency-prefix tuning, and channel mixing disabled. The hourly frequency
token is **7** in the inspected official preprocessor. Use a single target
channel and take the exact forecast step needed after the last eligible
observation; do not interpret output position one as the decision time.
Keep unused later output steps unused. There is no pretrained 168-hour variant
in the inspected branch census, so do not invoke automatic checkpoint
selection or silently pad 168 values to 180/512.
[Pinned configuration](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2/blob/cd2ad2a54ba5531fbcf6ba3b7a763a6e14223680/config.json),
[frequency mapping](https://github.com/ibm-granite/granite-tsfm/blob/main/tsfm_public/toolkit/time_series_preprocessor.py).

An important implementation trap: the inspected TTM forward path passes
`past_observed_mask` into its scaler, then patches the scaled values. This does
**not** show that arbitrary NaN fillers are safe or that the mixing network
receives a separate missingness token. In particular, numerical `NaN×0` remains
NaN. A mask argument alone is not evidence of robust irregular-data modeling.
[Official implementation](https://github.com/ibm-granite/granite-tsfm/blob/main/tsfm_public/models/tinytimemixer/modeling_tinytimemixer.py).

For this one comparator, require 90 finite eligible observations on the last
90-hour grid. Otherwise use a separately pinned **90-hour univariate Chronos**
fallback, retaining the missing-input reason. This keeps the tested model
simple and avoids claiming it solves E028/E030's sparse minute observations.
All original cases remain in the accounting; no contiguous favorable periods
are selected. Compare against that same 90-hour Chronos on **every** case, and
also report the existing 168-hour Chronos baseline so the cost of shorter
context is visible. This asks a compute/architecture question, not whether
discarding older information can optimize the known errors.

Use a fixed per-station standardization fitted only on the permitted training
period, with scale floor 1°F; retain the checkpoint's internal scaling and
invert the external transform once. No evaluation-wide scaler. Pin evaluation
mode and verify reload/batch invariance. Although the configuration names a
Student-t distribution, this checkpoint uses **MAE loss**; do not claim its
point head supplies a trained Student-t probability distribution. Construct
uncertainty by the original calibration-only residual-quantile procedure.

**Fixed future budget:** one checkpoint, no neural fit, no new exogenous or
channel-mixing head; at most 9,870 TTM and 9,870 matched Chronos-90 forecasts;
1,200 seconds total execution; two CPU threads; 4 GiB peak process memory.
Before any evaluation, permit one separate maximum 16 MiB checkpoint fetch
only after artifact/revision pins and dependency checks are registered. **No
such fetch occurred or is being initiated by this report.** No version search
or substitute model after a compatibility failure.

**Advance criterion:** the complete panel must have at least 90% TTM use,
day-weighted calibrated quantile loss no worse than 2% above matched
Chronos-90, and total inference CPU at most half that comparator's. Report both
algorithms' complete time and memory, including fallback costs. A speed pass
with poorer calibration fails. This is a resource-efficiency hypothesis;
current Chronos latency is already small, so its direct profit benefit is low
unless later station scale or inference contention becomes a measured limit.

## Common failure accounting and next evidence

Freeze four contrasts in any combined registration: C1 versus Chronos-168 and
NBH, C2 versus Chronos-90 and Chronos-168. Use one shared calendar-day dependence
scheme for descriptive simultaneous intervals; do not count overlapping
hourly forecasts as independent weather experiments. The practical thresholds
above do not replace uncertainty reporting or the
[financial validation gates](../docs/REQUIREMENTS.md). No live-order or profitability
claim follows from a weather-loss comparison.

Root's E030 follow-ups establish that January is **not uniformly unavailable**:
coverage varies by date and station. They do not reverse E028's failed original
gate or prove a complete minute archive. MIA absence in the late-August canary,
literal `M` fields and incomplete peer coverage remain relevant. No minute
measurements enter either candidate here. A later spatial model must preserve
the fixed station family and explicit age/missingness inputs instead of
selecting whichever period has complete measurements.

Ten bounded documentation/metadata/source GETs were archived, all HTTP 200;
no weight or measurement endpoint was requested. Protocol **128523** and
[request manifest](../evidence/pretrained_path_sources.json) retain actual
receipts and hashes. Source IDs are **128534** (TTM branches), **128543** (TTM
card), **128553** (selector), **128561** (TimesFM), **128570** (Moirai),
**129570** (pinned TTM configuration), **129581/129589** (TTM model/configuration
source), **129712** (pinned model metadata), **129739** (preprocessor).
The installed Chronos pipeline SHA256 is
`482d28d81b7a50852fd081bd660f5ce39dab79534c237157efaf12bb586c07e1`.
Current repository source is documentation evidence, not automatically the
runtime to pair with a 2025 checkpoint; compatibility must be pinned and tested.

The next useful bounded scout task is a **synthetic-only adapter audit** of C1's
sparse past covariate and forecast-origin isolation, using the installed model
interface without downloading weights or reading weather labels. Its output
should be an exact input/provenance contract and missingness behavior, not
another model ranking. That resolves a practical blocker before any expensive
residual training or another historical comparison is considered.
