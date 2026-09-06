# Research objective and evidence ledger

The objective is to discover an executable weather-market edge and quantify its
uncertainty and risks. Neither software tests nor a profitable exploratory backtest
establish completion. See the [strategy guide](STRATEGIES.md) and
[validation protocol](../config/validation.json). Status: research in progress.

| Phase | Required evidence | Current state |
|---|---|---|
| 1 Market mechanics | Complete discovered universe, versioned exact rules/sources, station/period/rounding, fees, opening/settlement, depth and reaction study | In progress |
| 2 Point-in-time data | Archived raw data, publication/revision timestamps, as-of joins, leakage tests; model/observation/market lineage | Prospective receipts logged; 819 original NBM cycles and NWS source versions reconstructed; historical public-access/observation-receipt evidence incomplete |
| 3 Baselines | Market, NBM, individual models, model ensemble, local bias, calibration, intraday update on identical chronological samples | Miami baselines and both old/updated daily NBM variants trail market; one conditional pool's probability gain failed its cost screen. Observation-conditioned nowcasting and further independent sources pending |
| 4 Distributions | Coherent bracket probabilities, conditional calibration, justified distributional alternatives | Coherent daily Gaussian/empirical distributions and mean/spread regression tested; better interval coverage does not beat market scores |
| 5 Structural search | Local bias, nowcasting, both latency types, dispersion, tails, bracket/cross-market consistency, temporal/maker/regime tests | E013/E014 ran 1,836 trading-policy/cost comparisons. None qualifies. E015/E016 now test paired makers and netting; E018 selects 12 conditional hourly models on August data and registers new forward paper execution. Observation-reaction and broader relative value remain open |
| 6 Net edge | Executable price + all costs + calibrated uncertainty with positive lower bound | Pending |
| 7 Simulation | Depth, partial/maker/taker fills, latency, cancels/staleness, capital lockup and correlated positions; three scenarios | E009 prospective paper broker running with depth/latency/partial fills, queue/tape maker rules and cash/settlement accounting; actual fill-model calibration and sufficient outcomes pending |
| 8 Validation | Walk-forward and sealed final holdout; complete prediction/trading metrics, grouped confidence intervals | Protocol registered; evidence pending |
| 9 Adversarial audit | Timestamp/source/DST/model/revision/survivorship audit, placebos, selection correction | Leakage/replay/source checks; 188 missing winner strikes recovered; broader attacks pending |
| 10 Capital | Log-growth sizing comparisons; validated ruin/threshold/time distributions, dependence and capacity | Pending |
| 11 Literature | Primary-source methods implemented/tested, findings and decisions in research.md | In progress |
| 12 System | Reproducible ingestion/features/model/edge/sizing/execution/monitoring/update pipeline, database, logs, config, tests, dashboard, kill switches | In progress |
| 13 Forward | Immutable before-outcome decisions and hypothetical fills; settled shadow outcomes and promotion gates | E009 audit at 16:12 UTC reproduces 195 taker and 57 maker fills, 98 settled positions across two hourly events, and independent fee/cash arithmetic. Cumulative realized results are negative in 31 of 32 alternatives; the other is +$0.6371 with further open risk. This is one dependent day. E018's new model/cohort is frozen before 16:30; sufficient forward days and promotion evidence remain pending |
| Outcome A | All profitability gates satisfied, quantified edge/risk/capacity and production operations | Unproven |
| Outcome B | Exhaustive mechanism search rejected with robust execution-adjusted evidence | Unproven |

Exploratory data is never renamed a holdout. Downloading data after the fact does
not establish its historical availability. Inspection of final results consumes
the holdout. A failure to obtain data is not evidence that weather has no edge.
