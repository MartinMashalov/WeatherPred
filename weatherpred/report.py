"""Render a self-contained evidence dashboard from actual local report records."""

import html
import json
from collections import Counter
from pathlib import Path

from weatherpred.timeutil import iso, utcnow


def dashboard(archive, directory="reports"):
    root = Path(directory)
    universe = json.loads((root / "universe.json").read_text())
    market_census = json.loads((root / "markets_open.json").read_text())
    baskets = json.loads((root / "E001_baskets.json").read_text())
    summary = baskets["summary"]
    capture_row = archive.latest("capture_cycle", "temperature")
    capture = archive.json(capture_row) if capture_row else None
    baseline_path = root / "E004_baselines.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    benchmark_html = ""
    pending_path = root / "E005_pending_index.json"
    pending_html = ""
    if pending_path.exists():
        pending = json.loads(pending_path.read_text())
        lead = pending.get("first_seen_lead_seconds")
        median = f"{lead['median']:.0f} seconds" if lead else "Not measured"
        pairs_path = root / "E005_pending_pairs.jsonl"
        pairs = (
            [json.loads(line) for line in pairs_path.read_text().splitlines()] if pairs_path.exists() else []
        )
        distinct_values = len({r["canonical_value"] for r in pairs if r["status"] == "paired"})
        arithmetic = pending["historical_conditional_arithmetic"]
        pending_html = f"""<section class="panel"><h2>E005 · Earlier station inputs</h2>
<p>{pending["exact_provisional_matches"]} of {pending["live_paired_minutes"]} provisional estimates matched
the later index value. Median first-seen lead in our sampled feed: {median}.</p>
<p class="muted">This snapshot contains {distinct_values} distinct paired values. These minutes are dependent;
they are not independent weather events. Market-price advantage has
not been tested. Pending inputs are forecasts, never settlement labels.</p>
<p class="muted">Historical arithmetic: {arithmetic.get("half_up_matches", 0):,} of {arithmetic["points"]:,}
values reproduced; a fallback rounding discrepancy
remains unresolved. No profit or execution conclusion.</p></section>"""
    if baseline:
        score_rows = baseline["binary_scores_paired_event_averaged"]
        lines = []
        for horizon in (30, 15, 5):
            market = next(
                r for r in score_rows if r["model"] == "market_midpoint" and r["horizon_minutes"] == horizon
            )
            simple = next(
                r
                for r in score_rows
                if r["model"] == "persistence_empirical" and r["horizon_minutes"] == horizon
            )
            lines.append(
                f"<tr><td>{horizon} minutes</td><td>{market['events']}</td>"
                f"<td>{market['brier']:.3f}</td><td class='negative'>{simple['brier']:.3f}</td></tr>"
            )
        benchmark_html = f"""<section class="panel"><h2>E004 · Simple forecasts trail the market</h2>
<p>Miami hourly temperature, September 1–5. All four frozen baselines scored worse than market prices.</p>
<table><thead><tr><th>Before close</th><th>Events</th><th>Market</th><th>Persistence</th></tr></thead>
<tbody>{"".join(lines)}</tbody></table><p class="muted">Brier probability score: lower is better.
Persistence predicts from the last eligible temperature and training errors. It is the strongest of four
tested baselines in these data. Scores average contracts within each event first.</p>
<p class="muted">Only five validation days. Historical availability uses an unverified 10-minute lag.
1,219 of 3,510 contract/horizon pairs had eligible quotes. Candles supply no fill or depth evidence.</p></section>"""
    counts = dict(Counter(row["kind"] for row in archive.db.execute("SELECT kind FROM records")))
    fresh_html = ""
    fresh_path = root / "E006_model.json"
    if fresh_path.exists():
        fresh = json.loads(fresh_path.read_text())
        fresh_html = f"""<section class="panel"><h2>E006 · Does fresher input help?</h2>
<p>A separate model comparison is registered for {len(fresh["slots"])} future slots, starting
{html.escape(fresh["slots"][0]["decision_at"][:19])} UTC. The model was fitted to
{fresh["training_rows"]} training examples.</p>
<p>{counts.get("e006_forecast", 0)} paired forecasts recorded; {counts.get("e006_skip", 0)} skipped slots.</p>
<p class="muted">Five-minute and ten-minute input limits use the same archived response and quotes.
The September 1–5 validation results are not reused for this new comparison.
No orders, fills or model promotion.</p></section>"""
    nbm_html = ""
    nbm_path = root / "E003_NBM_acquisition_progress.json"
    if (root / "E003_NBM_acquisition.json").exists():
        nbm_path = root / "E003_NBM_acquisition.json"
    if nbm_path.exists():
        nbm = json.loads(nbm_path.read_text())
        nbm_html = f"""<section class="panel"><h2>E003 · Original NBM forecast archive</h2>
<p>{len(nbm["days"])} development days acquired from NOAA, with eight station cards per complete day;
{len(nbm["errors"])} unresolved acquisition errors in this snapshot.</p>
<p class="muted">Exact station identifiers, forecast times and object storage timestamps retained.
Storage timestamps provide evidence separate from model initialization. Historical public-access
history still needs verification. Forecast scores are reported separately below; no trading returns.</p></section>"""
    daily_html = ""
    daily_path = root / "E003_daily_baselines.json"
    if daily_path.exists():
        daily = json.loads(daily_path.read_text())
        lookup = {(r["model"], r["horizon_hours"]): r for r in daily["validation_scores"]}
        daily_rows = []
        for key, label in (
            ("raw_market", "Market"),
            ("nbm_native_18h_gaussian_proxy", "Native guidance proxy"),
            ("nbm_spread_regression", "Spread regression"),
            ("grid_station_empirical", "Local error distribution"),
        ):
            daily_rows.append(
                f"<tr><td>{label}</td><td>{lookup[(key, 24)]['brier']:.4f}</td>"
                f"<td>{lookup[(key, 12)]['brier']:.4f}</td></tr>"
            )
        candidate_names = {name for name, _ in lookup if name not in ("raw_market", "logistic_market")}
        losses = sum(
            all(lookup[(name, h)]["brier"] > lookup[("raw_market", h)]["brier"] for h in (24, 12, 6, 3))
            for name in candidate_names
        )
        daily_html = f"""<section class="panel"><h2>E003 · Daily forecast comparison</h2>
<p>July–September 2025: {losses} of {len(candidate_names)} frozen NBM-based models had worse Brier scores
than the market at all four lead times. The 24-hour comparison covers
{lookup[("raw_market", 24)]["events"]} events across {lookup[("raw_market", 24)]["days"]} calendar days.</p>
<table><thead><tr><th>Probability model</th><th>24 hours</th><th>12 hours</th></tr></thead>
<tbody>{"".join(daily_rows)}</tbody></table>
<p class="muted">Lower is better. Forecasts stay fixed from the start of the day; they do not use later observations.
Native guidance is an 18-hour Gaussian proxy, not a direct forecast of the contract's full-day outcome.
Other models calibrate to verified full-day labels.</p>
<p class="muted">Only 61 eligible contract quotes at six hours and 9 at three hours.
Calendar gaps leave their block confidence intervals unavailable. All full-event model probabilities
sum to one. No fills, net returns or profitable strategy established.</p></section>"""
    calibration_html = ""
    calibration_path = root / "E002_baselines.json"
    if calibration_path.exists():
        result = json.loads(calibration_path.read_text())
        intervals = [
            r
            for r in result["paired_day_block_comparisons"]
            if r["block_days"] == 7 and r["metric"] == "brier" and r["horizon_hours"] in (24, 12)
        ]
        included_zero = sum(r.get("lower", 1) <= 0 <= r.get("upper", -1) for r in intervals)
        calibration_html = f"""<section class="panel"><h2>E002 · Market calibration uncertainty</h2>
<p>{included_zero} of {len(intervals)} available seven-day-block confidence intervals at 24 and 12 hours
include zero improvement. A point estimate alone does not establish an advantage.</p>
<p class="muted">The original estimator rejects gaps in eligible dates. Those gaps are retained;
no smaller multiple-comparison family is substituted. The final October–December holdout is sealed.</p></section>"""
    rows = []
    for side in ("yes", "no"):
        for scenario in ("optimistic", "realistic", "pessimistic"):
            values = [
                float(s["conditional_pnl_integer_usd"])
                for r in baskets["results"]
                for s in r.get("scenarios", [])
                if s["side"] == side and s["scenario"] == scenario and s["full_displayed_size_available"]
            ]
            best = f"${max(values):.4f}" if values else "Unavailable"
            rows.append(
                f"<tr><td>{side.upper()} basket</td><td>{scenario.title()}</td>"
                f"<td>{len(values)}</td><td class='negative'>{best}</td></tr>"
            )
    timestamp = iso(utcnow())
    captured = html.escape(capture["finished_at"][:19] + " UTC") if capture else "No capture yet"
    pool_html = ""
    if (root / "E007_market_weather_pool.json").exists():
        pool = json.loads((root / "E007_market_weather_pool.json").read_text())
        pool_scores = {r["model"]: r for r in pool["validation_scores"] if r["horizon_hours"] == 24}
        pool_rows = "".join(
            f"<tr><td>{label}</td><td>{pool_scores[name]['brier']:.5f}</td></tr>"
            for name, label in (
                ("temperature_market", "Calibrated market"),
                ("nbm_native_18h_gaussian_proxy", "Market + native weather"),
                ("nbm_spread_regression", "Market + calibrated weather"),
            )
        )
        pool_html = f"""<section class="panel"><h2>E007 · Combining market and weather</h2>
<p>A small probability-score improvement on {pool_scores["temperature_market"]["events"]} complete events
across {pool_scores["temperature_market"]["days"]} development days. Lower Brier score is better.</p>
<table><thead><tr><th>Forecast</th><th>Brier</th></tr></thead><tbody>{pool_rows}</tbody></table>
<p class="muted">Six combinations tested. Four gave the weather forecast zero weight. Training forecasts
use earlier months only. Calendar gaps prevent the registered confidence calculation; this is a lead, not proof.</p></section>"""
    cost_html = ""
    if (root / "E008_quote_cost_screen.json").exists():
        screen = json.loads((root / "E008_quote_cost_screen.json").read_text())
        names = {
            "zero_fee_zero_delay_quote": "No fees or slippage",
            "current_fee_plus_one_cent": "Fee assumption + 1 cent",
            "cent_rounded_fee_plus_two_cents": "Rounded fee + 2 cents",
        }
        cost_rows = "".join(
            f"<tr><td>{names[r['scenario']]}</td><td>{r['conditional_purchases']}</td><td>${float(r['conditional_pnl_usd']):.2f}</td></tr>"
            for r in screen["conditional_scenario_results"]
            if r["model"] == "nbm_native_18h_gaussian_proxy"
        )
        cost_html = f"""<section class="panel"><h2>E008 · Costs erase the candidate gain</h2>
<p>Market + native weather, one conditional contract per selected event.</p>
<table><thead><tr><th>Cost assumption</th><th>Purchases</th><th>Conditional P&amp;L</th></tr></thead><tbody>{cost_rows}</tbody></table>
<p class="muted">These historical candle scenarios assume a fill; they do not prove one. Fee assumptions
are not verified historical fees. All models and scenarios retained. No statistically established gain.</p></section>"""
    paper_html = ""
    if (root / "E009_paper_state.json").exists():
        paper = json.loads((root / "E009_paper_state.json").read_text())
        scenario_names = {
            "taker_1s_full": "Taker · 1s / full depth",
            "taker_5s_half": "Taker · 5s / half depth",
            "taker_30s_quarter": "Taker · 30s / quarter depth",
            "maker_5s_queue": "Maker · 5s / queue",
        }
        paper_rows = []
        for scenario, label in scenario_names.items():
            accounts = [a for a in paper["accounts"] if a["account"].endswith(":" + scenario)]
            equities = [float(a["marked_equity"]) for a in accounts]
            fills = sum(a["paper_fill_count"] for a in accounts)
            paper_rows.append(
                f"<tr><td>{label}</td><td>{fills}</td><td>${min(equities):.2f}–${max(equities):.2f}</td></tr>"
            )
        account_rows = "".join(
            f"<tr><td class='wrap'>{html.escape(a['account'])}</td><td>${float(a['available_cash']):.2f}</td><td>${float(a['realized_pnl']):.2f}</td></tr>"
            for a in paper["accounts"]
        )
        open_orders = sum(o["status"] in ("pending", "resting") for o in paper["orders"])
        paper_html = f"""<section class="panel" id="paper"><div class="label">Paper execution · no real-money orders</div>
<h2>Eight models, four fill assumptions</h2>
<p>{len(paper["accounts"])} independent virtual accounts, each starting with $100.</p>
<div class="grid"><div><div class="number">{len(paper["orders"])}</div><div class="muted">Paper orders</div></div>
<div><div class="number">{paper["paper_fill_count"]}</div><div class="muted">Simulated fills</div></div>
<div><div class="number">{len(paper["positions"])}</div><div class="muted">Open positions</div></div>
<div><div class="number">{open_orders}</div><div class="muted">Pending orders</div></div></div>
<table><thead><tr><th>Fill assumption</th><th>Fills</th><th>Account equity range</th></tr></thead><tbody>{"".join(paper_rows)}</tbody></table>
<p class="muted">Taker fills use books requested after the order delay, with depth limits, fees and partial fills.
Maker fills require trades through the limit price after the queue ahead clears. A price touch does not fill.</p>
<p class="muted">Equity uses liquidating bids before exit fees; closed positions awaiting final settlement remain at cost.
These are simulated results, not exchange executions or proven returns. First slot: 13:30 UTC.</p>
<details><summary>All virtual accounts: cash and settled profit/loss</summary><table><thead><tr><th>Account</th><th>Available cash</th><th>Settled P&amp;L</th></tr></thead><tbody>{account_rows}</tbody></table></details>
<p class="muted">Snapshot {html.escape(paper["generated_at"][:19])} UTC · {len(paper["processed_slots"])} slots processed.
The process updates this page every 30 seconds while running.</p></section>"""
    data = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>WeatherPred Research Monitor</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#0d1520;color:#e8edf4;font:15px/1.5 system-ui,sans-serif}}
main{{max-width:1080px;margin:auto;padding:32px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:center}}
.label{{color:#93aec5;text-transform:uppercase;letter-spacing:.14em;font-size:11px;font-weight:700}}
h1{{font-size:30px;letter-spacing:-.03em;margin:4px 0 12px}}h2{{font-size:19px;margin:0 0 12px}}
p{{color:#acbdcf;margin:8px 0}}.badge{{border:1px solid #ca9458;background:#302519;color:#f4c589;border-radius:24px;padding:7px 14px;white-space:nowrap;font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:24px 0}}
.card,.panel{{background:#152231;border:1px solid #2b3e51;border-radius:12px;padding:20px}}
.number{{font-size:34px;font-weight:650;line-height:1.3;margin-top:8px}}.muted{{color:#a7b9ca;font-size:13px}}
.panel{{margin:16px 0}}.negative{{color:#edb5a4;font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;font-size:14px}}th{{text-align:left;color:#93aec5;font-size:12px;font-weight:500}}
td,th{{padding:11px 6px;border-bottom:1px solid #2b3e51}}td:last-child,th:last-child{{text-align:right}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}ul{{padding-left:18px;color:#bdcbd8}}li{{margin:9px 0}}
.footer{{font-size:11px;color:#8d9fb2;margin-top:24px}}.status{{color:#9ecabe}}
.wrap{{overflow-wrap:anywhere;max-width:170px}}details{{margin-top:16px}}summary{{cursor:pointer;color:#bed4e6}}
@media(max-width:650px){{main{{padding:20px}}header{{display:block}}h1{{font-size:26px}}.badge{{display:inline-block}}.grid{{grid-template-columns:1fr 1fr;gap:10px}}.card{{padding:14px}}.number{{font-size:28px}}.two{{grid-template-columns:1fr;gap:0}}table{{font-size:12px}}td,th{{padding:10px 3px}}.panel{{padding:16px}}}}
</style></head><body><main>
<header><div><div class="label">WeatherPred / Evidence monitor</div><h1>Search for an executable edge</h1></div>
<span class="badge">Profitability unproven</span></header>
<p>Public data research. No real-money orders. Results below are exploratory observations.</p>
{paper_html}
<section class="grid">
<div class="card"><div class="label">Series discovered</div><div class="number">{universe["climate_series_count"]}</div><div class="muted">Climate &amp; weather catalog</div></div>
<div class="card"><div class="label">Open contracts</div><div class="number">{len(market_census["markets"]):,}</div><div class="muted">Census across all 367 series</div></div>
<div class="card"><div class="label">Daily events tested</div><div class="number">{summary["states"].get("observed", 0)}</div><div class="muted">E001 corrected coverage</div></div>
<div class="card"><div class="label">Positive baskets</div><div class="number">{summary["positive_conditional_integer_scenarios"]}</div><div class="muted">Of {summary["fully_quoted_scenarios"]} fully quoted scenarios</div></div>
</section>
{benchmark_html}
{fresh_html}
{nbm_html}
{daily_html}
{calibration_html}
{pool_html}
{cost_html}
{pending_html}
<section class="panel"><h2>E001 · Complete temperature baskets</h2>
<p>Buying one contract on every bracket produced no positive net result in this snapshot.</p>
<table><thead><tr><th>Side</th><th>Depth / costs</th><th>Available</th><th>Best conditional net</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<p class="muted">Dollars per one-unit basket under the integer-outcome assumption. Fees included.
Slippage: 0 / 1 / 2 cents per leg; retained depth: 100% / 50% / 25%. Displayed quotes are not fills.
Settlement precision is unresolved; multi-leg execution is not atomic.</p></section>
<div class="two"><section class="panel"><h2>Data capture</h2>
<div class="status">Last recorded cycle: {capture["cycle"] if capture else "none"}</div>
<p>{captured}</p><ul><li>{capture["market_tickers"] if capture else 0} temperature books requested per recent cycle</li>
<li>{len(capture["errors"]) if capture else 0} errors in the latest cycle</li><li>{sum(counts.values()):,} append-only archive records</li>
<li>{counts.get("shadow_forecast", 0)} original baseline forecasts logged; no exchange orders</li></ul>
<p class="muted">A timestamp shows the last receipt, not proof the process is still running. This page is a generated snapshot.</p></section>
<section class="panel"><h2>What prevents a trading claim</h2><ul>
<li>Reconcile current settlement sources, station windows and precision.</li>
<li>Establish when each historical forecast was actually available.</li>
<li>Improve on market probabilities; initial simple baselines trail them.</li>
<li>Verify fills, untouched outcomes and a separate forward test.</li></ul>
<p class="muted">Growth, drawdown, ruin and $10,000 target probabilities: not estimated.</p></section></div>
<div class="footer">Generated {timestamp} · E001 observed {summary["generated_at"]}<br>
Sources: Kalshi, NOAA and IEM archives of NWS reports. Complete evidence and limitations: EXPERIMENTS.md and docs/REQUIREMENTS.md.</div>
</main></body></html>"""
    path = root / "dashboard.html"
    path.write_text(data)
    return {"dashboard": str(path.resolve()), "generated_at": timestamp}
