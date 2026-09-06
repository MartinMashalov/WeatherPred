# Small forecasting models: what we researched and actually tested

Evidence date: September 6, 2026. This study supports WeatherPred's research program; it does not establish trading profitability or a world-leading weather forecast.

**The practical candidate is Chronos-2-small, but it has not earned promotion.** It runs comfortably on this laptop. Two fixed pretrained variants lost to persistence overall. One supervised fine-tune improved the short-horizon development result and the model's quantile score, but still lost to persistence on aggregate temperature error. All results are retained below.

## Model comparison

Parameter counts are approximate vendor specifications except where measured in our run. Benchmark rankings on unrelated time series do not establish skill on Kalshi settlement targets.

| Candidate | Size and model type | Practical use here | License / constraint | Decision |
| --- | --- | --- | --- | --- |
| [Chronos-2-small](https://huggingface.co/autogluon/chronos-2-small) | 28 million parameters; transformer; multiple series and explanatory variables | Small enough for local CPU inference and supervised adaptation; produces quantiles, which describe the spread of possible outcomes | Apache-2.0 model card | Tested. Retain for a further controlled pilot; do not promote from current results. |
| [Chronos-Bolt-tiny](https://huggingface.co/amazon/chronos-bolt-tiny) | About 9 million; patch-based transformer | Cheap univariate benchmark; less useful for the planned station and forecast inputs | Apache-2.0 | Reserve as a simpler neural comparator; not tested here. |
| [IBM TinyTimeMixer R2](https://huggingface.co/ibm-granite/granite-timeseries-ttm-r2) | Variants start around 1 million; compact mixing network | Useful non-transformer control. Supports small-scale adaptation, but requires suitable history lengths and per-channel scaling | Apache-2.0 | Reserve. It should not be described as a transformer just to match a preferred architecture. |
| [TimesFM 2.5](https://github.com/google-research/timesfm) | About 200 million plus an optional quantile head | Longer contexts and a larger comparison model; official [LoRA example](https://github.com/google-research/timesfm/tree/master/timesfm-forecasting/examples/finetuning) trains small adapter matrices rather than every weight | Apache-2.0 weights through version 2.5 | Secondary option after expanding data; no local benchmark here. |
| [TimesFM 3.0](https://huggingface.co/google/timesfm-3.0-pytorch) | Newer multivariate transformer, released August 2026 | Relevant research comparison, with direct multivariate and covariate support | Current pretrained weights have a separate non-commercial, non-production license; source-code license is different | Exclude from the proposed trading deployment. |
| [Aurora](https://microsoft.github.io/aurora/models.html) | A spatial atmospheric foundation model requiring gridded surface and pressure-level inputs | Potential upstream weather provider/model research, not a drop-in replacement for the city-index series | Check the separate [license](https://github.com/microsoft/aurora/blob/main/LICENSE.txt); its authors direct commercial inquiries to Microsoft | The small checkpoint is explicitly recommended for debugging only. Do not call it a strong small production forecaster. |

Chronos-2's official [implementation](https://github.com/amazon-science/chronos-forecasting) supports shared information across related series and known explanatory variables. Its [fine-tuning API](https://github.com/amazon-science/chronos-forecasting/blob/main/src/chronos/chronos2/pipeline.py) supports both full adaptation and LoRA. We tested full adaptation because this 28-million-parameter version fits locally without a GPU.

## Exact local setup

The machine reported **Apple M1 Max, 32 GiB RAM**. The production Python environment had no PyTorch installed. We created an isolated environment under ignored `tmp/forecast-model-env`; no production dependency or frozen live strategy changed.

Installed versions were `chronos-forecasting 2.3.1`, `torch 2.14.0`, `transformers 5.16.1`, `numpy 2.4.6`, and `huggingface-hub 1.30.0`. Inference and training used CPU, float32 numbers and two CPU threads.

The checkpoint is pinned to:

```text
autogluon/chronos-2-small
ddec01313e50b6bc58ebaa92ede81bc24a3d9f9a
```

The public repository reports a December 3, 2025 modification date, before the August 2026 labels used here. That makes direct inclusion of these later observations implausible for this pinned checkpoint, but its complete training corpus has not been independently audited. Broader station and weather-pattern overlap remains possible. File hashes and the exact package versions are recorded in the result JSON.

The actual model contains **27,934,624 parameters**. A warmed single-series forecast took **18–20 milliseconds** for 512 historical minute values and 40 future minutes. The initial call took 29 milliseconds. Peak process memory during the initial comparison was **774,356,992 bytes**, including the Python runtime and libraries. These are measurements from one laptop run, not a service-level latency guarantee.

## Pretrained comparison: both variants retained

The comparison uses only the existing E006 August training archive: dataset record **12322**, parent model **12323**, and its original historical index sources. It uses all targets on August 20–31 at UTC hours divisible by six: **48 hourly events, three decision horizons, 144 forecasts per variant**. Two variants were specified before inspecting their scores:

1. The recent Miami index alone.
2. The same index plus the five component station temperatures and clock sine/cosine values.

Every forecast ends its input at the same last eligible value as E006. The index lag is five minutes, maximum input age is ten minutes, and missing context remains missing; there is no interpolation from future observations. Station covariates also require their supplied receipt timestamp to be no later than the decision. Only clock features are provided in the future. The model forecasts the exact number of minutes from the last eligible value to settlement.

An initial implementation guard caught one six-minute-old input, where the first adapter had assumed every input was exactly five minutes old. It stopped before any August predictions. The corrected adapter preserves that older input and forecasts the exact settlement step. The failed run and correction are retained; no cases were removed to improve a score.

Root mean squared error (RMSE) measures typical temperature error, with larger errors penalized more heavily. Lower is better.

| Decision horizon | Persistence | Pretrained index only | Pretrained index + stations + clock |
| --- | ---: | ---: | ---: |
| 30 minutes | 0.9358°F | 1.0681°F | 1.1278°F |
| 15 minutes | 0.7213°F | 0.7333°F | 0.7755°F |
| 5 minutes | 0.5146°F | 0.5238°F | 0.5093°F |
| All horizons | **0.7440°F** | 0.8068°F | 0.8431°F |

The station variant's tiny five-minute RMSE improvement does not establish a useful edge: its mean absolute error was worse than persistence, and its overall error was substantially worse. Adding variables did not automatically improve the model.

The two 144-forecast batches took **0.522 seconds** and **3.994 seconds** respectively. All 288 forecasts were finite and had ordered quantiles. This checks numerical behavior, not probability calibration.

Evidence: `reports/model_candidate_protocol.json`, `reports/model_candidate_chronos2_small.json`, `reports/model_candidate_smoke_output.txt`. The original source bytes and hash are preserved in `reports/model_candidate_smoke_source_v1.json`.

## E021: one supervised training experiment

This is a chronological development experiment, not an untouched test. Its evaluation days had already been examined in the preceding comparison and earlier WeatherPred research.

The configuration and source hash were written before training. There was exactly one fit: **100 optimizer steps**, learning rate **0.00001**, batch **8**, **512-minute context**, **40-minute output**, minimum history **60**, seed **62026**, and the AdamW optimizer with a linear learning-rate schedule. No evaluation data were passed to the fitting routine; no best-checkpoint selection or parameter retry occurred.

Training used the minute index beginning August 20 and ending strictly before the August 24 fit cutoff after allowing the assumed five-minute publication lag. The August 24 midnight decisions occur before that fit cutoff, so those three forecasts are explicitly excluded. Evaluation retains the remaining **93 forecasts on 31 hourly targets across eight days**, with identical inputs for the trained model and pretrained comparison. The minimum context and target-window sampling are training implementation choices; they do not turn overlapping minute windows into independent weather events.

The model completed all **100 steps in 22.55 seconds**, including saving its checkpoint, below the fixed five-minute budget. Peak process memory was **1,587,675,136 bytes**. The original weights stayed unchanged, and the new parameter hash differs. Reloading the saved model reproduced all 93 deterministic forecasts exactly, with maximum quantile difference 0.0°F.

| Decision horizon | Persistence RMSE | Pretrained RMSE | Fine-tuned RMSE |
| --- | ---: | ---: | ---: |
| 30 minutes | **0.9488°F** | 1.0559°F | 1.0702°F |
| 15 minutes | 0.7074°F | **0.6801°F** | 0.7588°F |
| 5 minutes | 0.5375°F | 0.5171°F | **0.4512°F** |
| All horizons | **0.7504°F** | 0.7842°F | 0.8010°F |

The fine-tuned model's average quantile loss improved from **0.18215 to 0.17221** versus its pretrained version. Quantile loss penalizes over- and underprediction according to the probability level being forecast. That is useful distributional evidence, but it does not show that its probabilities are calibrated at traded strikes or that a trade survives fees.

The first post-fit score failed that replay: the library had left the model in training mode, with random dropout still enabled. We preserved that rejected score and its source, wrote an evaluation amendment, and rescored the same saved weights with `model.training = false`. No training or hyperparameter search was repeated. The table contains only the corrected deterministic results. The pretrained comparison also used evaluation mode.

The five-minute mean absolute error also improved, from **0.4100°F** for persistence to **0.3602°F** after training. This is an exploratory subgroup lead. The aggregate RMSE and the 30-minute horizon worsened. **The experiment does not justify replacing the baseline.** It suggests evaluating a short-horizon specialist on genuinely new days while retaining the failures and correcting for the model/horizon comparisons.

Evidence: `reports/model_candidate_E021_protocol.json`, `reports/model_candidate_E021_finetune.json`, `reports/model_candidate_E021_output.txt` (original training log and rejected stochastic score), `reports/model_candidate_E021_replay.json`, and `reports/model_candidate_E021_evaluation_amendment.json`. Original stochastic predictions: `reports/model_candidate_E021_initial_training_mode.json`. Checkpoint: ignored `tmp/E021-chronos2-small/finetuned-ckpt`.

These historical source payloads were collected September 6. The five-minute historical publication lag is an assumption; the replay cannot prove those exact index versions were available at those past decisions. Neither experiment used September development labels or the sealed final holdout, and neither simulates a trade.

## Why supervised learning comes before RL here

Reinforcement learning is a method for learning actions from rewards. It is not a source of additional weather information. For a forecast probability `p` and observed binary result `y`, the Brier loss is `(p − y)²`. Its expected gradient is `2(p − q)`, where `q` is the true outcome probability. Direct supervised learning already provides a clear signal toward honest probabilities. Replacing that signal with a sampled policy-gradient reward can add variance without adding information.

RL-based forecast refinement is also **not an unprecedented idea**. [PostTime, May 2026](https://arxiv.org/abs/2605.29401), studies supervised and reinforcement training of a language-model reviser around a time-series forecast. A separate [June 2026 probabilistic forecasting study](https://arxiv.org/abs/2607.00164) reports calibration failures when noisy single outcomes drive ordinary reward training, then investigates alternative rewards. These are useful research leads, not evidence that their methods beat Kalshi weather prices.

A defensible sequence is:

1. Improve the forecast distribution with supervised quantile or likelihood training; compare against strong calibrated statistical baselines.
2. Calibrate the distribution using only earlier dates, then measure probability error specifically where the policy would trade.
3. Freeze those forecasts. Test whether an execution policy can choose better orders, cancellations and position reductions under realistic future order flow.
4. Consider RL for that sequential execution problem only after conservative simulators and sufficient forward evidence support its rewards. Compare it with transparent threshold rules and simple inventory policies.

The execution reward should reflect net bankroll growth after fees, imperfect fills and inventory risk. A simulator that rewards filled spread while ignoring unmatched inventory would teach an exploitable simulation artifact. Our current sample of independent trading days is too small to establish that such a policy generalizes.

## A concrete next model hypothesis

A promising application is **predicting the local error of a physical weather forecast**, rather than asking a generic transformer to replace all atmospheric modeling. Give the model recently available station observations, upstream observations, the latest eligible HRRR/NBM forecast, cloud and wind conditions, ensemble disagreement, and explicit ages of those inputs. Predict a distribution for the residual: actual settlement-target temperature minus the operational forecast.

Then keep three distinct outputs:

- The physical target distribution, including joint trajectories when the contract depends on daily extrema or several days.
- The settlement mapping: exact station/index, calendar, rounding and the possibility of source revisions.
- The executable trade value after current depth, fees, arrival delay, partial fills and capital constraints.

This makes a testable hypothesis: age and source disagreement may reveal when an apparently precise forecast should have a wider distribution, and when a short-horizon correction adds information before the market adjusts. The combination may be useful for this project; we have not established that nobody has studied it before.

The parallel market study also found a public source of hourly station weather across 37 primary stations. Its archived weekly snapshot is record 78077. Acquire earlier eligible weeks and audit exact target definitions before using it: a station hourly average is not interchangeable with the Miami multi-station index. Historical corrections and source receipt times still need explicit treatment.

Before another model search, fix one chronological pilot, a small candidate count and a new forward evaluation window. Compare station-held-out and date-held-out behavior; report confidence intervals using weather days or systems as dependent groups. A prettier forecast or a lower error on these eight reused days cannot substitute for executable positive trading value.

## Reproduction

These commands were executed successfully in this session. On a fresh research copy with the archived data available:

```sh
uv venv tmp/forecast-model-env --python 3.11
uv pip install --python tmp/forecast-model-env/bin/python \
  'chronos-forecasting==2.3.1' 'torch==2.14.0' 'transformers==5.16.1'
tmp/forecast-model-env/bin/python research/probes/forecast_model_smoke.py
tmp/forecast-model-env/bin/python research/probes/forecast_model_smoke.py --finetune
```

The probe writes report files under `reports/` and model files under `tmp/`; preserve an earlier result before repeating it. It reads the local SQLite archive through a read-only connection. It does not submit market orders, change a frozen live strategy, train through an external account or start a server.
