# Mathematics, with implementation links

This guide explains the equations actually used in WeatherPred. Examples are
illustrations, not trading recommendations. Where a model relies on an assumption,
that assumption is part of the explanation. The system has not established a
profitable strategy or a validated probability of reaching a bankroll target.

## 1. Contracts, prices and profit

Let $Y\in\{0,1\}$ be a YES contract's final payout, $p=P(Y=1)$ its estimated
probability, $a$ the purchase ask and $F$ the entry fee for one contract.
The cost is $c=a+F$. Holding to settlement gives

$$\Pi=Y-c,\qquad E[\Pi]=p-c.$$

If a later sale receives bid $b'$ and costs exit fee $F'$, profit is instead

$$\Pi_{\rm exit}=b'-F'-a-F.$$

These are different strategies. The second can profit from a price movement
without waiting for the event to resolve. Both lose money if the spread and
fees exceed the available edge.

YES and NO payouts sum to one, but their executable prices do not have to sum
to one because bid and ask differ. If $b_Y,a_Y$ are YES quotes, the complementary
NO ask is $1-b_Y$ and the NO bid is $1-a_Y$. The midpoint
$m=(b_Y+a_Y)/2$ is used as a benchmark probability or signal, **not a fill price**.

Implementation: [books.py](../weatherpred/books.py),
[quote_screen.py](../weatherpred/quote_screen.py).

## 2. Fees, precision and a worked trade

The implemented quadratic taker fee model is

$$F_{\rm model}(q,a)=0.07\,M\,q\,a(1-a),$$

where $q$ is quantity and $M$ is the applicable fee multiplier. For a maker,
the coefficient is either zero or 0.0175, depending on the verified schedule.
The code first rounds trade fees upward to six decimal places, aligns cash
changes to the supported balance precision, and carries rounding amounts across
partial fills with the documented rebate treatment. A separate accumulator
belongs to each order. Historical experiments explicitly state their assumed
schedule; a current fee formula is not evidence of past fees.

For a single contract on a cent-balance account, buying at $0.40 costs $0.42:
the model fee is $0.0168, which yields a two-cent debit after alignment. Selling
at $0.60 receives $0.58 after its separate fee. Profit is $0.16, not $0.20.
At 0.0001-dollar balance precision the analogous isolated cash changes are
−$0.4168 and +$0.5832, giving $0.1664. Partial fills must follow the accumulator,
not independently rounded copies of a whole-order fee.

Implementation: [fees.py](../weatherpred/fees.py).
Primary specification: [Kalshi fee rounding](https://docs.kalshi.com/getting_started/fee_rounding).

## 3. Basket consistency

For $K$ mutually exclusive, exhaustive brackets, exactly one YES wins:

$$\sum_{j=1}^K Y_j=1,\qquad \sum_{j=1}^K(1-Y_j)=K-1.$$

Buying one of each YES has conditional net surplus
$1-\sum_j c_j$; buying one of each NO has surplus
$(K-1)-\sum_j c_j^{NO}$. For uncertain coverage, the scanner evaluates numeric
predicates across boundary points and uses the **minimum** possible aggregate
payout rather than assuming the partition is correct. Integer and continuous
settlement domains are checked separately.

Positive displayed surplus would still be conditional: prices can move while
the legs are filled, and multi-leg execution is not atomic.

Implementation: [contracts.py](../weatherpred/contracts.py),
[basket.py](../weatherpred/basket.py).

## 4. Probability calibration from market prices

Market-only logistic calibration transforms a quoted probability $m$ using

$$z=\alpha+\beta\log\frac{m}{1-m},\qquad
\hat p=\frac{1}{1+e^{-z}}.$$

Probabilities are clipped to $[10^{-6},1-10^{-6}]$ when taking logarithms.
The fit minimizes weighted binary negative log likelihood plus a slope penalty:

$$L=\sum_i w_i\{\log(1+e^{z_i})-y_i z_i\}
  +\frac{\lambda}{2}\beta^2,\qquad \lambda=1.$$

The intercept shifts the overall probability level; the slope changes how
extreme the probabilities are. This regularizer shrinks the slope toward zero;
it differs from the market-centered regularizer in the combination model below.
Fits use chronological earlier outcomes, not a random split of contracts.

Implementation: [calibration.py](../weatherpred/calibration.py).

## 5. Turning temperature forecasts into bracket probabilities

Let $T$ denote a latent temperature with Gaussian distribution
$T\sim\mathcal N(\mu,\sigma^2)$, and let $\Phi$ be the standard-normal cumulative
distribution function. For an inclusive integer bracket $L\leq T_{\rm published}
\leq U$, the model's rounding-cell approximation gives

$$P(L\leq T_{\rm published}\leq U)=
\Phi\!\left(\frac{U+0.5-\mu}{\sigma}\right)-
\Phi\!\left(\frac{L-0.5-\mu}{\sigma}\right).$$

Thus an integer 77–78°F bracket corresponds to the latent interval
$[76.5,78.5)$. Strict greater/less predicates use the corresponding first/last
allowed integer. Upper-tail subtraction uses survival functions where possible
to avoid numerical cancellation.

Hourly index contracts have a different 0.01°F lattice. A strict threshold $k$
uses latent boundary $(\lfloor100k\rfloor+0.5)/100$. This is a modeling
discretization, not permission to substitute one contract's rounding rules for
another's.

Implementation: [daily_forecasts.py](../weatherpred/daily_forecasts.py),
[forecasts.py](../weatherpred/forecasts.py).

## 6. Bias correction and empirical errors

For a base forecast $f_i$ and observed label $T_i$, residuals are $e_i=T_i-f_i$.
The daily model uses weighted bias and variance

$$\bar e_w=\frac{\sum_i w_i e_i}{W},\quad W=\sum_i w_i,$$

$$s_w^2=\frac{\sum_i w_i(e_i-\bar e_w)^2}
 {W-\sum_i w_i^2/W}.$$

The corrected mean is $\mu=f+\bar e_w$. Daily Gaussian spread is floored at
0.5°F. Station models estimate these quantities by station; the global version
pools stations. Training day weights prevent a day with more station rows from
automatically receiving more influence.

The daily empirical model uses samples $f+e_i$. Its bracket probability adds
one unit of Gaussian smoothing mass:

$$\hat P(B)=\frac{\sum_i w_i\mathbf1\{f+e_i\in B\}
                  +P_{\rm Gaussian}(B)}{W+1}.$$

The hourly empirical exceedance model instead uses Jeffreys smoothing:

$$\hat p=\frac{\#\{f+e_i\geq\text{boundary}\}+0.5}{n+1}.$$

Hourly Gaussian errors use the ordinary sample mean and standard deviation,
with a 0.05°F spread floor. Empirical and Gaussian models are alternatives;
the implementation does not pretend their uncertainty assumptions are identical.

Implementation: [daily_forecasts.py](../weatherpred/daily_forecasts.py),
[forecasts.py](../weatherpred/forecasts.py).

## 7. Daily mean and spread regression

The regression combines the daily grid maximum $g_i$, the native extrema proxy
$x_i$, the native spread $s_i$ and station indicators $I_{ik}$:

$$\mu_i=g_i+\sum_k\theta_k I_{ik}+\theta_d(x_i-g_i),$$

$$v_i=\sigma_i^2=0.25+\exp(\gamma_0)+\exp(\gamma_1)s_i^2.$$

The exponentials keep variance positive; the 0.25 term is a 0.5°F standard-
deviation floor. The fit minimizes Gaussian negative log likelihood with an
L2 penalty on mean coefficients:

$$L=\frac12\sum_i w_i\left[\log(2\pi v_i)+
\frac{(T_i-\mu_i)^2}{v_i}\right]+\frac12\|\theta\|_2^2.$$

An analytic gradient is supplied to L-BFGS-B and checked against numerical
derivatives in tests. The model's 18-hour extrema proxy is retained as a feature;
it is not relabeled as the contract's 24-hour daily maximum.

Implementation: [daily_forecasts.py](../weatherpred/daily_forecasts.py).

## 8. Hourly persistence, trend and information timing

Persistence uses the latest eligible value: $f_{\rm persist}=T_{\rm last}$.
The trend model fits a least-squares slope to recent points:

$$\hat\beta=\frac{\sum_i(t_i-\bar t)(T_i-\bar T)}
                  {\sum_i(t_i-\bar t)^2},\qquad
f_{\rm trend}=T_{\rm last}+\hat\beta(t_{\rm target}-t_{\rm last}).$$

Time is measured in minutes in this calculation. Residual distributions turn
these point forecasts into probabilities. Five- and ten-minute input limits
test freshness; they do not give permission to use later-received records.

For every decision $t$, a forward feature must satisfy
$t_{\rm received}\leq t$. Model initialization, valid time, object modification
time, document issue time and our receipt time are separate fields. Retrospective
object/document timestamps support explicitly limited historical assumptions,
not an assertion of independently observed historical public access.

Implementation: [forecasts.py](../weatherpred/forecasts.py),
[freshness.py](../weatherpred/freshness.py), [updated_forecasts.py](../weatherpred/updated_forecasts.py).

## 9. Logarithmic forecast combination

For a complete event, let $q_j$ be normalized market probabilities and $p_j$
weather probabilities. The combined probability is

$$r_j=\frac{q_j^{\beta}\max(p_j,10^{-6})^{\gamma}}
 {\sum_k q_k^{\beta}\max(p_k,10^{-6})^{\gamma}},
 \qquad 0\leq\beta,\gamma\leq3.$$

The sum is one by construction. Parameters minimize categorical negative log
likelihood with penalty $\tfrac12[(\beta-1)^2+\gamma^2]$, favoring the original
market distribution $(\beta,\gamma)=(1,0)$. Softmax and log-sum-exp provide
numerically stable evaluation. A zero fitted weather weight is a meaningful
result, not a failed optimization to hide.

Earlier-month weather fits produce the forecasts used to train the combination
stage. This prevents the second stage from learning from artificially good
in-sample predictions of its first stage.

Implementation: [forecast_pool.py](../weatherpred/forecast_pool.py).

## 10. Momentum, reversal, favorites and longshots

Let the current midpoint be $m_t$ and the earlier midpoint be $m_{t-h}$.
Define the price move $\Delta_h=m_t-m_{t-h}$. For threshold $\tau$:

- Momentum follows the sign of $\Delta_h$ when $|\Delta_h|\geq\tau$.
- Reversal trades against that sign under the same trigger.
- Favorite rules require $m_t\geq\tau$ and buy the registered YES or NO side.
- Longshot rules require $m_t\leq\tau$ and buy the registered YES or NO side.

Momentum/reversal select the largest absolute eligible move; favorite/longshot
rules select the highest midpoint satisfying the threshold. Ticker ordering
breaks ties. At most one contract is selected per event and policy. Signal
functions reject outcome and future-quote fields.

The entry limit is fixed at the signal-side ask plus scenario slippage, capped
at $0.99. At the exact later entry hour, the later ask plus slippage must still
fit the limit. Exits use the scheduled later bid less slippage and an exit fee,
or the preregistered settlement fallback if there is no valid exit quote.
Only endpoint prices are used; candle highs/lows are not assumed tradable.

Implementation: [trading_research.py](../weatherpred/trading_research.py).

## 11. Preliminary observation bounds and error allowance

Let $H_t$ be a preliminary daily high and $d\in\{0,1,2\}$ a fixed safety margin.
The candidate lower bound on the final high is $H_t-d$. An inclusive bracket
with upper boundary $U<H_t-d$, or a strict-less contract with threshold
$U\leq H_t-d$, cannot win **if the bound holds**.

It sometimes does not hold. Training counts a day as a failure when any station's
preliminary bound exceeds its final exchange value. For $k$ failed days in $n$
observed training days, the one-sided 95% Clopper–Pearson upper failure rate is

$$u=\operatorname{BetaQuantile}(0.95;k+1,n-k),\qquad
\hat p_{NO,\rm lower}=1-u.$$

If every day fails, the lower probability is zero. At least 100 training days
are required. With zero failures, the lower probability simplifies to
$0.05^{1/n}$; for $n=180$ it is approximately 0.983495. Zero observed failures
therefore does not imply certainty.

This interval assumes a binomial sampling model; serial weather dependence and
conditioning on unusually cheap contracts can invalidate interpreting it as a
universal trade-level confidence bound. It is an exploratory conservative
feature. A two-degree preliminary/final discrepancy in validation demonstrates
the practical issue. No margins were changed after viewing that result.

Implementation: [intraday_bounds.py](../weatherpred/intraday_bounds.py).

## 12. Taker depth and maker queue mechanics

For a taker with $R$ contracts remaining, displayed quantity $s_\ell$ at level
$\ell$ and retained-depth fraction $\rho$, the next hypothetical fill is

$$q_\ell=\min\{R,\lfloor100\rho s_\ell\rfloor/100\},$$

provided the worsened purchase price does not exceed the earlier limit.
The simulator walks levels, applies fees to actual partial quantities and
cancels the remainder. It obtains the book only after the registered arrival
delay, rather than selecting a convenient old snapshot.

For a maker, let $A$ be queue volume ahead, $v$ qualifying opposite-side
trade volume strictly through the quote, $R$ remaining quantity and $\eta=0.25$:

$$A'=\max(A-v,0),\quad e=\max(v-A,0),\quad
q_{\rm fill}=\min\{R,\lfloor100\eta e\rfloor/100\}.$$

Only deduplicated, eligible, non-block trades count. Touches and cancellations
do not advance this queue model. It is deliberately conservative, but is still
a model of hypothetical execution rather than actual exchange queue priority.
Post-fill quotes at 0/30/60/300 seconds diagnose adverse selection: the risk
that getting filled is itself associated with a subsequent unfavorable move.

Implementation: [paper.py](../weatherpred/paper.py),
[execution diagnostics](../research/experiments/e009_execution_diagnostics.py).

## 13. Fractional Kelly sizing and cash limits

Spend fraction $f$ of bankroll on a binary contract with total unit cost $c$ and
win probability $p$. On a win, bankroll is multiplied by
$1+f(1-c)/c$; on a loss it is multiplied by $1-f$. Expected log growth is

$$g(f)=p\log\left(1+f\frac{1-c}{c}\right)+(1-p)\log(1-f).$$

Setting its derivative to zero gives the full-Kelly cost allocation

$$f^*=\frac{p-c}{1-c}.$$

The paper experiment uses quarter Kelly after subtracting a fixed three
percentage points from the relevant side's model probability:

$$p'=p-0.03,\qquad f_{\rm paper}=0.25\max\left(0,\frac{p'-c}{1-c}\right).$$

That haircut is a heuristic, not a confidence interval. The dollar budget is
then bounded by available unreserved cash, Kelly allocation, remaining 5%
event exposure and remaining 10% Miami-cluster exposure. The global 25% limit
is looser than the single-cluster limit in this experiment. Whole intended
quantity is $\lfloor\text{budget}/\text{reserved unit cost}\rfloor$; actual partial
fills can have hundredth-contract precision.

For illustration, if $p'=0.65$ and total cost $c=0.55$, quarter Kelly allocates
about 5.56%; the 5% event cap limits a $100 account to $5 before quantity
rounding. This is only useful if the probability estimate is trustworthy.

Cash is never recycled before exit or settlement. Open positions use provisional
liquidating bid marks without exit fees; closed but unfinalized positions are
carried at cost. Consequently reported interim equity and drawdown are limited
measures, not a validated liquidation or ruin-risk distribution.

Implementation: [paper runner](../research/experiments/e009_paper.py),
[paper.py](../weatherpred/paper.py).

## 14. Scores and trading performance

Binary probability forecasts use Brier score and log loss:

$$\operatorname{Brier}=\frac1N\sum_i(\hat p_i-y_i)^2,$$

$$\operatorname{LogLoss}=-\frac1N\sum_i
[y_i\log\hat p_i+(1-y_i)\log(1-\hat p_i)].$$

For continuous distributions, CRPS measures both location and spread:

$$\operatorname{CRPS}(F,y)=E|X-y|-\tfrac12E|X-X'|,$$

where $X,X'$ are independent draws from the forecast distribution. Gaussian
CRPS has a closed form; the empirical version uses sorted samples to avoid
an $n\times n$ pairwise matrix. Coverage measures how often a nominal prediction
interval contains the outcome. Better coverage alone need not improve trading.

Trading research tracks net profit, cash, locked capital and daily log growth
$g_d=\log(B_d/B_{d-1})$. Realized drawdown is

$$D_{\max}=\max_d\left(1-\frac{B_d}{\max_{s\leq d}B_s}\right).$$

E013's $B_d$ carries unresolved positions at cost. Its drawdown is therefore
realized-only and can understate economic drawdown. No Sharpe ratio or target-
hit probability is presented as validated performance.

Implementation: [calibration.py](../weatherpred/calibration.py),
[forecasts.py](../weatherpred/forecasts.py),
[autoresearch runner](../research/experiments/e013_autoresearch.py).

## 15. Chronological selection and multiple experiments

Contracts from the same weather event are dependent. The research aggregates
within events/days and resamples whole day blocks, preserving cross-city and
cross-strategy dependence. E013 uses 10,000 shared circular seven-day bootstrap
samples over all 1,728 policy/scenario combinations.

For candidate $j$, let $\bar g_j$ be mean daily log growth, $\bar g_j^{*(b)}$
its mean in bootstrap sample $b$, and $s_j$ the standard deviation of those
bootstrap means. The centered maximum statistic is

$$M^{*(b)}=\max_j\frac{\bar g_j^{*(b)}-\bar g_j}{s_j}.$$

The observed statistic $T_j=\bar g_j/s_j$ is compared with this maximum
distribution, with finite-sample p-value

$$p_j=\frac{1+\#\{b:M^{*(b)}\geq T_j\}}{B+1}.$$

Zero-variance candidates receive p-value one. This is an approximate
familywise development diagnostic, relying on bootstrap assumptions; it does
not replace final holdout or forward evidence. Earlier experiments separately
use Holm's step-down adjustment for their declared families.

The monthly selector uses only returns released before the month starts.
It ranks candidates by $\bar g-2\,SE$, where $SE$ is estimated from nonoverlapping
seven-day training block sums, requires 30 traded days and positive stressed
training growth, and selects cash if none qualify. The two-SE criterion is a
selection heuristic, not a corrected confidence bound. Evaluating its subsequent
months exposes the cost of choosing strategies from earlier noisy results.

January–June 2025 is training and July–September is repeatedly examined
development validation. October–December remains sealed. The global promotion
protocol additionally requires substantial untouched and prospective samples,
cost/depth stress, parameter robustness and source audits. Tests of code
correctness are not evidence that those statistical gates have been met.

Implementation: [autoresearch runner](../research/experiments/e013_autoresearch.py),
[validation protocol](../config/validation.json).

## 16. Reproducibility and hash-chain evidence

Each response body has digest $D_i=\operatorname{SHA256}(\text{body}_i)$.
The archive record hash commits to its kind, key, actual availability timestamp,
metadata, body digest and previous record hash:

$$H_i=\operatorname{SHA256}(\operatorname{canonicalJSON}
  (\text{kind},\text{key},t_i,\text{metadata},D_i,H_{i-1})).$$

SQLite triggers reject record updates/deletes. Replays check the chain and
reconstruct selected outputs from original sources. This detects accidental
modification; it is not an independently notarized timestamp or protection
against an operator rewriting the whole archive. Published evidence identifies
the snapshot and local source records; the large raw archive is not bundled
in the Git repository.

Implementation: [archive.py](../weatherpred/archive.py),
[raw-quote audit](../research/experiments/e013_audit.py).

## Further reading used in the project

- [Gneiting et al., calibrated probabilistic forecasting](https://sites.stat.washington.edu/people/raftery/Research/PDF/gneiting2005.pdf): distributional calibration.
- [Gneiting and Ranjan, combining predictive distributions](https://arxiv.org/abs/1106.1638): coherent forecast combination.
- [Snowberg and Wolfers, favorite–longshot bias](https://www.nber.org/papers/w15923): motivation for selective price-based hypotheses, not proof of a weather-market effect.
- [Bailey et al., backtest overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659): why trying more configurations demands stronger validation.
- [Kalshi historical candle schema](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks): units and endpoint semantics.
- [NWS observation and climate-product FAQ](https://www.weather.gov/lot/weather_observations_faq): why preliminary and final temperature values can differ.
