#!/usr/bin/env python3
"""
Renders docs/items.html — a plain reference table showing TODAY'S LIVE
COMMODITY PRICE beside each ERP item. Nothing else.

Design rules (deliberate — do not reintroduce):
  * The "Live Market ₹/kg" column shows ONLY today's benchmark price for the
    item's detected base metal. The SAME benchmark value appears for every
    item of that metal. It is a live commodity reference, NOT an estimated
    selling/purchase price.
  * The base metal is detected from the item's category or description — never
    from the ERP item code, and never mapped per-item.
  * No gap %, no flags, no premiums, no conversion/processing/manufacturing
    cost, no future-pricing logic of any kind. If a benchmark isn't configured
    for a metal, the cell is left blank rather than estimated.

Runs in the daily workflow AFTER fetch_public_prices.py so the benchmark
prices refresh every day from the freshly-fetched aluminium/copper prices.
The item list itself changes only when data/items.json is regenerated from a
new master export.
"""
import json
import os
import re
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CARD_CLS = {"Copper": "cu", "Aluminium": "al", "CRGO steel": "st",
            "Stainless Steel": "ss", "Mild Steel": "ms"}


def detect_metal(name, cat):
    """Detect base metal from category first, then description. Never the code."""
    cat = (cat or "").upper()
    if cat.startswith("CU"):
        return "Copper"
    if cat.startswith("AL"):
        return "Aluminium"
    if cat == "CC":
        return "CRGO steel"
    n = (name or "").lower()
    if re.search(r"\bcrgo\b|grain[- ]oriented|core lamination", n):
        return "CRGO steel"
    if re.search(r"\bcopper\b|\bcu\b|electrolytic tough pitch|\betp\b", n):
        return "Copper"
    if re.search(r"\balumin", n):
        return "Aluminium"
    if re.search(r"stainless|\bss\s?30[0-9]\b|\bss\b", n):
        return "Stainless Steel"
    if re.search(r"\bmild steel\b|\bms\b|\bm\.s\.", n):
        return "Mild Steel"
    return None


def main():
    with open(os.path.join(ROOT, "data", "items.json")) as f:
        doc = json.load(f)
    with open(os.path.join(ROOT, "data", "public_summary.json")) as f:
        s = json.load(f)

    # Configured live benchmark sources, keyed by detected base metal.
    # A metal with no free/configured source maps to None -> blank cell.
    bench = {
        "Copper": {"price": s["copper"]["price_per_kg"],
                   "src": s["copper"]["source"], "asof": s["copper"]["settlement_date"],
                   "indicative": False},
        "Aluminium": {"price": s["aluminium"]["price_per_kg"],
                      "src": s["aluminium"]["source"], "asof": s["aluminium"]["effective_date"],
                      "indicative": False},
        "CRGO steel": {"price": s.get("crgo_steel", {}).get("price_per_kg"),
                       "src": s.get("crgo_steel", {}).get("source", "indicative"),
                       "asof": s.get("crgo_steel", {}).get("effective_date", ""),
                       "indicative": True},
        # No free daily benchmark configured yet for these — left blank, not estimated.
        "Stainless Steel": {"price": None, "src": "no benchmark configured", "asof": "", "indicative": False},
        "Mild Steel": {"price": None, "src": "no benchmark configured", "asof": "", "indicative": False},
    }

    rows = []
    for it in doc["items"]:
        metal = detect_metal(it.get("name"), it.get("cat"))
        b = bench.get(metal) if metal else None
        rows.append({"code": it["code"], "name": it["name"], "cat": it["cat"],
                     "metal": metal or "—", "uom": it["uom"],
                     "erp": it.get("rate"),
                     "lm": (b["price"] if b else None)})

    # Only render benchmark cards for metals that actually appear and have a price.
    present = {}
    for r in rows:
        if r["metal"] in bench and bench[r["metal"]]["price"] is not None:
            present[r["metal"]] = bench[r["metal"]]
    bench_cards = ""
    for m, b in present.items():
        tier = '<span class="tag-ind">indicative</span>' if b["indicative"] else ""
        bench_cards += (
            f'<div class="card {CARD_CLS.get(m,"st")}"><div class="accent"></div>'
            f'<div class="label"><span class="swatch"></span>{m} {tier}</div>'
            f'<div class="value">₹{b["price"]:,.2f} <small>/ kg</small></div>'
            f'<div class="meta">{b["src"]} · {b["asof"]}</div></div>')

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (TEMPLATE
            .replace("{{BENCH_CARDS}}", bench_cards)
            .replace("{{DATA}}", json.dumps(rows, ensure_ascii=False))
            .replace("{{GENERATED}}", generated)
            .replace("{{N_TOTAL}}", str(len(rows))))

    out = os.path.join(ROOT, "docs", "items.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out}: {len(rows)} items (live market price only, no gap/flags)")


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dynalektric - Live Commodity Price by Item</title>
<style>
:root{color-scheme:light dark;--bg:#f9f9f7;--surf:#fff;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.10);--grid:#e6e5df;
--cu:#eb6834;--al:#2a78d6;--st:#7a869a;--ss:#5a8f7b;--ms:#9a7b4f;}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--mut:#898781;--bd:rgba(255,255,255,.12);--grid:#2c2c2a;}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand .bname{font-weight:700;font-size:15px}.brand .btag{font-size:12px;color:var(--mut)}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13.5px;margin:0 0 8px;max-width:860px;line-height:1.5}
.gen{font-size:11.5px;color:var(--mut);margin:0 0 18px}
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-bottom:18px}
@media(max-width:760px){.cards{grid-template-columns:1fr}}
.card{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:15px 16px;position:relative;overflow:hidden}
.card .accent{position:absolute;left:0;top:0;bottom:0;width:4px}.card.cu .accent{background:var(--cu)}.card.al .accent{background:var(--al)}.card.st .accent{background:var(--st)}.card.ss .accent{background:var(--ss)}.card.ms .accent{background:var(--ms)}
.card .label{font-size:12px;color:var(--mut);margin-bottom:6px;display:flex;align-items:center;gap:6px}
.card .swatch{width:8px;height:8px;border-radius:2px}.card.cu .swatch{background:var(--cu)}.card.al .swatch{background:var(--al)}.card.st .swatch{background:var(--st)}
.card .value{font-size:24px;font-weight:650}.card .value small{font-size:12px;color:var(--tx2);font-weight:500}
.card .meta{font-size:11px;color:var(--mut);margin-top:5px}
.tag-ind{font-size:9.5px;font-weight:700;color:#a06a00;border:1px solid #fab219;border-radius:4px;padding:1px 5px;margin-left:4px}
.controls{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.controls input,.controls select{font-size:13px;padding:7px 10px;border:1px solid var(--bd);border-radius:8px;background:var(--surf);color:var(--tx)}
.controls input{min-width:230px}.count{font-size:12px;color:var(--mut);margin-left:auto}
table{width:100%;border-collapse:collapse;background:var(--surf);border:1px solid var(--bd);border-radius:12px;overflow:hidden}
thead th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:600;padding:9px 10px;border-bottom:1px solid var(--grid);position:sticky;top:0;background:var(--surf);cursor:pointer;white-space:nowrap}
thead th.num{text-align:right}tbody td{padding:7px 10px;border-bottom:1px solid var(--grid);font-size:12.5px;vertical-align:top}
tbody tr:hover{background:color-mix(in srgb,var(--surf) 92%,var(--tx) 8%)}
.mono{font-variant-numeric:tabular-nums;color:var(--tx2);font-size:12px}.num{text-align:right;font-variant-numeric:tabular-nums}
.lm{text-align:right;font-variant-numeric:tabular-nums;font-weight:700;color:var(--tx)}
.metal-cu{color:var(--cu);font-weight:600}.metal-al{color:var(--al);font-weight:600}.metal-st{color:var(--st);font-weight:600}.metal-ss{color:var(--ss);font-weight:600}.metal-ms{color:var(--ms);font-weight:600}
.na{color:var(--mut)}
footer{margin-top:24px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><span class="bname">Dynalektric</span> <span class="btag">Live Commodity Price by Item</span></div>
<h1>Today's live commodity price beside each item</h1>
<p class="sub">The <b>Live Market ₹/kg</b> column shows today's benchmark price for each item's base metal — the same value for every item of a given metal. It is a live commodity reference only, not an estimated selling or purchase price. No gap, premium, or processing cost is calculated. "ERP rate" is your item-master price, shown as-is.</p>
<p class="gen">Auto-refreshed daily (9:00 AM IST). Last generated: {{GENERATED}} · {{N_TOTAL}} items.</p>
<div class="cards">{{BENCH_CARDS}}</div>
<div class="controls">
 <input id="q" placeholder="Search code or item name..." oninput="render()">
 <select id="metal" onchange="render()"><option value="">All metals</option><option>Copper</option><option>Aluminium</option><option>CRGO steel</option><option>Stainless Steel</option><option>Mild Steel</option></select>
 <span class="count" id="count"></span>
</div>
<table><thead><tr>
 <th onclick="sortBy('code')">Item code</th><th onclick="sortBy('name')">Item</th>
 <th onclick="sortBy('cat')">Cat</th><th onclick="sortBy('metal')">Base metal</th><th>UOM</th>
 <th class="num" onclick="sortBy('erp')">ERP rate &#8377;/kg</th>
 <th class="num" onclick="sortBy('lm')">Live market &#8377;/kg</th></tr></thead><tbody id="tb"></tbody></table>
<footer>Benchmarks: LME copper cash (westmetall.com) converted via live USD/INR · NALCO aluminium ingot · CRGO — indicative estimate, no free daily feed. Prices refresh each daily run. <a href="./index.html" style="color:var(--al)">Public summary &rarr;</a></footer>
</div>
<script>
const DATA={{DATA}};
let sortKey='metal',sortDir=1;
function metalCls(m){return m==='Copper'?'cu':m==='Aluminium'?'al':m==='CRGO steel'?'st':m==='Stainless Steel'?'ss':m==='Mild Steel'?'ms':''}
function sortBy(k){if(sortKey===k)sortDir*=-1;else{sortKey=k;sortDir=1}render()}
function render(){
 const q=document.getElementById('q').value.toLowerCase();
 const mf=document.getElementById('metal').value;
 let r=DATA.filter(d=>{
   if(mf&&d.metal!==mf)return false;
   if(q&&!(String(d.code).toLowerCase().includes(q)||d.name.toLowerCase().includes(q)))return false;
   return true;});
 r.sort((a,b)=>{let x=a[sortKey],y=b[sortKey];
   if(x===null)x=sortDir>0?1e12:-1e12;if(y===null)y=sortDir>0?1e12:-1e12;
   if(typeof x==='string')return x.localeCompare(y)*sortDir;return(x-y)*sortDir;});
 document.getElementById('count').textContent=r.length+' of '+DATA.length+' items';
 document.getElementById('tb').innerHTML=r.slice(0,1500).map(d=>{
   const lm=d.lm===null?'<span class="na">—</span>':d.lm.toLocaleString('en-IN',{minimumFractionDigits:2});
   const mc=metalCls(d.metal);
   return '<tr><td class="mono">'+d.code+'</td><td>'+d.name.replace(/</g,'&lt;')+'</td>'+
   '<td class="mono">'+d.cat+'</td><td class="'+(mc?'metal-'+mc:'na')+'">'+d.metal+'</td>'+
   '<td class="mono">'+d.uom+'</td><td class="num mono">'+(d.erp?d.erp.toLocaleString('en-IN',{minimumFractionDigits:2}):'—')+'</td>'+
   '<td class="lm">'+lm+'</td></tr>';}).join('');
}
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
