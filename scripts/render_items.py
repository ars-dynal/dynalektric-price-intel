#!/usr/bin/env python3
"""
Renders docs/items.html — the detailed, per-item price table — from
data/items.json (the item list + your rates) and data/public_summary.json
(today's live benchmark prices).

Run in the daily workflow AFTER fetch_public_prices.py, so the benchmark
prices, gaps, and data-quality flags on the page refresh every day using the
freshly-fetched aluminium/copper prices. The item list itself only changes
when data/items.json is regenerated from a new master export (a separate,
periodic step).

NOTE: unlike render_public.py, this page intentionally DOES show item codes
and rupee rates — it is published by explicit owner decision. Keep that in
mind before treating this repo's Pages site as fully non-sensitive.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Realistic per-kg floor below which a "price" is almost certainly a
# placeholder / stale entry rather than a real material cost.
FLOOR = {"Copper": 200.0, "Aluminium": 80.0, "CRGO steel": 40.0}
CARD_CLS = {"Copper": "cu", "Aluminium": "al", "CRGO steel": "st"}


def classify(rate, market, metal):
    """Return (flag_key, flag_label). Flags update as benchmark prices move."""
    if rate is None or rate <= 0:
        return "norate", "No rate"
    if rate < FLOOR[metal]:
        return "placeholder", "Placeholder / stale"
    if market and rate > 4 * market:
        return "outlier", "Outlier high"
    gap = (market - rate) / rate * 100 if rate else 0
    if gap >= 50:
        return "review", "Rate far below market"
    return "ok", "OK"


def main():
    with open(os.path.join(ROOT, "data", "items.json")) as f:
        doc = json.load(f)
    with open(os.path.join(ROOT, "data", "public_summary.json")) as f:
        s = json.load(f)

    bench = {
        "Copper": {"price": s["copper"]["price_per_kg"],
                   "src": s["copper"]["source"], "asof": s["copper"]["settlement_date"],
                   "indicative": False},
        "Aluminium": {"price": s["aluminium"]["price_per_kg"],
                      "src": s["aluminium"]["source"], "asof": s["aluminium"]["effective_date"],
                      "indicative": False},
        "CRGO steel": {"price": s.get("crgo_steel", {}).get("price_per_kg", 230.0),
                       "src": s.get("crgo_steel", {}).get("source", "indicative"),
                       "asof": s.get("crgo_steel", {}).get("effective_date", ""),
                       "indicative": True},
    }

    enriched, counts = [], {"placeholder": 0, "norate": 0, "outlier": 0, "review": 0, "ok": 0}
    for it in doc["items"]:
        metal = it["metal"]
        market = bench[metal]["price"]
        rate = it.get("rate")
        gap = ((market - rate) / rate * 100) if rate else None
        fk, fl = classify(rate, market, metal)
        counts[fk] += 1
        enriched.append({"code": it["code"], "name": it["name"], "cat": it["cat"],
                         "metal": metal, "uom": it["uom"], "yr": rate, "lm": market,
                         "gap": gap, "fk": fk, "fl": fl})

    bench_cards = ""
    for m, b in bench.items():
        tier = '<span class="tag-ind">indicative</span>' if b["indicative"] else ""
        bench_cards += (
            f'<div class="card {CARD_CLS[m]}"><div class="accent"></div>'
            f'<div class="label"><span class="swatch"></span>{m} {tier}</div>'
            f'<div class="value">₹{b["price"]:,.2f} <small>/ kg</small></div>'
            f'<div class="meta">{b["src"]} · {b["asof"]}</div></div>')

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (TEMPLATE
            .replace("{{BENCH_CARDS}}", bench_cards)
            .replace("{{DATA}}", json.dumps(enriched, ensure_ascii=False))
            .replace("{{GENERATED}}", generated)
            .replace("{{N_TOTAL}}", str(len(enriched)))
            .replace("{{N_PLACEHOLDER}}", str(counts["placeholder"]))
            .replace("{{N_NORATE}}", str(counts["norate"]))
            .replace("{{N_REVIEW}}", str(counts["review"]))
            .replace("{{N_OUTLIER}}", str(counts["outlier"])))

    out = os.path.join(ROOT, "docs", "items.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out}: {len(enriched)} items | flags: "
          f"{counts['placeholder']} placeholder, {counts['norate']} no-rate, "
          f"{counts['review']} review, {counts['outlier']} outlier")


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dynalektric - Live Item Prices</title>
<style>
:root{color-scheme:light dark;--bg:#f9f9f7;--surf:#fff;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.10);--grid:#e6e5df;
--cu:#eb6834;--al:#2a78d6;--st:#7a869a;--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--goodt:#e7f6e7;--warnt:#fff6e2;--critt:#fbe9e9;}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--mut:#898781;--bd:rgba(255,255,255,.12);--grid:#2c2c2a;--goodt:rgba(12,163,12,.16);--warnt:rgba(250,178,25,.16);--critt:rgba(208,59,59,.16);}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1240px;margin:0 auto;padding:22px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand .bname{font-weight:700;font-size:15px}.brand .btag{font-size:12px;color:var(--mut)}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13.5px;margin:0 0 8px;max-width:860px;line-height:1.5}
.gen{font-size:11.5px;color:var(--mut);margin:0 0 18px}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:16px}
@media(max-width:760px){.cards{grid-template-columns:1fr}}
.card{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:15px 16px;position:relative;overflow:hidden}
.card .accent{position:absolute;left:0;top:0;bottom:0;width:4px}.card.cu .accent{background:var(--cu)}.card.al .accent{background:var(--al)}.card.st .accent{background:var(--st)}
.card .label{font-size:12px;color:var(--mut);margin-bottom:6px;display:flex;align-items:center;gap:6px}
.card .swatch{width:8px;height:8px;border-radius:2px}.card.cu .swatch{background:var(--cu)}.card.al .swatch{background:var(--al)}.card.st .swatch{background:var(--st)}
.card .value{font-size:24px;font-weight:650}.card .value small{font-size:12px;color:var(--tx2);font-weight:500}
.card .meta{font-size:11px;color:var(--mut);margin-top:5px}
.tag-ind{font-size:9.5px;font-weight:700;color:#a06a00;border:1px solid var(--warn);border-radius:4px;padding:1px 5px;margin-left:4px}
.flagbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.chip{font-size:12px;padding:6px 11px;border-radius:999px;border:1px solid var(--bd);background:var(--surf);cursor:pointer;color:var(--tx2)}
.chip b{color:var(--tx)}.chip.on{outline:2px solid var(--al)}
.chip.c-placeholder b{color:var(--crit)}.chip.c-review b{color:#a06a00}.chip.c-norate b{color:var(--mut)}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.controls input,.controls select{font-size:13px;padding:7px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--surf);color:var(--tx)}
.controls input{min-width:230px}.count{font-size:12px;color:var(--mut);margin-left:auto}
table{width:100%;border-collapse:collapse;background:var(--surf);border:1px solid var(--bd);border-radius:12px;overflow:hidden}
thead th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:600;padding:9px 10px;border-bottom:1px solid var(--grid);position:sticky;top:0;background:var(--surf);cursor:pointer;white-space:nowrap}
thead th.num{text-align:right}tbody td{padding:7px 10px;border-bottom:1px solid var(--grid);font-size:12.5px;vertical-align:top}
tbody tr:hover{background:color-mix(in srgb,var(--surf) 92%,var(--tx) 8%)}
.mono{font-variant-numeric:tabular-nums;color:var(--tx2);font-size:12px}.num{text-align:right;font-variant-numeric:tabular-nums}
.metal-cu{color:var(--cu);font-weight:600}.metal-al{color:var(--al);font-weight:600}.metal-st{color:var(--st);font-weight:600}
.gap{font-weight:700}.g-crit{color:var(--crit)}.g-warn{color:#a06a00}.g-ok{color:var(--good)}
.fl{font-size:10.5px;padding:2px 7px;border-radius:999px;white-space:nowrap}
.fl-placeholder{background:var(--critt);color:var(--crit)}.fl-review{background:var(--warnt);color:#a06a00}
.fl-norate{background:var(--grid);color:var(--tx2)}.fl-outlier{background:var(--critt);color:var(--crit)}.fl-ok{background:var(--goodt);color:var(--good)}
footer{margin-top:24px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><span class="bname">Dynalektric</span> <span class="btag">Commodity Price Intelligence - Item Detail</span></div>
<h1>Live item prices - aluminium, copper &amp; CRGO</h1>
<p class="sub">Every KGS-priced metal item in your master with today's benchmark attached. "Your rate" is the item-master default price; "Live market" is today's public benchmark for that metal; gap shows how far the market sits above (+) or below (-) your rate. Copper/aluminium benchmarks are raw metal - finished wire/strip carries a conversion premium on top.</p>
<p class="gen">Auto-refreshed daily. Last generated: {{GENERATED}} · {{N_TOTAL}} items · flags: {{N_PLACEHOLDER}} placeholder, {{N_NORATE}} no-rate, {{N_REVIEW}} review, {{N_OUTLIER}} outlier.</p>
<div class="cards">{{BENCH_CARDS}}</div>
<div class="flagbar">
 <span class="chip" data-flag="" onclick="setFlag('')">All <b id="cnt-all"></b></span>
 <span class="chip c-placeholder" data-flag="placeholder" onclick="setFlag('placeholder')">Placeholder / stale <b id="cnt-placeholder"></b></span>
 <span class="chip c-review" data-flag="review" onclick="setFlag('review')">Rate below market <b id="cnt-review"></b></span>
 <span class="chip c-norate" data-flag="norate" onclick="setFlag('norate')">No rate <b id="cnt-norate"></b></span>
 <span class="chip" data-flag="outlier" onclick="setFlag('outlier')">Outlier high <b id="cnt-outlier"></b></span>
 <span class="chip" data-flag="ok" onclick="setFlag('ok')">OK <b id="cnt-ok"></b></span>
</div>
<div class="controls">
 <input id="q" placeholder="Search code or item name..." oninput="render()">
 <select id="metal" onchange="render()"><option value="">All metals</option><option>Copper</option><option>Aluminium</option><option>CRGO steel</option></select>
 <span class="count" id="count"></span>
</div>
<table><thead><tr>
 <th onclick="sortBy('code')">Item code</th><th onclick="sortBy('name')">Item</th>
 <th onclick="sortBy('cat')">Cat</th><th onclick="sortBy('metal')">Metal</th><th>UOM</th>
 <th class="num" onclick="sortBy('yr')">Your rate &#8377;/kg</th><th class="num" onclick="sortBy('lm')">Live market &#8377;/kg</th>
 <th class="num" onclick="sortBy('gap')">Gap %</th><th onclick="sortBy('fk')">Flag</th></tr></thead><tbody id="tb"></tbody></table>
<footer>Benchmarks: LME copper cash (westmetall.com) · NALCO aluminium ingot · CRGO - indicative estimate only, no free daily feed. Gaps and flags recomputed each daily run against the latest fetched prices. <a href="./index.html" style="color:var(--al)">Public summary &rarr;</a></footer>
</div>
<script>
const DATA={{DATA}};
let sortKey='gap',sortDir=-1,flagFilter='';
function metalCls(m){return m==='Copper'?'cu':m==='Aluminium'?'al':'st'}
function gapCls(g){if(g===null)return'';const a=Math.abs(g);return a>=50?'g-crit':a>=15?'g-warn':'g-ok'}
function sortBy(k){if(sortKey===k)sortDir*=-1;else{sortKey=k;sortDir=1}render()}
function setFlag(f){flagFilter=f;document.querySelectorAll('.chip').forEach(c=>c.classList.toggle('on',c.dataset.flag===f));render()}
function counts(){const c={'':DATA.length,placeholder:0,review:0,norate:0,outlier:0,ok:0};DATA.forEach(d=>c[d.fk]++);
 document.getElementById('cnt-all').textContent=c[''];['placeholder','review','norate','outlier','ok'].forEach(k=>document.getElementById('cnt-'+k).textContent=c[k]);}
function render(){
 const q=document.getElementById('q').value.toLowerCase();
 const mf=document.getElementById('metal').value;
 let r=DATA.filter(d=>{
   if(mf&&d.metal!==mf)return false;
   if(flagFilter&&d.fk!==flagFilter)return false;
   if(q&&!(String(d.code).toLowerCase().includes(q)||d.name.toLowerCase().includes(q)))return false;
   return true;});
 r.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
   if(x===null)x=sortDir>0?1e9:-1e9;if(y===null)y=sortDir>0?1e9:-1e9;
   if(typeof x==='string')return x.localeCompare(y)*sortDir;return(x-y)*sortDir;});
 document.getElementById('count').textContent=r.length+' shown';
 document.getElementById('tb').innerHTML=r.slice(0,1500).map(d=>{
   const gap=d.gap===null?'<span class="fl fl-norate">n/a</span>':'<span class="gap '+gapCls(d.gap)+'">'+(d.gap>=0?'+':'−')+Math.abs(d.gap).toFixed(0)+'%</span>';
   return '<tr><td class="mono">'+d.code+'</td><td>'+d.name.replace(/</g,'&lt;')+'</td>'+
   '<td class="mono">'+d.cat+'</td><td class="metal-'+metalCls(d.metal)+'">'+d.metal+'</td>'+
   '<td class="mono">'+d.uom+'</td><td class="num mono">'+(d.yr?d.yr.toLocaleString('en-IN',{minimumFractionDigits:2}):'—')+'</td>'+
   '<td class="num mono">'+d.lm.toLocaleString('en-IN',{minimumFractionDigits:2})+'</td>'+
   '<td class="num">'+gap+'</td><td><span class="fl fl-'+d.fk+'">'+d.fl+'</span></td></tr>';}).join('');
}
counts();render();
</script></body></html>"""


if __name__ == "__main__":
    main()
