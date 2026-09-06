Compare the frozen E022 forecasts with original NOAA NBM hourly station bulletins first. This is a proposal for a new registered development comparison, prepared on September 6, 2026 after E022's aggregate and subgroup results were known. It is not an untouched test, a new model result, or evidence of trading profit. This investigation read documentation, station-directory metadata, existing source code and case timestamps; it did not download benchmark forecast bodies, train models, run inference, or compute new forecast errors.

Use **NBH**, the hourly NBM bulletin. NOAA documents NBH at one-hour intervals through forecast hour 25, whereas NBS is three-hourly, with its first forecast hour varying by cycle. The E022 targets are on a three-hour lattice, but that does not ensure a sufficiently recent NBS cycle contains every near-term target. NBH covers the proposed forecast leads 3, 5 and 8 directly. Its temperature field is `TMP` in Fahrenheit; `TSD` is a standard deviation, not an empirical quantile distribution. Current documentation specifies `-99` as missing. An entirely missing element can have no printed line. [NOAA NBM v5.0 station-card specification](https://vlab.noaa.gov/web/mdl/nbm-textcard-v5.0)

NBM is a calibrated blend of numerical weather models and postprocessed guidance. It already combines physical modeling and statistical correction. Its text products sample the closest usable grid point to each station, so station identity and valid time can match E022 while terrain, coastal exposure and measurement representation still differ. Report that limitation, including integer-Fahrenheit text precision. [NOAA NBM description](https://registry.opendata.aws/noaa-nbm/), [NOAA text-product description](https://vlab.noaa.gov/web/mdl/nbm-text-products)

The comparison must retain E022 registration **105314**, its existing case IDs and the following calendar:

| Item | Fixed definition |
|---|---|
| Calibration | July 6–19, 2026; 3,271 eligible cases |
| Development comparison | July 20–August 16, 2026; 6,599 eligible cases across 28 UTC days |
| Target | The existing TWC `temperature_f` record at exactly `(station_id, target_ms)` |
| Target hours | 00, 06, 12 and 18 UTC |
| Decision | `D = target − h`, for `h = 1, 3, 6` hours |
| Station-history assumption | Publication at observation time plus 15 minutes; actual September receipts stay separate |
| Transformer context | Original 168-hour grid, at least 120 finite values; latest input at most 120 minutes old |
| Frozen case artifact | `reports/E022_manifest.json`, SHA256 `ccbcbb142cbb7d6954d3ea0d89000427003b27834f9b9f8bb28477d46339b741` |

The midnight targets on the first day of each split fail E022's existing decision cutoff; do not restore them for this comparison. The first eligible target in each split is 06 UTC. Keep all original calendar exclusions, including stations without sufficient history. Do not select stations using E022 errors. This station target is also distinct from the five-airport Miami hourly market index studied in E004/E018. Matching the recorded hourly station target does not prove equivalence to an exchange settlement formula or to a continuous hourly temperature average.

All 20 eligible E022 stations appear in the current NOAA v5.0 directory. The table below freezes exact IDs and NOAA coordinates, useful for a later grid extraction. Current directory membership does not establish that every historical bulletin contains a usable temperature for every station. Every missing card remains an explicit failure; do not substitute airports.

| Station | Latitude | Longitude | Calibration cases | Development cases |
|---|---:|---:|---:|---:|
| KATL | 33.6297 | -84.4422 | 165 | 330 |
| KAUS | 30.1831 | -97.6799 | 158 | 330 |
| KBOS | 42.3606 | -71.0097 | 154 | 324 |
| KDCA | 38.8472 | -77.0346 | 165 | 330 |
| KDEN | 39.8466 | -104.6562 | 155 | 330 |
| KDFW | 32.8975 | -97.0220 | 165 | 330 |
| KIAH | 29.9844 | -95.3608 | 165 | 330 |
| KLAS | 36.0719 | -115.1634 | 164 | 330 |
| KLAX | 33.9382 | -118.3866 | 165 | 330 |
| KMIA | 25.7881 | -80.3169 | 165 | 330 |
| KMSP | 44.8853 | -93.2313 | 165 | 333 |
| KMSY | 29.9975 | -90.2777 | 165 | 330 |
| KNYC | 40.7795 | -73.9691 | 165 | 333 |
| KOKC | 35.3884 | -97.6004 | 165 | 330 |
| KORD | 41.9602 | -87.9316 | 165 | 330 |
| KPHL | 39.8605 | -75.2708 | 165 | 330 |
| KPHX | 33.4278 | -112.0037 | 165 | 330 |
| KSAT | 29.5443 | -98.4840 | 165 | 333 |
| KSEA | 47.4447 | -122.3144 | 165 | 326 |
| KSFO | 37.6196 | -122.3656 | 165 | 330 |

Directory evidence: [NOAA v5.0 station page](https://vlab.noaa.gov/web/mdl/nbm-stations-v5.0) links this [station CSV](https://vlab.noaa.gov/documents/6609493/0/blend_stations+%284%29.csv/232487af-40a1-269a-fc2c-6a80e931b731?t=1778709594281). Actual retrieval was `2026-09-06T18:53:22.661332Z`; HTTP Last-Modified was `2026-05-13T21:59:54Z`. The 669,659-byte file contained 9,591 rows; an exact stripped-ID join matched 20 stations with no missing IDs. CSV SHA256: `4041701569436c98f88b23fceb5d7e6535094ba358e20e86c1559aa023520ade`. These coordinates are directory metadata, not fitted parameters; retain their source and date.

Use a fixed NBM run **`R = D − 2 hours`**. This is a conservative acquisition rule, not a claim that every product was published within two hours. Require the original object's storage timestamp to be no later than `D`, plus an exact matching card runtime and target valid time. A missing, late, changed or malformed object causes abstention for its affected cases; there is no automatic earlier-cycle or later-cycle replacement. Register this rule before acquiring forecast values.

| Nominal horizon | Last possible station observation | NBH/HRRR run | Physical forecast lead | Transformer grid steps to target |
|---|---|---|---:|---:|
| 1 hour | `D − 1 hour` | `D − 2 hours` | 3 hours | 2 |
| 3 hours | `D − 1 hour` | `D − 2 hours` | 5 hours | 4 |
| 6 hours | `D − 1 hour` | `D − 2 hours` | 8 hours | 7 |

This gives equal decision and target times, with different information sources and different ages of the newest inputs. The physical systems assimilate broader observations; the transformer uses a station's recent history. Do not call the comparison equal-information or describe all three candidates as having the same initialization lead. If a latest station observation is missing, the actual transformer input is older still; retain E022's recorded age.

The minimal NBH acquisition manifest is **498 unique objects**, 165 associated with calibration and 333 with development, linked to all 9,870 cases. Request the original bucket `noaa-nbm-grib2-pds`, with keys `blend.YYYYMMDD/HH/text/blend_nbhtx.tHHz`. One object supplies all stations. Required run hours are 01, 03, 04, 07, 09, 10, 13, 15, 16, 19, 21 and 22 UTC, subject to the frozen case calendar. The first key is `blend.20260705/22/text/blend_nbhtx.t22z`; the last is `blend.20260816/15/text/blend_nbhtx.t15z`. The July 5 run is legitimate history for a July 6 decision. [NOAA's original NBM bucket](https://registry.opendata.aws/noaa-nbm/)

This dependency-free recipe expands the exact case-to-object manifest without accessing labels or the network. Canonical JSON means UTF-8 JSON with sorted dictionary keys and no separator whitespace. Its output can be archived and hashed in the next registration; it does not itself authorize acquisition or evaluation.

```python
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

H = 3_600_000
raw = Path("reports/E022_manifest.json").read_bytes()
assert hashlib.sha256(raw).hexdigest() == (
    "ccbcbb142cbb7d6954d3ea0d89000427003b27834f9b9f8bb28477d46339b741"
)
bindings = []
for case in json.loads(raw)["cases"]:
    run_ms = case["decision_ms"] - 2 * H
    run = datetime.fromtimestamp(run_ms / 1000, timezone.utc)
    bindings.append({
        "case_id": case["case_id"], "station_id": case["station_id"],
        "split": case["split"], "target_ms": case["target_ms"],
        "decision_ms": case["decision_ms"], "run_ms": run_ms,
        "forecast_hour": (case["target_ms"] - run_ms) // H,
        "key": f"blend.{run:%Y%m%d}/{run:%H}/text/blend_nbhtx.t{run:%H}z",
    })
bindings.sort(key=lambda row: row["case_id"])
encoded = json.dumps(bindings, sort_keys=True, separators=(",", ":")).encode()
assert len(bindings) == 9870
assert len({row["key"] for row in bindings}) == 498
assert hashlib.sha256(encoded).hexdigest() == (
    "8d36540487bcd56264ed71c7eca5d34eb3013708db33a9ae1ac954127c43ed5e"
)
```

The new acquisition record should retain `case_id`, station, split, decision, requested run, parsed run, forecast lead and exact valid timestamp; bucket/key, object size, ETag, version ID if provided, Last-Modified, listing/HEAD receipt and body receipt; request byte ranges, Content-Range, response headers, archived raw record IDs and SHA256 of retained bytes; product/version, station card and raw `TMP`; and all parse, availability and missing-data reasons. Preserve TWC's actual receipt and assumed historical availability separately. Set `historical_public_availability_verified=false` for the retrospective study.

The existing E003/E010 acquisition pattern is useful: exact-key S3 listings, conditional ETag range requests, matching Last-Modified, exact station/runtime validation, and archived raw receipts. Create new source/config files. Leave `weatherpred/nbm.py`, E003/E010 and E022 frozen. That NBS parser requires 23 columns, an `FHR` row and a specific final line; NBH requires a different layout. New NBH parsing should reconstruct hours 1–25 from the header runtime, check the printed UTC sequence including midnight rollover, require exactly the requested station and `TMP` field, and reject duplicate/truncated cards. Explicitly handle `-99` and absent elements. Do not carry over the old parser's `999` missing-value assumption or require daily-extrema fields. Test these mechanics with synthetic fixtures before reading benchmark values.

For a bounded first acquisition, register one complete NBH object to discover station offsets, then use merged conditional ranges with a fixed 65,536-byte radius per station. Allow at most eight full-object recoveries in chronological key order, a 40 MiB per-object ceiling and a 2 GiB total transfer ceiling; log incomplete acquisition if a ceiling binds. The two endpoint objects checked below are about 28.4 MB each, so downloading all 498 whole objects would be roughly 14.2 GB. Do not assume constant file size or stable offsets merely from these endpoints. Freeze transport retry policy and source hashes before starting; recovery changes byte acquisition, never the selected forecast run.

Keep four times distinct: the model initialization `R`, forecast valid time `T`, current object's Last-Modified `M`, and this project's actual receipt `A`. An old run name alone is insufficient. Require `R` to equal the declared cycle, `R + lead = T`, and `M <= D`; if a historical issuance timestamp is supplied, preserve and gate it too. `A` will be September or later for these retrospective downloads and cannot be relabeled as a July receipt. ETag pins the returned object version; it is not necessarily a cryptographic content hash. HEAD retrieves metadata without the object body. [AWS HeadObject specification](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html)

Even `M <= D` does not prove contemporaneous public accessibility or recreate an overwritten version. It is storage evidence supporting a conditional retrospective comparison. A strictly receipt-verified July/August comparison is unavailable from this project's September downloads. Prospective collection must record successful receipt before a decision. NOAA's schedules vary by product and cycle; some current text-table rows appear earlier than their nominal cycle, so do not translate that table mechanically into historical issuance times. NBM's separate probability products can also arrive much later than core guidance. [NOAA availability schedules](https://blend.mdl.nws.noaa.gov/nbm-documentation)

Metadata-only endpoint checks on September 6 found these six objects. Each request was HEAD, returned HTTP 200, and read no forecast body. They establish endpoints, not complete archive coverage; the future acquisition must check every requested object and index.

| Product/key suffix | Bytes | Last-Modified UTC | Earliest assigned decision UTC |
|---|---:|---|---|
| NBM `20260705/22`, `blend_nbhtx.t22z` | 28,431,858 | July 5 22:38:36 | July 6 00:00 |
| NBM `20260816/15`, `blend_nbhtx.t15z` | 28,431,858 | August 16 15:38:31 | August 16 17:00 |
| HRRR `20260705`, `hrrr.t22z.wrfsfcf08.grib2` | 151,603,700 | July 5 23:07:05 | July 6 00:00 |
| HRRR `20260816`, `hrrr.t15z.wrfsfcf03.grib2` | 158,271,394 | August 16 15:57:52 | August 16 17:00 |
| GFS `20260705/18`, `gfs.t18z.pgrb2.0p25.f012` | 523,588,703 | July 5 21:39:50 | July 6 00:00 |
| GFS `20260816/12`, `gfs.t12z.pgrb2.0p25.f006` | 555,080,550 | August 16 15:35:22 | August 16 17:00 |

Actual HEAD receipts span `2026-09-06T18:54:05.880928Z` through `18:54:06.288673Z`. In table order, returned ETags were `ca388a329ea28237dec442b29c904d8d`, `d5f93b930c875d60c037c67db7239f5a`, `b3a09a71de28b838a1e43ba762c20367`, `2ac7987cf892122e2fe8cc66b420825c`, `920197d0bd903e377c7718ea2bf5c0fd`, and `f22f2e5e31f73de24e3ed577c08b9ed5`. No response supplied a version ID.

HRRR and GFS are feasible additional registered comparators, but the first minimal acquisition need only include NBH. Their original GRIB2 files contain instantaneous temperature two metres above ground, which must be distinguished from surface-skin temperature and time-averaged/max/min fields. Fetch `.idx` inventories and conditional ranges for exactly `TMP:2 m above ground` with matching reference time, forecast step and valid time; verify the decoded GRIB metadata. Pin decoder versions and coordinate mapping. Convert Kelvin using `(K − 273.15) × 9/5 + 32`, without premature rounding. [HRRR surface-field inventory](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/hrrr.t00z.wrfsfcf02.grib2.shtml), [GFS field inventory](https://www.nco.ncep.noaa.gov/pmb/products/gfs/gfs.t00z.pgrb2.0p25.f003.shtml)

| Possible later comparator | Frozen run rule | Leads for nominal 1/3/6h | Unique forecast objects | Original key template |
|---|---|---|---:|---|
| HRRR CONUS surface | `R = D − 2h` | 3 / 5 / 8 | 498 | `hrrr.YYYYMMDD/conus/hrrr.tHHz.wrfsfcfFF.grib2` |
| GFS 0.25 degree | `R = floor_to_6h(D − 4h)` | 6 / 12 / 12 | 332 | `gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF` |

HRRR is hourly and has sufficient standard-cycle lead coverage; GFS cycles are 00/06/12/18 UTC. The GFS rule is an explicit four-hour minimum dissemination assumption plus the same object-storage gate. On this calendar its run ages at decision are 5, 9 and 6 hours respectively. These are much older inputs than a nominal “one-hour GFS forecast” would suggest. Use the original NOAA buckets `noaa-hrrr-bdp-pds` and `noaa-gfs-bdp-pds`; downloaded/repacked archives or later analyses are different products. [NOAA HRRR product timing and filenames](https://www.nco.ncep.noaa.gov/pmb/products/hrrr/), [NOAA GFS products](https://www.nco.ncep.noaa.gov/pmb/products/gfs/), [HRRR archive](https://registry.opendata.aws/noaa-hrrr-pds/), [GFS archive](https://registry.opendata.aws/noaa-gfs-bdp-pds/)

For either GRIB comparator, freeze one nearest geographic grid point per station using the NOAA coordinates above and great-circle distance, with a deterministic grid-index tie break. Archive the chosen latitude/longitude, distance, grid index and grid-definition hash. Do not search nearby cells for lower errors or substitute land cells after seeing scores. Report coastal/terrain representation separately. Choose no spatial or temporal interpolation in the first protocol. These grids cover all 20 continental stations geographically; usable record coverage remains unverified until registered acquisition. Apply timestamp/version checks to `.idx` files as well as the data, and reject mismatched range boundaries.

The next registration should name NBH as the only new candidate, plus **all eight already saved E022 candidates** for arithmetic comparison. Keep raw point forecasts unchanged. Calibrate NBH intervals using the same fixed quantile levels and only the July 6–19 calibration cases: start with its point repeated at every level and add the corresponding empirical residual quantile, then apply the existing monotonic rearrangement. Do not derive probabilities by treating `TSD` as a proven Gaussian error model. No new transformer inference, fine-tuning, residual learner, station-specific adjustment or choice among run lags belongs in this first comparison.

The primary point metric remains daily-weighted raw MAE: average absolute Fahrenheit errors within each UTC target day, then give the 28 days equal weight. Report the original RMSE and calibrated interval metrics too. Preserve every missing physical forecast and the cause. If NBH fails any required case, report the full-panel candidate as incomplete; a separately labeled common-available-case comparison may recompute **every candidate** on the identical subset, with daily/station/horizon counts and no substitution of its result for the full E022 score. Register that contingency before reading values. Do not compare a partial NBH score to the existing full-panel neural score.

If uncertainty intervals are included, predeclare one shared seven-day circular-block bootstrap over the 28 development days, 10,000 draws and seed `6202601`, with all eight NBH-versus-existing-candidate differences retained and simultaneous 95% intervals using the centered maximum absolute standardized statistic. Missing days must not be concatenated into an invented continuous calendar. The point of this finite experiment is to measure added forecasting value against operational weather guidance. It cannot establish a profitable executable strategy, and it should not be advertised as a novel forecasting breakthrough merely because one development comparison improves.

The timestamp-only check executed while preparing this proposal returned:

```json
{"case_count":9870,"forecast_values_read":0,"model_fits":0,"model_inferences":0,"physical_leads":[3,5,8],"scores_computed":0,"station_count":20,"target_horizons":[1,3,6],"unique_nbh_objects":498}
```
