# Physical guidance is a stronger benchmark than regression

NOAA's original hourly NBH guidance has mean absolute error **1.8345°F** on
the same development cases where adapted Chronos reaches **1.8872°F** and
ridge-100 reaches **2.1501°F**. The 12.23% neural improvement over regression
remains valid, but it does not establish superiority to operational weather
guidance. The difference between NBH and either neural candidate is small
relative to its descriptive uncertainty interval.

E026 registration **117527** precedes all NBH error scores. Acquisition v2
**113137** finishes all **498** original forecast files with all **9,870**
requested cases available and no missing forecasts. Source binding **129709**
pins every acquisition object before evaluation; report **129711** retains
all nine candidates. The original acquisition failure is also preserved.

The panel comprises 3,271 earlier calibration cases on July 6–19 and 6,599
development forecasts on July 20–August 16: 20 stations, three horizons and
28 development UTC days. Multiple stations on one day are dependent evidence.
No model is fitted or rerun in E026; it uses the eight saved E022 predictions.

| Forecast | Mean absolute error, °F | Root mean squared error, °F | Calibrated quantile loss, °F | 90% interval coverage |
| --- | ---: | ---: | ---: | ---: |
| Persistence | 5.1183 | 6.8903 | 1.4852 | 89.21% |
| Previous day | 3.1517 | 4.5435 | 1.0056 | 90.52% |
| Equal persistence/previous-day blend | 3.2853 | 4.4153 | 0.9826 | 89.81% |
| Ridge, penalty 1 | 2.1518 | 3.0286 | 0.6507 | 91.25% |
| Ridge, penalty 10 | 2.1503 | 3.0277 | 0.6510 | 91.18% |
| Ridge, penalty 100 | 2.1501 | 3.0350 | 0.6558 | 91.38% |
| Pretrained Chronos | 1.9072 | 2.8108 | 0.5855 | 91.09% |
| Fixed adapted Chronos | 1.8872 | 2.8089 | 0.5723 | 91.66% |
| Original NBH | **1.8345** | **2.5317** | **0.5695** | 92.42% |

Lower error and quantile loss are better. Coverage should be assessed together
with interval width, rather than simply maximized. NBH's 90% interval width is
8.4666°F, adapted Chronos 8.2814°F and pretrained Chronos 8.6547°F. All metrics
first average cases within each UTC target day, then weight the 28 days equally.
Point errors use raw forecasts; distribution metrics use earlier calibration.

## Source and timing checks

Each NBH run is fixed at two hours before the original decision. Its forecast
leads of 3, 5 and 8 hours map to decision horizons of 1, 3 and 6 hours. The
source validator checks station, run, target, original card bytes, range
responses, object versions and storage timestamps. It verifies **6,375**
original response records. No alternative station, run or interpolation is
selected when information is inconvenient.

The physical point is NBH's temperature field in Fahrenheit. It is repeated
across the original thirteen quantile levels before the same per-horizon
residual calibration used by the other forecasts. NBH's spread field is not
treated as a justified Gaussian distribution.

Actual downloads happened in September. Original object storage timestamps
precede the hypothetical decisions, but do not prove that these precise
versions were publicly received then. The historical availability assumption
remains explicit. This is a station-temperature comparison, not the five-station
Miami settlement index, a daily high or a forecast of trading profits.

## Uncertainty and interpretation

All eight NBH-minus-existing-model comparisons share the same 10,000 circular
seven-day bootstrap samples, seed 6202601. The maximum absolute standardized
statistic supplies simultaneous descriptive 95% intervals. For ridge-100 the
mean difference is −0.3156°F with interval **[−0.4581, −0.1731]°F**. For
pretrained Chronos it is −0.0727°F with **[−0.2540, +0.1085]°F**; for adapted
Chronos it is −0.0527°F with **[−0.2394, +0.1340]°F**. Both neural intervals
include zero. These are development diagnostics after E022 was already known.

The separate E022 arithmetic auditor reconstructs all nine candidates'
calibration and scores within **1.42e−14**. All eight unchanged full-panel
scores reproduce their original report. The further E026 audit **139319**
independently reconstructs all eight bootstrap comparisons, including exact
random-draw and resampled-mean hashes, and rechecks all 6,375 source receipts.
It shares the disclosed raw-card validator while using separate score and
bootstrap arithmetic. It does not claim independent model execution or
untouched validation. [Audit evidence](../evidence/E026_result_audit.json).

The next fixed experiment is E029: three physical/transformer combinations
registered as a design **before this result**. It will report every weight and
all twelve paired comparisons without choosing a favorable station, horizon
or period. A combined forecast is a hypothesis, not an assumed improvement.

[All nine results and intervals](../evidence/E026_physical_comparison.json),
[completed acquisition](../evidence/E025_v2_acquisition_complete.json),
[retained original acquisition failure](../evidence/E025_v1_acquisition_failure.json),
[physical source design and primary references](PHYSICAL_STATION_BASELINE.md),
[fixed combination design](../evidence/E029_design.json).
