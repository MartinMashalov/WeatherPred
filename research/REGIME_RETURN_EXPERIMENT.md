# A bounded regime-conditioned return experiment

**Unregistered design, 6 September 2026. No additional price extraction,
model fit, performance score or order was run for this proposal.**
The hypothesis is that persistent price changes and temporary quote movement
have different subsequent execution-adjusted returns. Test whether a small
switching autoregression adds information beyond E032's return predictor.
These are statistical price regimes, not identified weather regimes.

The [E032 configuration](../config/e032_statistical_lab.json),
[candidate code](../weatherpred/statistical_candidates.py) and
[data adapter](../weatherpred/statistical_replay_data.py) remain unchanged.
A new source-field extraction and experiment registration must precede access
to the additional historical endpoints. The 2026 panel remains reused
development data; the sealed 2025 quarter is inaccessible.

## Three fixed tracks

| Track | Model | Purpose |
| --- | --- | --- |
| B0 | Unchanged E032 `ridge_net_return`, using its pinned monthly artifacts | Preserve the exact existing feature transform, prediction and account baseline. |
| B1 | One-state AR(1) plus a small additive return correction | Determine whether longer history and an autoregressive feature help without hidden regimes. |
| C1 | Two-state AR(1) plus the same correction interface | Test whether switching dynamics add information beyond both controls. |

B1 prevents crediting the regime model merely for receiving extra history.
No alternative lookback, state count, initialization, penalty, entry threshold,
exit or sizing rule is searched. Cash remains the common reference. If the
complete E032 return baseline is unavailable, this proposal cannot launch;
another baseline needs its own declaration, not a reconstructed favorable subset.

## Seven endpoints produce six changes

For each existing E032 feature-eligible opportunity at D, extract the exact
same-contract endpoints **D−6h, D−5h, D−4h, D−3h, D−2h, D−1h, D**.
There are seven levels across six hours. Every timestamp must be inside the
recorded market window and meet the existing finite two-sided quote rules.
Current spread remains at most8¢; no additional historical spread threshold
is introduced. Retain exact quote strings, source hashes, actual September
receipts and the explicit assumed historical availability separately.

In chronological order, define

```text
m_k = (bid_k + ask_k)/2, k=0,...,6
l_k = logit(clip(m_k, 10^-6, 1−10^-6))
r_k = l_k − l_(k−1), k=1,...,6.
```

Condition on the first observed change, r1, and model r2 through r6.
Each context consequently has **five AR emissions and four transitions
between modeled states**. Do not invent an earlier zero change, a seventh
change or a connection between different contracts.

Missing any endpoint retains the opportunity and returns B0's exact prediction
for B1 and C1. No moved decision, carried quote, alternate strike or favorable
subset. A complete flat-price context is valid; an entirely degenerate training
history is a fit failure. These are different conditions.

## Training window, weighting and model

Use E032's six March–August2026 fit dates and five-day decision buffer; the
August model remains fixed through September5. Regime training contexts have
D strictly before fit cutoff minus five days and quote endpoints no later
than D. No target, future return or eventual fill chooses their membership.
The unsupervised regime fit can use an eligible context without a released
settlement label; the return correction has a separate label-time gate.

Give each eligible source day weight1, split equally among eligible events
and then complete-history contracts. Call this sequence weight w_s. Estimate
the pooled mean mu_r and standard deviation s_r from all six changes, assigning
each change weight w_s/6. Standardize u_k=(r_k−mu_r)/s_r. A nonfinite or
at-most10^-6 training standard deviation fails the model; this numerical gate
does not assert a reporting precision or market tick size.

For conditional sequence likelihood, use v_s=w_s/5. In state j in{0,1}:

```text
u_k = a_j + phi_j*u_(k−1) + epsilon_k
epsilon_k | z_k=j ~ Normal(0, sigma_j^2)
P(z_k=j | z_(k−1)=i) = A_ij.
```

The first modeled state has fixed prior(1/2,1/2), not an estimated prior.
Minimize the negative weighted conditional log-likelihood plus fixed penalties:

```text
J = −sum_s v_s*log p(u_(s,2:6) | u_(s,1), theta)
    + .5*sum_j(a_j^2 + phi_j^2) − sum_(i,j)log A_ij.
```

Fix −.95≤phi_j≤.95, sigma_j≥.10 in standardized units, and
.02≤A_ij≤.98. The transition penalty corresponds to one added count per
entry in its maximization step. These are predeclared regularization choices,
not values selected from trading results. The likelihood is a working model;
overlapping contracts and cities do not become independent observations.

## Deterministic initialization and twenty updates

First fit a pooled constrained AR(1) on the five conditional changes, using
the same sequence weights and ridge penalty1. Its weighted least-squares
objective is sum(v_s*residual²) + a² + phi². If its unconstrained slope leaves
the allowed interval, fix the slope at the closest boundary and reoptimize
the intercept. Let a, phi and v denote its intercept, slope and weighted
residual mean square. Initialize the two-state model exactly as follows:

```text
a_0 = a_1 = a
phi_0 = max(−.95, phi−.25); phi_1 = min(.95, phi+.25)
sigma_0^2 = max(.01, .5*v); sigma_1^2 = max(.01, 1.5*v)
A = [[.9,.1],[.1,.9]]; initial state probabilities = [.5,.5].
```

No random start, restart or initialization search. Execute **exactly twenty
generalized EM updates**, unless a registered numerical failure stops the fit:

1. Use scaled/log-space forward and backward calculations on each **training**
   sequence to obtain state responsibilities gamma and transition
   responsibilities xi. Weight sufficient statistics by v_s. The posterior
   within a sequence is ordinary HMM inference, not a calendar-weight-tempered
   posterior.
2. Update each transition row from its weighted expected counts plus1 per
   entry. Compute its first probability as first count divided by row total,
   constrain it to[.02,.98], and set the other to its complement. This is the
   exact two-state constrained row optimum.
3. For each state, with its old variance fixed, solve weighted least squares
   for a and phi with weights v_s*gamma and penalty
   sigma_old²*(a²+phi²). Constrain the slope as above and reoptimize the
   intercept if necessary. Then update variance to the weighted residual
   mean square with floor.01. These are successive conditional maximizations,
   hence generalized EM, not an asserted exact joint coefficient/variance
   maximization.
4. Record parameters, objective, occupancy and numerical status at every
   update. A nonfinite value, effective state mass at most10^-8, or an objective
   increase exceeding10^-8*(1+abs(previous_J)) fails the candidate. Never widen
   this tolerance after seeing a failure.

After every update, order states by(phi, sigma², a) and permute both axes of
A accordingly. State1 has greater fitted AR persistence, never better P&L.
At update20 require each state to carry at least5% of weighted emission mass
and the two slopes to differ by at least.05. Otherwise the two-persistence-state
hypothesis is unidentified and C1 fails; no dropped state or restart repairs it.
B1 is the original pooled AR fit, retained independently with no EM iterations.

## Forward-only features

Once parameters are fitted, generate correction features for both training
and evaluation contexts with the same **forward-only** procedure. Update the
fixed equal prior with the density of u2; for k=3,...,6:

```text
q_k(j) proportional to Normal(u_k; a_j+phi_j*u_(k−1), sigma_j²)
                       * sum_i q_(k−1)(i)*A_ij.
```

C1 adds these two features at D:

```text
h1 = q_6(state with greater phi)
h2 = mu_r + s_r*sum_j[(sum_i q_6(i)*A_ij)*(a_j+phi_j*u_6)].
```

h1 is an estimated **price-regime** probability, not P(contract settles YES).
h2 is a next-hour mean change in log-odds, not a weather observation,
probability change or dollar return. For B1, h1 is constant0 and
h2=mu_r+s_r*(a+phi*u6). Both models require the same seven endpoints even
though the one-state forecast uses only the latest change once fitted.

Backward probabilities are allowed inside training EM. They are forbidden as
return-model features, including within training contexts. Future prices must
not alter an already computed forward state or earlier prediction.

## Freeze the baseline; fit only a small correction

For each side, keep B0's fitted E032 model, scaler and base prediction unchanged.
Fit a two-coefficient **no-intercept** ridge correction, penalty1:

```text
residual_target = known_one_contract_net_return − B0_training_prediction
candidate_prediction = B0_prediction + beta_side·standardized(h1,h2).
```

Center and scale h separately for each head on complete-history training rows
with that head's known targets, using its original E032 weights. Both heads
receive the same two raw features; only these training-fitted transforms differ.
Preserve full-cohort weights when contexts are
missing: do not renormalize the covered subset to the entire day's mass.
A constant feature receives scale1 and centered value0. No extra intercept
allows a gain merely by assigning a different mean return to the covered subset.

This is a single greedy residual-regression stage. Training residual loss is
not validation evidence; no penalty or model choice uses it. Every known
rejected-order target remains0, while missing quotes and unreleased settlements
remain masked. Both heads independently need45 distinct known-label,
complete-history training days. Insufficient history or any fit failure fails
the whole candidate, retaining earlier fold artifacts. Only a particular
opportunity's missing context invokes exact B0 fallback.

The second-stage targets use only the costed scenario. Do not learn a separate
stress model, subtract fees a second time from its predicted net return, or
interpret its regression output as a calibrated confidence bound.

## Unchanged broker, calendar and research gate

Preserve E032's opportunity IDs and complete census, fit dates, 12-hour decision
horizon, spread gate, fixed ask+1¢ limit, score strictly above3¢, tie-break and
side/contract selection. Both scenarios use identical predictions: entry at
D+1h/+1¢ or D+2h/+2¢ against the same original limit and assumed fees. There is
no new exit rule or maker-fill assumption.

The $200 account starts September6,2025 with a cash-only protected prefix and
warmup; first permitted decisions are March2026. Keep1% risk sizing, aggregate
integer fees, exposure limits, locked capital, drawdown stop and actual
terminal-release assumptions. Unsupported late-period source windows remain
unsupported. Do not scale unit P&L instead of replaying aggregate cash movements.

Archive each fold's model and predictions before its successor, then the
complete decisions before broker resolution. Retain all three tracks and both
scenarios, every fallback/failed case, coverage by month and city, state
occupancies, parameter paths, numerical diagnostics and measured runtime.

C1 must satisfy the existing E032 complete-campaign, positive costed/stress
cash,60-release-day, no-pending-position and drawdown gates. Additionally require
complete-history coverage of at least80% in **each supported evaluation month**,
while retaining every fallback in the account. Require positive simultaneous
1/7/14-day-block lower bounds for C1's daily log-growth difference against
**both B0 and B1** in both scenarios. The fixed comparison family has ten
columns: all six track/scenario log-growth streams against cash, plus four
paired differences C1−B0 and C1−B1 under the two scenarios. Reuse10,000 shared
circular-block draws, seed6203201, and alpha.05/3 for each of the three block
lengths; retain degenerate columns as in E032. Identical contiguous daily
timestamps are mandatory. A failed planned track blocks all advancement; no reduced
successful-only campaign. These are reused-development research gates, not
profitability proof, a ruin estimate or an annual return forecast.

## Finite implementation budget and checks

Three tracks and six fit dates imply at most six pooled-AR fits, six two-state
fits and24 two-feature return-head fits. B0 is reused, not refitted.
Reuse each pinned pooled fit for both B1 and C1 initialization; recomputing
it would be an extra fit and is not part of this count. Cap each
track at600 seconds, total1,800seconds, two CPU threads and4GiB memory. No
network, extra model weights or automatic retry. Unstarted/budget-failed tracks
remain explicit. Candidate implementation follows E032 test-quality review
and a separate registration; this document starts nothing.

Required synthetic checks before historical access:

- Seven endpoints, six changes, five emissions; missing, duplicate, reordered,
  future or outside-window endpoints fail without imputation.
- Independently enumerate all2^5=32 latent paths for a tiny context and
  reproduce forward likelihood, state and transition responsibilities.
- Verify the constrained AR/variance and transition updates against J,
  the fixed20-update count and monotonicity tolerance; retain collapse errors.
- State permutation leaves likelihood/predictions unchanged after relabeling;
  state labels cannot consult return targets or profits.
- Appending D+1h data changes neither D's state nor its prediction. Forward
  features and backward training responsibilities remain separate objects.
- Each head rejects labels known at or after the fit cutoff. Corrected models
  cannot mutate E032 scaler/parameters or archived earlier-fold predictions.
- Every missing-context output equals B0 exactly and remains in account and
  coverage reports. No fill/outcome-based context or contract selection.

## Prior art and source access

Regime-switching autoregressive economic models long predate this project.
Hamilton's *A New Approach to the Economic Analysis of Nonstationary Time
Series and the Business Cycle*, Econometrica57(2),357–384(1989), is a
precedent. [Original journal record](https://www.jstor.org/stable/1912559).
The objective and implementation choices above are this proposal, not a claim
that that paper establishes weather-trading profits.

This round used one literature search and three page opens. The journal
record returned only a minimal page; an author's survey PDF and a paper-mirror
request timed out. Full text was not reviewed or reproduced. No market or
measurement endpoint, historical quote/outcome body or model-performance report
was accessed. No frozen source or experiment changed.
