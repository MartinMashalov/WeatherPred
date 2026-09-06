# Retrospective hourly station history

The bounded acquisition retrieved **50,928 source observations** and exported **50,771 unique station-hours across 31 stations**. It queried all 17 predeclared Mondays from May 4 through August 24, 2026. The four May weeks returned valid empty responses. Actual observations begin on June 1 for the original station group; later stations have shorter histories.

These are **current retrospective versions**, downloaded on September 6. Their historical publication and revision times are unknown. No model was fitted, no forecast was scored, and no interval was declared an untouched test set by this acquisition.

## Scope and evidence

The plan was permanently recorded as local archive **88228** before the first request. May 4 and August 24 were probed first. The oldest response had zero observations, while the newest had 5,122, establishing that the endpoint worked but did not supply the entire requested history. The remaining predeclared weeks were then requested.

The source is [TWC's public weekly hourly table](https://weather.com/kalshi/api/metar?primary=true&weekStart=2026-08-24), the endpoint discovered in the published TWC client. Original source reference **78077** described the same schema for the following week; that later week's observations were not added to this dataset.

- **17 HTTP requests, all HTTP 200; no retry was needed.** The minimum measured interval between request starts was **1.002991 seconds**.
- **37 station objects** occur in the source schema. **31 have observations**; six have none in this requested window.
- **157 hours were excluded** because their UTC date was August 31 even though their station-local date was August 30. Both UTC and local model-row dates are capped at August 30.
- **240 hours are missing between the first and last available observation** within the 31 observed station histories. These remain missing; there is no interpolation or manufactured row.
- There were **zero exact duplicate rows and zero conflicting duplicate hours** in the actual retrieved snapshots. The parser handles both cases explicitly for future use.
- Every exported row currently has source status `settled`. That label does not prove historical availability or immunity to later source revision.

Final coverage record: **89421**. Compact summary: **89718**. Source records are **88229, 88232, 88353, 88354, 88365, 88366, 88373, 88377, 88411, 88422, 88423, 88424, 88425, 88426, 88433, 88494 and 88521**.

The deterministic model-row artifact is `reports/station_history_rows.jsonl`, SHA-256:

```text
201faf4ef6e7f2930da4e30659c2a0de39648422187dd6283b28c2f0e4f9a7bc
```

`reports/station_history_coverage.json` contains full station coverage, missing-hour timestamps, rejected-row references and source IDs. `reports/station_history_summary.json` is a compact acquisition/coverage report. `reports/station_history_acquisition.json` retains all request results, including empty weeks. Empty responses are distinguished from unattempted or failed requests.

## Calendar coverage

| Week starting | Raw hours | Exported hours |
|---|---:|---:|
| May 4 | 0 | 0 |
| May 11 | 0 | 0 |
| May 18 | 0 | 0 |
| May 25 | 0 | 0 |
| June 1 | 3,353 | 3,353 |
| June 8 | 3,298 | 3,298 |
| June 15 | 3,327 | 3,327 |
| June 22 | 3,352 | 3,352 |
| June 29 | 3,348 | 3,348 |
| July 6 | 3,667 | 3,667 |
| July 13 | 4,008 | 4,008 |
| July 20 | 3,994 | 3,994 |
| July 27 | 4,167 | 4,167 |
| August 3 | 4,196 | 4,196 |
| August 10 | 4,454 | 4,454 |
| August 17 | 4,642 | 4,642 |
| August 24 | 5,122 | 4,965 |

Twenty stations have observations from June 1: KATL, KAUS, KBOS, KDCA, KDEN, KDFW, KIAH, KLAS, KLAX, KMIA, KMSP, KMSY, KNYC, KOKC, KORD, KPHL, KPHX, KSAT, KSEA and KSFO.

The later starts are explicit: KGNV/KJAX/KSPG/KTPA on July 9; KSAN on July 27; KHOU/KMDW on August 11; KSJC on August 19; KEWR/KTTN on August 24; KSDF on August 26. KCLL, KCMH, KDJT, KLEX, KMKE and KPVD have zero rows.

Consequently, a benchmark asking for a full preceding week of context must exclude early cases deterministically. Later station availability must not be mistaken for a continuously observed historical universe. Houston Bush (KIAH) and Houston Hobby (KHOU), or Chicago O'Hare (KORD) and Midway (KMDW), are separate series.

## Row schema and time interpretation

Each JSONL row contains:

| Field | Meaning |
|---|---|
| `station_id`, `station_timezone`, `station_name` | Source station identity and IANA timezone |
| `observed_at` | Normalized UTC value of the source's `reportTimeUTC` hourly timestamp |
| `local_date`, `local_hour`, `local_fold` | Local calendar derived from UTC and the station timezone; fold distinguishes a repeated daylight-saving hour |
| `temperature_f` | Numeric Fahrenheit value exactly as reported, used as the candidate modeling variable |
| `temperature_c_reported` | Original Celsius field, retained separately |
| `status` | Original `settled` or `pending` label |
| `source_record_id`, `source_row_index`, `source_row_indexes`, `source_station_index` | Exact archived response and row references, including collapsed duplicates |
| `source_report_time_utc`, `source_report_time_local` | Original source timestamp/display fields |
| `received_at` | Our September 6 archival receipt time |
| `provider_fetched_at`, `provider_source` | Provider metadata retained without treating it as historical publication time |
| `requested_week_start` | The predeclared local-calendar week requested |
| `historical_availability_verified` | Always `false` for this retrospective acquisition |

The source's hourly timestamp is not evidence of the raw instrument's measurement or dissemination time. In particular, setting an assumed 15-minute information lag in a historical forecasting benchmark would be an experimental assumption, not a demonstrated latency advantage.

The weekly TWC hourly values also must not automatically be treated as the same target as the proprietary Miami hourly index. A trading experiment must establish its settlement mapping separately.

## Parser safeguards

The parser verifies the requested Monday/week-end/date array, station identity, timezone, source observation count, numeric temperature, recognized status and exact hourly UTC alignment. A local-hour/date mismatch or naive timestamp is rejected with its source row index. Current/future receipt inconsistencies and dates beyond the model cutoff are rejected.

The unique key is **station plus UTC timestamp**. Exact repetitions collapse and keep all source-row indexes. Conflicting versions of the same hour are quarantined together; the code does not choose the warmer value, the later response position or a version favorable to a model. Distinct UTC timestamps that share a local hour during daylight-saving transitions remain distinct, with their fold values preserved. The requested May–August period contains no such repeated hour for the observed station timezones; the behavior is tested synthetically.

Missing rows remain absent. Coverage compares the exported UTC timestamps with the expected hourly calendar for every source station, including zero-row stations. Raw response bytes remain immutable in the archive. Re-running acquisition reuses those captured responses; re-export verifies their hashes and writes the JSONL atomically so concurrent readers cannot observe a partially rewritten file.

## Reproduction and checks

```sh
uv run python research/probes/station_history.py --probe-only
uv run python research/probes/station_history.py
uv run python research/probes/station_history.py --export-only
uv run pytest -q tests/test_station_history.py
```

The first command obtains or reuses the two availability probes; the second completes or reuses the bounded acquisition; the third makes no network requests. Current source versions are not silently refreshed by these commands.

Actual checks in this session:

```text
All checks passed!
..........                                                               [100%]
10 passed in 0.03s
```

Tests cover receipt/status preservation, missing hours, duplicate and conflicting revisions, station/calendar mismatch, naive timestamps, invalid temperatures/statuses, the UTC cutoff, repeated local hours, empty history and incorrect provider counts. No sealed October–December 2025 data was read.
