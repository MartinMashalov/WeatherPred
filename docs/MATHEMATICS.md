# Mathematics, with implementation links

This guide explains the equations actually used in WeatherPred. Examples are
illustrations, not trading recommendations. Where a model relies on an assumption,
that assumption is part of the explanation. The system has not established a
profitable strategy or a validated probability of reaching a bankroll target.

Start with contracts and fees, then the forecast distributions, then execution
and validation. A probability is a belief about an outcome; an executable price
is what an order can actually pay or receive. Every trade calculation needs both.
The final worked example connects these steps. The [project brief](INTERVIEW_GUIDE.md)
also defines the main modeling and trading terms.

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

Let $T$ denote the temperature before reporting-rounding, with Gaussian distribution
$T\sim\mathcal N(\mu,\sigma^2)$, and let $\Phi$ be the standard-normal cumulative
distribution function: the probability mass below a given standardized value.
Here $\mu$ is the mean and $\sigma$ the standard deviation, which describes spread.
For an inclusive integer bracket $L\leq T_{\rm published}
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

In the original E009 cohort, position cash is not recycled before exit or
settlement. The separate E016 cohort also returns cash through a same-contract
offset, as explained below. Open positions use provisional
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

## 17. Paired maker inventory and payout bounds

The later E015 experiment adds an explicit inventory calculation. If a contract
has YES quantity $q_Y$, NO quantity $q_N$ and total acquisition cost $C$, its
terminal profit under normal binary settlement is

$$\Pi(Y)=q_Y Y+q_N(1-Y)-C.$$

Thus the minimum and maximum payouts on already filled positions are
$\min(q_Y,q_N)$ and $\max(q_Y,q_N)$. Within one account, cash $K$ already includes
acquisition costs. Summing over its contracts $j$, bounds relative to initial
capital $W_0$ are

$$\Pi_{\min}=K+\sum_j\min(q_{Y,j},q_{N,j})-W_0,$$

$$\Pi_{\max}=K+\sum_j\max(q_{Y,j},q_{N,j})-W_0.$$

These bounds exclude future fills from outstanding orders and assume normal
binary settlement. Related contracts may make the individual extremes impossible
to reach together, so the bounds can be loose. They are not an expected return.
E015 holds the matched cash
until settlement as a capital stress assumption; E016 corrects same-contract
cash return below. Averaging each leg's cost allows a decomposition into matched-pair profit
and unmatched cost at risk, but only their combined result is portfolio profit.

For the inventory-skew benchmark, let $I=q_Y-q_N$ and
$d=\operatorname{clip}(0.01I,-0.03,0.03)$. If current own-side bids are $b_Y,b_N$,
candidate buy quotes before valid-tick rounding are

$$p_Y=\min(b_Y+0.01-d,1-b_N-0.01),$$

$$p_N=\min(b_N+0.01+d,1-b_Y-0.01).$$

The quotes must leave at least one cent per matched pair after current maker
fees. A larger YES inventory lowers the YES bid and raises the NO bid, encouraging
rebalancing. The shift is a fixed benchmark inspired by inventory-sensitive
market-making literature, not a fitted optimal-control solution or a fair-value
estimate. E015 keeps one-contract quotes under the same cash/exposure checks.

Implementation: [market_making.py](../weatherpred/market_making.py).

## 18. Same-contract netting and realized round trips

Kalshi offsets opposite positions in the same contract. If both sides have
filled, let $m=\min(q_Y,q_N)$. The cash and quantities become

$$K'=K+m,$$

$$q_Y'=q_Y-m,\qquad q_N'=q_N-m.$$

With each side's total acquisition cost denoted by $C_Y,C_N$, average allocated
cost and realized profit of the matched part are

$$C_m=\frac{m C_Y}{q_Y}+\frac{m C_N}{q_N},$$

$$\Pi_m=m-C_m.$$

This is applied only when both quantities are positive. Matched costs are removed
from the remaining positions. A losing offset produces negative realized profit;
it is not discarded. Unmatched positions still have outcome risk.

For the same fills, netting does not improve the eventual economic result:

$$K+\min(q_Y,q_N)=K'+\min(q_Y',q_N'),$$

and the analogous equality holds for the maximum payout. It moves cash earlier,
which may allow new orders. Those extra opportunities need their own future
execution evidence; an accounting replay cannot fabricate them.

E016 performs a fill and its offset in a single immutable journal update. Its
same-contract rule does not assume optional collateral return across different
contracts. Earlier E015 results remain an overcollateralized stress comparator.

Implementation: [netted_paper.py](../weatherpred/netted_paper.py),
[identical-fill replay and future cohort](../research/experiments/e016_netted_maker.py).

## 19. Worked example: forecast to an order decision

This numerical illustration joins the methods above. It is not an observed
trade or a claim that the assumed forecast probabilities are calibrated.

Suppose an earlier fitted weather model gives a final temperature mean of 78°F
and standard deviation of 2°F. Consider a contract for a reported integer high
of 77–78°F. Under the rounding-cell model,

$$p=\Phi(0.25)-\Phi(-0.75)\approx0.372079.$$

The model assigns about 37.21% probability to YES. An ask of 30¢ is below that
probability, but the gap is not the net edge. For one contract under the
illustrated cent-balance taker schedule, the model fee is
$0.07(0.30)(0.70)=0.0147$ dollars, and the isolated cash debit rounds to 32¢.

| Step | Calculation | Meaning |
|---|---|---|
| Cost | 30¢ ask + 2¢ rounded fee = 32¢ | Cash spent if that isolated fill occurs. |
| Expected settlement profit | 37.21¢ − 32¢ = about 5.21¢ | An average under the assumed model, not a certain payout. |
| Probability haircut | 37.21% − 3 percentage points = 34.21% | The paper rule's fixed allowance for model uncertainty. |
| Adjusted edge | 34.21¢ − 32¢ = about 2.21¢ | Still conditional on the model and the fill. |
| Quarter-Kelly allocation | 0.25 × (0.342079 − 0.32) / (1 − 0.32) ≈ 0.00812 | About 0.81% of bankroll, or 81¢ for a $100 account, before further limits. |

The system then applies available-cash and exposure limits, rounds the intended
quantity and fixes the order limit. A later eligible book must support the
purchase. If the ask moves above the limit or there is no eligible depth, the
unfilled quantity is cancelled. Multiple partial fills use the fee accumulator,
so their fees are not computed by simply multiplying this isolated example.

Even if the one-contract purchase fills at 30¢ with a 2¢ fee, its realized
settlement result is either **+68¢ or −32¢**. The expected 5.21¢ is neither of
those outcomes. Validation asks whether many independent future decisions
support the probability and execution assumptions behind that expectation.

For a maker, there is another distinction. Buying one YES for 42¢ and one NO for
55¢ would lock in 3¢ before any applicable fees **if both fill**. Receiving only
the YES fill leaves 42¢ at risk. The paired-maker experiments track that unmatched
risk and the queue evidence required for each side separately.

## 20. Conditional hourly regression and Student t errors

E018 predicts the remaining temperature change rather than assuming all hours
share one residual distribution. For the known target's Miami local hour $h$,
define $\theta=2\pi h/24$. Features contain the trend forecast minus persistence,
plus $\sin(k\theta),\cos(k\theta)$ for $k=1$ or $k=1,2$. The second pair permits a
more flexible daily shape. Target time is known at the decision and is not a
future observation.

Let $r_i=T_i-f_{{\rm persist},i}$. Training-only means and standard deviations
standardize each feature into a row of $Z$. Weights in diagonal matrix $W$ give
each training day equal total influence and are normalized to sum to the number
of rows. With weighted mean residual $\bar r$, the ridge solution is

$$A=Z^T WZ+\lambda I,$$

$$b=Z^T W(r-\bar r\mathbf1),\qquad\hat\beta=A^{-1}b.$$

The forecast mean is

$$\mu=f_{\rm persist}+\bar r+z^T\hat\beta.$$

The intercept is unpenalized. The registered penalties are $\lambda\in\{1,10,100\}$;
larger penalties shrink the fitted trend and daily-pattern effects more strongly.
Parameters are fitted separately for 30-, 15- and 5-minute horizons.

The error distribution is either Gaussian or a Student t with fixed five degrees
of freedom. For the latter,

$$T=\mu+sU,\qquad U\sim t_5,$$

$$P(T>k)=\operatorname{SF}_{t_5}\!\left(\frac{b_k-\mu}{s}\right),$$

where $b_k$ is the earlier 0.01°F rounding-cell boundary and SF is the survival
function, the probability above a threshold. The t distribution has heavier
tails. Its scale $s$ is not its standard deviation: the latter is
$s\sqrt{5/3}$. The scale is chosen by minimizing negative log density, with a
0.05°F floor and a 20°F upper search bound for the t case.

There are twelve candidates. Expanding August fits predict the following two
days; the candidate with the lowest equal-day average negative log density is
selected. This is a probability-density score, distinct from the binary log
loss of a YES contract. The mean is then refitted on all twelve August days,
and its scale is calibrated using the selected candidate's earlier-fold errors.
Those same eight days were used for selection, so their apparent improvement is
not an unbiased estimate of future performance. The frozen forward cohort uses
new receipts, orders and outcomes to test the result.

Implementation: [conditional_forecasts.py](../weatherpred/conditional_forecasts.py),
[forward runner](../research/experiments/e018_conditional_hourly.py),
[model replay](../research/experiments/e018_diagnostics.py).

## 21. Conditional rain-calendar pairs and unequal fills

Let $A$ indicate Saturday rain and $B$ indicate Sunday rain, each taking value
zero or one. Under identical normal source and reporting conventions, the
weekend indicator is

$$W=\max(A,B)=A+B-AB.$$

If Saturday has officially finalized dry and the source still confirms that
value, $A=0$ implies $W=B$. One Sunday YES plus one weekend NO then pays

$$B+(1-W)=1.$$

Let the executable costs including fees be $c_D$ and $c_W$. A fully matched
one-contract pair has conditional settlement profit

$$\pi=1-c_D-c_W.$$

At the exploratory 16:30 UTC snapshot, NYC's asks were $0.10$ for daily YES and
$0.81$ for weekend NO. The fee accumulator produces a total cost of $0.9271$,
leaving $0.0729$ conditional surplus. With quarter depth and two cents extra
slippage per leg, total cost is $0.9673$ and the surplus is $0.0327$. Neither
calculation proves that later orders can fill at those prices.

If the two actual fills are unequal, let $q_D,q_W$ be their quantities and $C$
their combined cash cost, including fees. Then

$$\Pi(B)=q_DB+q_W(1-B)-C,$$

$$\min_B\Pi=\min(q_D,q_W)-C,\qquad
\max_B\Pi=\max(q_D,q_W)-C.$$

For example, if only 0.50 daily YES fills at 11 cents and the weekend leg fails,
its actual fee-inclusive cash cost is $0.0585$. Possible profit is between
−$0.0585 and +$0.4415, despite a positive matched-pair price screen. The missing
leg is never inserted retrospectively. Different contracts also do not release
matched cash through the same-contract netting mechanism.

Source consistency is another condition. If the relation breaks and both held
sides lose, the payoff is zero and profit is $-C$. For a hypothetical complete
one-contract pair with normal payout one and failure payout zero, an assumed
failure probability $r$ would give

$$E[\Pi]=1-r-C.$$

The project has not estimated $r$ reliably. Two observed weekends cannot establish
a rare-failure rate, and exchange review can affect settlement. E019 therefore
reports the source-break loss separately. Its fixed one-cent source allowance
is a stress deduction, not a measured probability or confidence bound. It requires
two cents additional conditional surplus after that deduction, reserves at most
5% of equity per pair and retains the existing aggregate exposure caps.

Implementation: [rain_relations.py](../weatherpred/rain_relations.py),
[prospective pair runner](../research/experiments/e019_rain_pairs.py).

## 22. Position capacity, early exit and geometric growth

E020 measures the cost of increasing a matched rain-pair position. Let $C(q)$
include both legs' ask depth, slippage and accumulated entry fees for quantity
$q$. When both legs have enough displayed size, conditional surplus is

$$S(q)=q-C(q).$$

The diagnostic chooses the largest surplus among quantities 1–100 subject to
$C(q)\le K$ and $S(q)/q\ge0.03$, where $K$ is an illustrative cash-cost cap.
Costs need not increase linearly: the next contracts may be offered at worse
prices. This calculation is a quote screen, not a fill or a recommendation to
increase E019's registered limits. Under quarter depth and two cents additional
slippage per leg, the September 6 snapshot supports six pairs for $5.8108,
leaving $0.1892 conditional surplus. A larger cap does not improve that row.

Selling early requires a separate calculation. If eligible bid slices for the
held positions have prices $b_j$ and quantities $u_j$, then

$$V_{\rm exit}=\sum_j b_ju_j-F_{\rm sell},\qquad
\Pi_{\rm exit}=V_{\rm exit}-C_{\rm entry}.$$

The implementation also applies the exit slippage and depth scenario. It reports
profit only for a fully quoted exit; quantities without bids remain unfilled.
At the observed full-depth bids, the fastest E019 account could receive $2.4384
after exit fees against its $4.5887 cost: a $2.1503 loss if those sales execute.
Its conditional $5 normal settlement payout cannot be used as immediate cash.

Now let $B$ be initial bankroll, with $0<C<q$ and $C<B$. Suppose the pair pays
$q$ normally and zero on source failure. For an assumed failure probability $r$,

$$E[\Pi]=(1-r)q-C,$$

$$g(r)=(1-r)\log\left(\frac{B+q-C}{B}\right)
+r\log\left(\frac{B-C}{B}\right).$$

The second expression is expected logarithmic growth. It penalizes losses more
strongly as they consume the bankroll. Define $a=\log((B+q-C)/B)$ and
$b=\log((B-C)/B)$. The break-even failure assumptions are

$$r_{\rm arithmetic}=1-C/q,\qquad r_{\rm geometric}=\frac{a}{a-b}.$$

They are sensitivity thresholds, not estimates of the actual failure frequency.
For example, $B=100,q=100,C=90,r=0.08$ gives expected profit of $2$, but negative
expected log growth. Positive average dollars alone can conceal poor compounding.
No source-failure estimate or reliable bankroll-target probability is available
from the single current weekend.

Implementation: [capital and exit functions](../weatherpred/execution_finance.py),
[execution study](../research/EXECUTION_FINANCE.md).

## 23. Transformer quantiles and supervised adaptation

E021 uses a pretrained Chronos-2-small transformer through its official library.
The project implements data alignment, adaptation and evaluation; it does not
claim to have invented or reimplemented the pretrained architecture. A sequence
of 512 minute values produces forecasts for the next 40 minutes. Missing values
remain masked. The model's median forecast is used for the temperature-error
comparison, while its quantiles describe possible outcomes.

A quantile $Q_\tau$ is a value below which the model assigns probability $\tau$.
For error $u=y-Q_\tau$, the pinball loss is

$$\rho_\tau(u)=\max(\tau u,(\tau-1)u).$$

Underprediction receives weight $\tau$, and overprediction receives weight
$1-\tau$. At $\tau=0.5$, this equals half the absolute error. For $n$ forecasts
and $m$ quantile levels, the reported score in Fahrenheit is

$$L_{\rm report}=\frac{1}{nm}\sum_{i=1}^{n}\sum_{j=1}^{m}
\rho_{\tau_j}(y_i-Q_{i,\tau_j}).$$

The library's training loss uses its internally normalized targets, twice the
pinball loss, masks missing targets and known future covariates, averages over
the output horizon, sums across quantile levels and averages over the batch.
Consequently, its training-log loss is not numerically interchangeable with
the evaluation score above. E021 trains all model parameters for 100 AdamW
optimizer steps with batch size eight and initial learning rate $10^{-5}$,
which follows the library's linear schedule. There is no validation-based
checkpoint selection. The short pilot is supervised learning, not RL.

The point-forecast comparisons are

$$\operatorname{RMSE}=\sqrt{\frac1n\sum_i(\hat y_i-y_i)^2},\qquad
\operatorname{MAE}=\frac1n\sum_i|\hat y_i-y_i|.$$

Across the 93 reused development forecasts, fine-tuned RMSE is 0.8010°F versus
0.7504°F for persistence. At the five-minute horizon it is 0.4512°F versus
0.5375°F. These 31 hourly targets span eight days and overlap the earlier model
comparison. Neither the lower subgroup error nor an unadjusted resampling
interval establishes independent skill after selecting among models/horizons.

Dropout randomly masks model activations during training. The initial evaluation
accidentally retained that mode and failed saved-checkpoint replay. Explicit
evaluation mode removes that randomness. The corrected scores use the same
saved weights and reproduce exactly; no training retry occurred.

For a binary event with true probability $q$, expected Brier loss satisfies

$$E[(p-Y)^2]=q(1-p)^2+(1-q)p^2,\qquad
\frac{\partial}{\partial p}E[(p-Y)^2]=2(p-q).$$

This explains why direct supervised probability learning already has a useful
optimization target. RL can instead address sequential actions such as posting,
cancelling or reducing an order. A proposed execution objective is expected
change in log wealth after fees, fills and final inventory outcomes. It remains
unimplemented and would require a reliable simulator and new validation days.

Implementation and sources: [model study](../research/model_candidates.md),
[probe](../research/probes/forecast_model_smoke.py).

## 24. Consecutive-day states and contract implications

The weekly heat contracts depend on consecutive hot days, not the mean of the
whole week. For day $d$, let $n_d$ be its number of eligible hourly observations
and $T_{dh}$ their temperatures. Under the exact contract rounding rule, define

$$q_d=\mathbf1\left\{n_d\ge18,\quad
\operatorname{round}\left(\frac{1}{n_d}\sum_hT_{dh}\right)>90\right\}.$$

The streak ending on a day follows $s_d=q_d(s_{d-1}+1)$ with initial $s_0=0$.
The longest streak is $L=\max_d s_d$, so a contract for at least $k$ consecutive
days pays $\mathbf1\{L\ge k\}$. Enumerating unresolved days as both zero and one
bounds the possible final result. Earlier source revisions can still change
the completed-day inputs; observed data is not an unconditional payout guarantee.

Similarly, a monthly rainfall total is $R=A_t+R_{\rm remaining}$, where $A_t$
is accumulated reported rain. When additional amounts are nonnegative and past
reports remain valid, $A_t>k$ implies a strictly-greater-than-$k$ threshold has
already been exceeded. Equality alone does not suffice.

More generally, if event $A$ implies event $B$ under compatible source rules,

$$\mathbf1_{\neg A}+\mathbf1_B\ge1.$$

Examples include a higher rainfall threshold implying a lower one, or major
hurricanes being included in a compatible hurricane count. Buying NO(A) and
YES(B) has a conditional minimum payout of one dollar per matched pair.
Profit still requires both fills and total cost below that payout. The new
541-relation screen finds no positive quoted floor after fees; theoretical
relationships do not guarantee a discounted purchase.

Implementation and precise source gates:
[expanded market study](../research/MARKET_EXPANSION.md).

## Further reading used in the project

- [Gneiting et al., calibrated probabilistic forecasting](https://sites.stat.washington.edu/people/raftery/Research/PDF/gneiting2005.pdf): distributional calibration.
- [Gneiting and Ranjan, combining predictive distributions](https://arxiv.org/abs/1106.1638): coherent forecast combination.
- [Snowberg and Wolfers, favorite–longshot bias](https://www.nber.org/papers/w15923): motivation for selective price-based hypotheses, not proof of a weather-market effect.
- [Bailey et al., backtest overfitting](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2308659): why trying more configurations demands stronger validation.
- [Kalshi historical candle schema](https://docs.kalshi.com/api-reference/historical/get-historical-market-candlesticks): units and endpoint semantics.
- [NWS observation and climate-product FAQ](https://www.weather.gov/lot/weather_observations_faq): why preliminary and final temperature values can differ.
- [Avellaneda and Stoikov, market making](https://math.nyu.edu/inmemoriam/avellaneda/HighFrequencyTrading.pdf): inventory-sensitive quotes and the distinction between subjective valuation and execution prices.
- [Kalshi netting](https://news.kalshi.com/p/collateral-return) and [current settlement documentation](https://docs.kalshi.com/getting_started/market_settlement): offsetting opposite positions and settling only remaining net positions.
