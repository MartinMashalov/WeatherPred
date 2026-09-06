# Miami hourly contract alignment audit

**Finding: the four paper events target the index minute at market close. There is no one-hour target shift in these events or the audited August training data.** Final cash settlement occurs about 70.6–70.8 minutes later because the exchange first determines the result and then runs its 3,600-second settlement timer. The final prices exactly match canonical index observations received about five to six minutes after close.

This is a diagnostic following losses, not a new strategy experiment. It changes no frozen model, job, probability, order, or outcome. The machine-readable evidence is [HOURLY_ALIGNMENT_AUDIT.json](../evidence/HOURLY_ALIGNMENT_AUDIT.json); reproduce it with:

```sh
uv run python research/probes/hourly_alignment_audit.py
```

## What the contract actually measures

The precise series is **KXTEMPMIAH**. Its metadata names Synoptic Data and the Kalshi Weather Index methodology. The linked TEMPH PDF identifies the formal rulebook as MIAWINDEX: this is an **“at” contract for one minute**, rather than an average over the following hour. The index administrator is Kalshi; Synoptic supplies the principal station observations. The final value uses the latest eligible canonical observation at or before the specified minute, with a maximum age of 60 minutes, after the five-minute publication deadline. Initial canonical values govern; later restatements are not ordinary replacements. See the [contract terms, Appendix A and Appendix B sections 3, 7–8](https://assets.kalshi.com/contract_terms/TEMPH.pdf).

Archived primary evidence: series records **243/9283**; formal PDF **1900**, received `2026-09-06T10:22:38.905916Z`, SHA256 `83ccfd50b032e9b1f350890decd9e16917b6aa0f35670f5a2b75547156837025`. The PDF’s payout paragraph on page 2 was also visually checked. Live market rules in every final event agree with this source identity and target clock time.

The API distinguishes result determination from completed payment. A `determined` market has an outcome while its settlement timer runs; `finalized` means settlement completed. The four source responses explicitly specify `settlement_timer_seconds=3600`. `expected_expiration_time` is close plus five minutes, not the final payout time. This explains why waiting only for `finalized` makes a known result appear to arrive an hour late. [Official market lifecycle documentation](https://docs.kalshi.com/getting_started/market_lifecycle).

## Four events: actual observed sequence

All dates below are September 6, 2026; times are UTC. “First receipt” means the first matching response in this archive, not proof of the first public publication. The full artifact retains microsecond timestamps, all state transitions, the last observed pending index versions, underlying station values, and source hashes.

| Event suffix / local target | Close and index event minute | Final index °F | First canonical receipt (record) | First `determined` receipt (record) | Exchange cash settlement timestamp | First `finalized` receipt (record) |
| --- | --- | ---: | --- | --- | --- | --- |
| `26SEP0610` / 10am EDT | 14:00 | 84.56 | 14:05:35.477 (32602) | 14:10:47.709 (33130) | 15:10:35.479 | 15:11:11.719 (43062) |
| `26SEP0611` / 11am EDT | 15:00 | 87.80 | 15:05:29.179 (42447) | 15:11:11.853 (43064) | 16:10:45.479 | 16:11:13.731 (60077) |
| `26SEP0612` / noon EDT | 16:00 | 89.60 | 16:05:36.055 (58465) | 16:11:13.872 (60079) | 17:10:45.485 | 17:10:56.369 (78720) |
| `26SEP0613` / 1pm EDT | 17:00 | 91.40 | 17:06:11.458 (77179) | 17:10:56.497 (78723) | 18:10:45.481 | 18:10:56.245 (99920) |

E018’s later final-source record **100119**, received `18:11:30.596884Z`, has exactly the same body hash as **99920**: `4f6c5489831a3646985fa6a0e8461c28415aab502f258c3ad63a3ef475bb641a`. It is another receipt of the same finalized event, not a new weather outcome.

Each first canonical target was `normal`, with five exact primary station observations. Their equal-weight temperature average reproduces the final number. Every final contract’s strict greater-than predicate agrees. The captured index did not wait for weather over 14–15, 15–16, 16–17, or 17–18 UTC: it already contained the final number for the close-time minute long before those intervals ended. The 12/12/12/8 retained canonical target receipts show no numeric changes.

The exchange’s update timestamp in each first determined response is around `:10:35–:10:41`. Final payout is approximately one hour after that. The additional roughly five minutes between canonical publication eligibility (`S+5m`) and the exchange’s determination is observed operational delay; this audit cannot establish its internal cause. Collector polling and request latency also affect first-receipt times.

## The Weather Company comparison is a different target

The archived [TWC weekly station feed](https://weather.com/kalshi/api/metar?primary=true&weekStart=2026-08-31), record **78077**, was fetched at `17:09:06.394Z` and received at `17:09:19.493299Z`. For **KMIA alone**, its September 6 rows at 10/11/noon/1pm local time contain **84.9, 88.0, 89.1, and 90.0°F**. The first three are marked `settled`; the 1pm row was still `pending` in that receipt. Its UTC and local-hour fields align with 14/15/16/17 UTC.

These observations neither equal nor replace the five-station Miami index. The event rules do not designate this feed as authoritative. The weekly 37-station data acquisition can support a separate station forecasting study, but cannot silently supply KXTEMPMIAH settlement labels. The differing temperatures do not demonstrate a clock error. This audit makes no claim about how another TWC-based hourly contract aggregates its observations.

## Historical and live model mapping

The independent probe reads the frozen E006 dataset **12322**, model **12323**, its original history sources **3554–3566**, and E004 model **3768** with market pages **3573/3576/3578/3581**. It restricts training checks to August 20–31. No September examples are fitted or scored as a new validation experiment; no sealed 2025 outcome data are read.

- **864 training rows / 288 hourly targets:** every label has `label_point_ms == settlement_ms`. The recorded target equals decision time plus the declared 30/15/5-minute horizon. Raw index values, feature cutoff, persistence, and independent trend arithmetic all reproduce; maximum trend difference **0.0°F**.
- **2,770 August contracts / 277 listed hourly events:** each expiration value equals the raw index selected at its close. The training grid includes hours without a listed event; the listed-market reconciliation therefore covers 277 of its 288 targets.
- **3,456 archived E004 residual values:** original ten-minute-lag persistence and trend inputs independently reproduce every stored Gaussian/empirical residual, with maximum difference **0.0°F**. This checks the existing fit inputs without fitting another model.
- **27 forward snapshots:** 12 original E004, 12 fresh-input paper, and three E018 predictions use their actual archived canonical input and an earlier raw receipt than forecast publication. Target time agrees with the event close throughout.

There is a smaller, concrete freshness difference. In August, **863 of 864** five-minute-lag rows used an input exactly five minutes before decision; one used six minutes. The fresh forward inputs were all **six minutes** before their scheduled decisions. Their last-input-to-target spans are therefore **36/21/11 minutes**, versus the usual historical **35/20/10**. Original E004’s ten-minute lag produced **40/25/15-minute** spans. A nominal horizon describes decision-to-target time; the model also has to cover the age of its latest input. The trend implementation already extrapolates over that full elapsed span, but its residual distributions were mostly learned with slightly fresher inputs.

For E018’s one forward hour, the exact diagnostics are:

| Decision UTC (forecast record) | Last canonical input | Persistence °F | Trend °F | Selected conditional mean °F | Final target °F | Mean minus target °F |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 16:30 (65689) | 16:24 | 89.96 | 90.1325 | 90.0883 | 91.40 | −1.3117 |
| 16:45 (70709) | 16:39 | 90.32 | 90.9815 | 90.5165 | 91.40 | −0.8835 |
| 16:55 (73799) | 16:49 | 90.32 | 90.6090 | 90.4228 | 91.40 | −0.9772 |

These are three dependent forecasts of one outcome. They establish underprediction for that hour, not why it occurred. Neither the one-minute freshness mismatch nor the conditional model’s shrinkage can be declared the binding cause from this sample. The result does not justify changing the frozen model after seeing its loss.

## Daylight saving and revisions

The audit uses `America/New_York` for UTC-to-local conversion. All tested dates are EDT (`UTC−04:00`); ticker local hour, event `strike_date`, market `close_time`, and TWC local-hour fields agree. No DST transition occurs in August 20–31 or September 6. The formal terms specify the earlier occurrence of an ambiguous fall-back local time unless issuance states otherwise, and prohibit nonexistent spring-forward times. Actual transition-day contracts remain untested. [Contract terms, Appendix A](https://assets.kalshi.com/contract_terms/TEMPH.pdf).

Across **858 raw capture records / 440 distinct canonical event minutes**, the audit finds **zero different numeric canonical versions**. That rules out a captured numeric revision as the explanation for these four mismatches. It cannot establish the first-publication provenance of August history retrieved in September. That historical limitation remains; a later historical value matching the contract does not by itself prove that every pre-decision historical feature edition was available at the assumed time.

## Consequences and evidence

The hour-shift hypothesis is rejected for the checked data. Keep distinct fields for the target minute, canonical-publication receipt, exchange determination receipt, and finalized cash release. A result may be scored provisionally when known, but an account must not spend settlement cash before the registered release rule permits it. Retain the existing paper losses and their actual final-source receipts.

For a separately registered future model study, receipt-based input-age matching is a defensible comparison. The present evidence does not authorize retroactively shifting labels, discarding losing hours, retuning this cohort, or calling the forecasts profitable. It also does not establish that all historical canonical revisions are recoverable.

Actual probe output on this audit:

```text
events_reconciled: 4
rows_reproduced: 864
original_e004_residuals_reproduced: 3456
listed_august_contracts_matched: 2770
forward_forecasts_reconciled: 27
canonical_numeric_revision_count: 0
source_records_verified: 1541
archive_writes: 0
models_fitted: 0
```

The probe is standard-library-only and imports neither production forecast code nor execution code. Its archive ceiling is fixed at record **100119**. Lint passed. The JSON includes the probe’s exact SHA256 and every consumed JSON source’s hash. The formal PDF and official lifecycle document above provide the separate primary rules interpretation.
