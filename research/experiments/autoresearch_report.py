"""Build a standalone, phone-readable trading-research result browser."""

import json
from pathlib import Path


def main():
    first = json.loads(Path("reports/E013_autoresearch.json").read_text())
    second = json.loads(Path("reports/E014_intraday_bounds.json").read_text())
    rows = []
    for batch, report in (("Price behavior", first), ("Observed high", second)):
        for r in report["results"]:
            v = r["split_results"]["validation"]
            rows.append(
                {
                    "batch": batch,
                    "family": r["policy"]["family"],
                    "scenario": r["scenario"],
                    "policy": r["policy"],
                    "pnl": v["conditional_pnl"],
                    "trades": v["released_trades"],
                    "days": v["trade_days"],
                    "adjusted_p": r["development_diagnostic"]["familywise_max_statistic_pvalue"],
                }
            )
    template = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>WeatherPred · Trading research</title><style>
:root{font-family:system-ui,sans-serif;color:#e8eef5;background:#0d1421}*{box-sizing:border-box}body{margin:0}main{max-width:1000px;margin:auto;padding:28px 20px 60px}small,.muted{color:#9facc0}h1{font-size:32px;line-height:1.1;margin:12px 0 18px}h2{font-size:19px;margin:0 0 10px}p{line-height:1.55}section{background:#172235;border:1px solid #2d3c54;border-radius:14px;padding:18px;margin:16px 0}.tag{color:#f3c96c;font-size:12px;font-weight:700;letter-spacing:1px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.card{background:#172235;border-radius:12px;padding:16px}.card strong{font-size:28px;display:block;margin-bottom:5px}.loss{color:#ff9f9f}.good{color:#81d3bb}.callout{border-left:4px solid #f3c96c}label{display:inline-flex;flex-direction:column;gap:5px;margin:0 12px 12px 0;font-size:13px}select{background:#0d1421;color:#fff;border:1px solid #6a7d99;border-radius:6px;padding:9px;max-width:100%}table{width:100%;border-collapse:collapse;font-size:13px}th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #324057}th{color:#afbdd0}details{margin:8px 0}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}button{border:1px solid #6a7d99;background:#22324b;color:#fff;border-radius:6px;padding:9px;cursor:pointer}@media(max-width:560px){main{padding:22px 15px}.cards{grid-template-columns:1fr 1fr}.card:last-child{grid-column:span 2}.card strong{font-size:25px}h1{font-size:29px}.desktop{display:none}td,th{padding:10px 4px}}
</style><main><div class="tag">WEATHERPRED / AUTORESEARCH</div><h1>Test trades.<br>Keep the evidence.</h1><p class="muted">Two registered batches · January–September 2025 data · Updated __TIME__</p>
<div class="cards"><div class="card"><strong>1,836</strong><small>Policy × cost comparisons</small></div><div class="card"><strong>0</strong><small>Validated profitable strategies</small></div><div class="card"><strong class="loss">−$4.45</strong><small>Monthly selector, hypothetical $100 account</small></div></div>
<section class="callout"><h2>Trading returns drive the search</h2><p>Price momentum, reversals, favorites, longshots and observed-temperature constraints. A strategy does not need to win an overall probability-accuracy contest.</p><small>Historical candle trades are conditional estimates. Actual depth and fills remain unverified. The final holdout stays sealed.</small></section>
<section><h2>What the batches found</h2><p><b>Price behavior:</b> 30 of 576 policies showed positive costed validation P&amp;L. None survived the statistical search adjustment. Selecting using earlier months lost money.</p><p><b>Observed highs:</b> no positive costed result. Preliminary reports can disagree with final settlement; cheap contracts were not guaranteed payouts.</p></section>
<section><h2>Explore every result</h2><label>Family<select id="family"><option value="all">All families</option></select></label><label>Execution assumption<select id="scenario"><option value="costed">Fees + 1¢ + 1h delay</option><option value="stress">Fees + 2¢ + 2h delay</option><option value="frictionless_diagnostic">No fees, 1h delay</option></select></label><p id="count" class="muted"></p><table><thead><tr><th>Policy</th><th>Net $</th><th>Trades</th><th class="desktop">Adjusted p</th></tr></thead><tbody id="results"></tbody></table><p><button id="more">Show more results</button></p><small>P&amp;L covers the 92-day development validation period, one conditional contract per selected event. Variations reuse the same events and are not independent trials. Positive rows are exploratory leads.</small></section>
<section><h2>Next experiments</h2><p>Faster reaction to newly received observations; maker spread capture after queue and adverse-selection checks; transient inconsistencies across related brackets. Each new batch is registered before scoring.</p><small>Live paper orders retain their frozen model and execution rules. No real-money orders.</small></section></main>
<script>const rows=__DATA__;let limit=20;const fam=document.querySelector('#family'),sc=document.querySelector('#scenario');for(const name of [...new Set(rows.map(r=>r.family))]){const o=document.createElement('option');o.value=name;o.textContent=name.replaceAll('_',' ');fam.append(o)}function draw(){const chosen=rows.filter(r=>(fam.value==='all'||r.family===fam.value)&&r.scenario===sc.value).sort((a,b)=>b.pnl-a.pnl||a.policy.id.localeCompare(b.policy.id));document.querySelector('#count').textContent=chosen.length+' comparisons · sorted by exploratory P&L';const tbody=document.querySelector('#results');tbody.replaceChildren();for(const r of chosen.slice(0,limit)){const tr=document.createElement('tr');const td=document.createElement('td');const d=document.createElement('details'),s=document.createElement('summary'),p=document.createElement('pre');s.textContent=r.family.replaceAll('_',' ');p.textContent=JSON.stringify(r.policy,null,2);d.append(s,p);td.append(d);tr.append(td);for(const [i,v]of [r.pnl.toFixed(2),r.trades,r.adjusted_p.toFixed(4)].entries()){const t=document.createElement('td');t.textContent=v;if(i===0)t.className=r.pnl>0?'good':'loss';if(i===2)t.className='desktop';tr.append(t)}tbody.append(tr)}}fam.onchange=sc.onchange=()=>{limit=20;draw()};document.querySelector('#more').onclick=()=>{limit+=20;draw()};draw();</script></html>"""
    body = template.replace("__TIME__", first["generated_at"][:16].replace("T", " ") + " UTC")
    body = body.replace("__DATA__", json.dumps(rows).replace("<", "\\u003c"))
    Path("reports/autoresearch.html").write_text(body)
    print(
        json.dumps(
            {"report": "reports/autoresearch.html", "comparisons": len(rows), "profitability_proven": False}
        )
    )


if __name__ == "__main__":
    main()
