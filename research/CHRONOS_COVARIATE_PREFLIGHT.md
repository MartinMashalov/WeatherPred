# Chronos + NBH: synthetic interface and time-boundary audit

**6 September 2026. The original sparse current-cycle C1 is blocked.** The
installed API accepts its arrays, but the one finite past NBH temperature
produces a normalization scale of **0.00001°F**. A subsequent 1°F change becomes
100,000 before the model's `arcsinh` transform, approximately 12.206 afterward.
This is a concrete input-distribution problem, not an observed forecast loss.
No model prediction, training, weight read/download, real NBH trajectory read
or new weather label access occurred.

The [synthetic builder](probes/chronos_covariate_preflight.py) and
[12 isolated-environment checks](probes/chronos_covariate_checks.py) exercise the actual
installed dataset, parameter-free normalization and group-mask functions.
They do not instantiate a forecasting model. Four additional
[NumPy-only base-environment tests](../tests/test_chronos_covariate_preflight.py)
check the builder's timing, source gates and missingness without requiring
Torch/Chronos in a clean base installation. No package assertion is skipped or
weakened; the explicit isolated command remains required for model work.
The selected follow-up proposal
uses explicitly different **observed-then-guided temperature** semantics;
it still needs a new extraction and experiment registration.
[Unregistered draft design](../config/c1_nbh_covariate_design.json).

## Exact installed interfaces and time grid

Package versions are `chronos-forecasting 2.3.1`, `torch 2.14.0`,
`numpy 2.4.6`, `transformers 5.16.1`. The checkpoint configuration is the
existing pinned Chronos-2-small revision
`ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a`, with 16-step input/output patches.
The draft records SHA256 for every package source inspected, the local
checkpoint configuration and the original relevant research sources.

| Installed source | Exact behavior checked |
| --- | --- |
| `chronos/chronos2/pipeline.py:468` | `Chronos2Pipeline.predict(inputs, prediction_length=7, batch_size=64, context_length=168, cross_learning=False)`. Future dictionary keys must also exist in the past dictionary. |
| `chronos/chronos2/preprocess.py:217` | `from_list_of_dicts` validates dictionary keys/schema, converts numeric arrays to F32, and keeps numeric task values separate. No categorical covariates are proposed. |
| `chronos/chronos2/dataset.py:182` | TEST mode keeps the full past context, sets `future_target=None`, and masks future target rows with NaNs. It does not carve a label out of the supplied history. |
| `chronos/chronos2/dataset.py:244` | Each dictionary gets a separate group ID shared only by its target/covariates; `target_idx_ranges` identifies output rows. |
| `chronos/chronos2/model.py:123` | The installed static group-time-mask function blocks attention between unequal group IDs. |
| `chronos/chronos2/pipeline.py:639` | `cross_learning=True` replaces all group IDs with zero. Reject this and the deprecated `predict_batches_jointly` override in the future runner. |
| `chronos/chronos_bolt.py:105` | `InstanceNorm.forward` estimates each row's mean/std from finite past values; zero std becomes `eps=1e-5`. Future values use that same past normalization. |
| `chronos/chronos2/model.py:392` | Missing context values are masked in patches; numeric context, time encoding and the observed mask enter the model together. This differs from merely imputing a number. |

Paths in this table are relative to the installed package directory
`tmp/forecast-model-env/lib/python3.11/site-packages/`.
The public [official pipeline source](https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos2/pipeline.py)
documents the same kinds of inputs, but this audit relies on the pinned local
bytes, not whatever later appears at `main`.

The E022 grid ends at `floor((D−15min)/hour)`, which is **D−1h** for these
hourly decisions. It still ends there when that final observation is missing.
The latest *finite* observation can be D−2h without changing the grid.
The original model always emits seven steps; retain that shape:

| Decision horizon | Target step / zero-based index | Known future grid used | Current NBH leads |
| --- | --- | --- | --- |
| 1h | 2 / 1 | D, D+1h | 2, 3 |
| 3h | 4 / 3 | D through D+3h | 2 through 5 |
| 6h | 7 / 6 | D through D+6h | 2 through 8 |

Set the remaining future-covariate positions **after the target** to NaN.
They are not labels or interpolated observations. Seven-position arrays retain
a common schema across horizons without using extra guidance after the target.
Do not mistakenly request 1/3/6 prediction steps or shift the grid to the last
finite observation. The synthetic tests retain the missing-last-slot case.

## Original failure and two honest remedies

Current NBH cycle `c=D−2h` starts at lead 1, valid D−1h. It therefore overlaps
the 168-hour observation context only once. The installed normalization
computes `loc=y`, `scale=eps` for that row. The test confirms acceptance by
`Chronos2Dataset`, one finite covariate point, scale exactly equal to the F32
representation of `1e-5`, and transformed future change greater than 12.
Do not patch `InstanceNorm`, fabricate two past anchors or fill historical
hours with the current-cycle forecast to hide this failure.

| Remedy | Honest meaning and remaining limitation | Status |
| --- | --- | --- |
| A: genuine older NBH history | At each past grid time v, use only the actual archived forecast at **fixed lead 3**, cycle v−3h, under the original conditional publication/source gates. Restrict acquisition to the existing 498-object census. Require at least eight finite points across three UTC dates and population standard deviation at least 1°F; otherwise retain a univariate fallback. | **Reserved, not selected or inspected.** Early contexts may lack coverage. No new downloads or retrospective substitution are implied. |
| B: observed-then-guided temperature | Past covariate equals eligible observed temperature; future covariate equals the eligible NBH path. It intentionally duplicates the past target, so its past scale matches the target's. It is explicitly **not historical NBH**. | **Selected for a new draft only.** Synthetic preprocessing is feasible. A near-identity relationship may simply cause the model to copy NBH; improvement remains untested. |

B is chosen for interface/source feasibility before any B forecast is made.
Its name is `observed_then_guided_temperature_f`. Both past arrays preserve
the same 168 slots and missing masks. No future observation is ever supplied.
If any required guidance value is missing, use the matched univariate forecast
and record the fallback; do not delete that case.

This is a change of covariate definition, requiring a **new** design
registration. It is not a repair that retroactively makes the original C1
valid. Conditioning on known future covariates has prior art in
[Chronos-2](https://arxiv.org/abs/2510.15821); that paper does not establish the
effectiveness of this particular observed-to-guidance transition. No novelty
or weather-skill claim is made.

## New raw-card extraction must be declared first

E025/E026's existing parsed target cells at leads **3, 5 and 8** cannot supply
the intervening trajectory. The original archived station cards contain the
needed columns, but this round **did not read their measurement bodies**.
Before any real extraction, register the new parser/source hashes and the
exact union **TMP leads 2–8** for B, with per-case truncation at its target.
The draft does not authorize that extraction.

Recheck complete census membership and permitted reuse lineage, response and
record hashes, range offsets, complete card bytes, station/header/run, version
and conditional publication eligibility **before reading TMP values**. For
each requested cell, verify the matching UTC column and retain its raw lexeme,
field/cell byte offset, forecast hour, UTC valid time, source/card hashes and
actual September receipt. Blank/`-99`/missing TMP rows remain missing; duplicate
fields, malformed widths or identity mismatches fail the parser. No offset,
station or cycle selection can depend on the temperature found.

Historical original-object publication eligibility remains an assumption;
our September receipt is not evidence of having traded with that forecast in
July. The exact original E026 source gate must remain intact. A prospective
deployment would require actual local receipts before decisions.

## Grouping guarantees and the limit of this audit

Synthetic append/permutation tests compare the exact earlier context and
future-covariate tensors before and after appending a later-origin task whose
values are deliberately very different. They remain unchanged. Separate
IDs `[0,0,1,1]` block cross-task attention in the installed mask function; the
deliberate unsafe all-zero-ID control permits it. TEST batches have no future
target, and an extra `settlement_label` feature key is rejected.

These are **input/mask invariance checks, not output invariance measurements**.
A future registered inference run must still verify predictions under
append/permutation within its fixed integrity budget. Keep evaluation mode,
fixed package bytes and `cross_learning=False`. Reject unknown keyword
overrides. Reassemble outputs through a stable case-ID manifest rather than
assuming shuffled batches still correspond to the original list order.

The installed preprocessor also rejects a mixed batch containing a covariate
dictionary and a univariate fallback dictionary. Segregate those schemas,
retain the IDs, and map the exact matched baseline forecast to fallback cases.
Do not add a fake covariate just to make the batch shape convenient.

## Finite follow-up design and proof

There are only **two neural variants** in the draft: B and the matched
pretrained univariate Chronos. The existing frozen NBH result is a necessary
reference because B could merely copy it. Root additionally requires **all
three frozen E029 combinations** as references; their development results are
already known, so this comparison cannot be called untouched. No third neural candidate or
hyperparameter search is allowed. Keep all original 9,870 cases, July 6–19
calibration, July 20–August 16 reused development and the original calibration
procedure. Thresholds are unchanged: at least 90% real guidance use and at
least 5% improvement in day-weighted calibrated quantile loss against the
matched univariate, NBH and **each of the three** frozen blend references to justify only a
prospective follow-up. Retain all errors and adjust the declared family of
comparisons for all five contrasts.

The proposed run is capped at **1,200 seconds, two CPU threads, 4 GiB peak
memory, zero fits, zero network and zero weight downloads**. At most 19,740
main forecast tasks plus 64 declared integrity tasks are permitted. A missing
source, timeout, nonfinite result or inconsistent output mapping remains a
recorded failure; it never triggers a parameter retry. No financial gate is
waived by this interface study.

Executed this session, after the final source edits:

```sh
tmp/forecast-model-env/bin/python -m research.probes.chronos_covariate_checks -v
uv run pytest -q tests/test_chronos_covariate_preflight.py
uv run ruff check research/probes/chronos_covariate_preflight.py \
  research/probes/chronos_covariate_checks.py \
  tests/test_chronos_covariate_preflight.py
```

The first lint check identified an import-style issue (`Callable`); the import
was corrected without changing tests or assertions. Final execution evidence
is in [the preflight check record](../evidence/C1_covariate_preflight.json).
The next step is independent review of B's extraction/feature definition,
followed by a new registration if root chooses to proceed. The original sparse
C1 remains blocked and retained.
