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
    try:
        with open(os.path.join(ROOT, "data", "price_history.json")) as f:
            history = json.load(f)
    except FileNotFoundError:
        history = {"series": {}, "meta": {}}
    try:
        with open(os.path.join(ROOT, "data", "buy_signals.json")) as f:
            signals = json.load(f)
    except FileNotFoundError:
        signals = {"signals": {}}

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

    # Short, per-row source label naming where each metal's live price comes from.
    SRC_SHORT = {
        "Copper": "LME cash · westmetall.com",
        "Aluminium": "NALCO circular",
        "CRGO steel": "Indicative — no live feed",
        "Stainless Steel": "—",
        "Mild Steel": "—",
    }

    rows = []
    for it in doc["items"]:
        metal = detect_metal(it.get("name"), it.get("cat"))
        b = bench.get(metal) if metal else None
        has_price = bool(b and b["price"] is not None)
        rows.append({"code": it["code"], "name": it["name"], "cat": it["cat"],
                     "metal": metal or "—", "uom": it["uom"],
                     "erp": it.get("rate"),
                     "lm": (b["price"] if b else None),
                     "src": SRC_SHORT.get(metal, "—") if has_price else "—"})

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

    # --- buy-timing signal cards ---
    SIG_ICON = {"BUY": "▲", "NEUTRAL": "▬", "HOLD": "▼", "NO SIGNAL": "○"}
    sig_cards = ""
    for name, sg in signals.get("signals", {}).items():
        cls = sg.get("cls", "none")
        pos = sg.get("pos")
        bar = ""
        if pos is not None:
            bar = (f'<div class="posbar"><div class="posfill" style="left:{pos*100:.0f}%"></div>'
                   f'<span class="poslo">₹{sg.get("lo",0):,.0f}</span>'
                   f'<span class="poshi">₹{sg.get("hi",0):,.0f}</span></div>')
        cur = f'₹{sg["cur"]:,.0f}/kg' if sg.get("cur") is not None else "—"
        sig_cards += (
            f'<div class="sigcard sc-{cls}">'
            f'<div class="sighead"><span class="sigbadge sb-{cls}">{SIG_ICON.get(sg["signal"],"")} {sg["signal"]}</span>'
            f'<span class="signame">{name}</span><span class="sigcur">{cur}</span></div>'
            f'<div class="sigline">{sg.get("headline","")}</div>'
            f'{bar}'
            f'<div class="sigreason">{sg.get("reason","")}</div>'
            f'<div class="sigaction"><b>Action:</b> {sg.get("action","")}</div>'
            f'</div>')
    sig_generated = signals.get("generated_at_utc", "")
    sig_disclaimer = signals.get("disclaimer", "")

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    html = (TEMPLATE
            .replace("{{SIGNAL_CARDS}}", sig_cards)
            .replace("{{SIG_DISCLAIMER}}", sig_disclaimer)
            .replace("{{BENCH_CARDS}}", bench_cards)
            .replace("{{HISTORY}}", json.dumps(history, ensure_ascii=False))
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
.signals{margin-bottom:24px}
.signals h2,.trends h2{font-size:15px;margin:0 0 3px}.signals .h2sub,.trends .h2sub{font-size:12px;color:var(--mut);margin:0 0 12px}
.trends{margin-bottom:24px}
.sigrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
@media(max-width:820px){.sigrid{grid-template-columns:1fr}}
.sigcard{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:14px 15px;border-left:4px solid var(--mut)}
.sc-buy{border-left-color:var(--good)}.sc-hold{border-left-color:var(--crit)}.sc-neutral{border-left-color:var(--warn)}.sc-none{border-left-color:var(--mut)}
.sighead{display:flex;align-items:center;gap:8px;margin-bottom:7px}
.sigbadge{font-size:11px;font-weight:800;padding:3px 9px;border-radius:6px;letter-spacing:.3px}
.sb-buy{background:var(--good);color:#fff}.sb-hold{background:var(--crit);color:#fff}.sb-neutral{background:var(--warn);color:#3a2c00}.sb-none{background:var(--grid);color:var(--tx2)}
.signame{font-weight:650;font-size:13.5px}.sigcur{margin-left:auto;font-weight:650;font-variant-numeric:tabular-nums;font-size:13px}
.sigline{font-size:12.5px;font-weight:600;margin-bottom:9px}
.posbar{position:relative;height:6px;background:linear-gradient(90deg,var(--goodt),var(--warnt),var(--critt));border-radius:4px;margin:16px 0 14px}
.posfill{position:absolute;top:-3px;width:3px;height:12px;background:var(--tx);border-radius:2px;transform:translateX(-50%)}
.poslo,.poshi{position:absolute;top:8px;font-size:9.5px;color:var(--mut)}.poslo{left:0}.poshi{right:0}
.sigreason{font-size:11.5px;color:var(--tx2);line-height:1.5;margin-bottom:8px}
.sigaction{font-size:11.5px;line-height:1.45}.sigaction b{color:var(--tx)}
.chartgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
@media(max-width:820px){.chartgrid{grid-template-columns:1fr}}
.chartcard{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:13px 14px 10px;position:relative}
.chartcard .ctitle{font-size:12.5px;font-weight:650;display:flex;align-items:center;gap:6px}
.chartcard .cdot{width:9px;height:9px;border-radius:2px;display:inline-block}
.chartcard .cnow{font-size:20px;font-weight:650;margin-top:2px}.chartcard .cnow small{font-size:11px;color:var(--tx2);font-weight:500}
.chartcard .cchg{font-size:11.5px;margin-left:6px;font-weight:600}.up{color:#0ca30c}.down{color:#d03b3b}
.chartcard .csrc{font-size:10.5px;color:var(--mut);margin-top:2px}
.chartcard svg{display:block;width:100%;height:auto;margin-top:6px;overflow:visible}
.chartcard .axlbl{fill:var(--mut);font-size:9px}
.chartcard .tip{position:absolute;pointer-events:none;background:var(--tx);color:var(--bg);font-size:11px;padding:3px 7px;border-radius:5px;opacity:0;transform:translate(-50%,-130%);white-space:nowrap;transition:opacity .08s}
.chartcard .indbadge{font-size:9px;font-weight:700;color:#a06a00;border:1px solid #fab219;border-radius:4px;padding:1px 4px;margin-left:auto}
.forecast-note{font-size:11px;color:var(--mut);margin-top:8px}
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
<section class="signals">
 <h2>Buy-timing monitor — should we buy now?</h2>
 <p class="h2sub">Each metal read against its own trailing-12-month range. BUY = historically low (stock-up window); HOLD = near its high (wait for a dip). {{SIG_DISCLAIMER}}</p>
 <div class="sigrid">{{SIGNAL_CARDS}}</div>
</section>
<section class="trends">
 <h2>Price trend — last year &amp; current year</h2>
 <p class="h2sub">Monthly ₹/kg per base metal. Solid = actual; dashed = naive linear projection (next 3 months), not a market forecast. Series self-builds daily.</p>
 <div class="chartgrid" id="charts"></div>
 <p class="forecast-note">Copper = LME cash × USD/INR. Aluminium = NALCO ingot (rebased to basic-price basis; pre-Jul-2026 points are rebased estimates). CRGO = indicative, no historical feed.</p>
</section>
<div class="controls">
 <input id="q" placeholder="Search code or item name..." oninput="render()">
 <select id="metal" onchange="render()"><option value="">All metals</option><option>Copper</option><option>Aluminium</option><option>CRGO steel</option><option>Stainless Steel</option><option>Mild Steel</option></select>
 <span class="count" id="count"></span>
</div>
<table><thead><tr>
 <th onclick="sortBy('code')">Item code</th><th onclick="sortBy('name')">Item</th>
 <th onclick="sortBy('cat')">Cat</th><th onclick="sortBy('metal')">Base metal</th><th>UOM</th>
 <th class="num" onclick="sortBy('erp')">ERP rate &#8377;/kg</th>
 <th class="num" onclick="sortBy('lm')">Live market &#8377;/kg</th>
 <th onclick="sortBy('src')">Live price source</th></tr></thead><tbody id="tb"></tbody></table>
<footer>Benchmarks: LME copper cash (westmetall.com) converted via live USD/INR · NALCO aluminium ingot · CRGO — indicative estimate, no free daily feed. Prices refresh each daily run. <a href="./index.html" style="color:var(--al)">Public summary &rarr;</a> · <a href="./consumption.html" style="color:var(--al)">Consumption &amp; spend &rarr;</a> · <a href="./demand.html" style="color:var(--al)">Forward demand &rarr;</a></footer>
</div>
<script>
const HISTORY={{HISTORY}};
const MCOLOR={Copper:'var(--cu)',Aluminium:'var(--al)','CRGO steel':'var(--st)'};
const MON=['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
function fmtM(m){const a=m.split('-');return MON[+a[1]-1]+" '"+a[0].slice(2)}
function addMonths(m,k){let a=m.split('-'),y=+a[0],mo=+a[1]+k;y+=Math.floor((mo-1)/12);mo=((mo-1)%12+12)%12+1;return y+'-'+String(mo).padStart(2,'0')}
function lin(pts){const n=pts.length;let sx=0,sy=0,sxy=0,sxx=0;pts.forEach(p=>{sx+=p.x;sy+=p.y;sxy+=p.x*p.y;sxx+=p.x*p.x});const den=(n*sxx-sx*sx)||1;const b=(n*sxy-sx*sy)/den;return{a:(sy-b*sx)/n,b:b}}
function drawCharts(){
 const grid=document.getElementById('charts');if(!grid)return;grid.innerHTML='';
 Object.entries(HISTORY.series||{}).forEach(([metal,s])=>{
  const pts=(s.points||[]).slice();const color=MCOLOR[metal]||'var(--st)';
  const last=pts.length?pts[pts.length-1].v:null;
  const card=document.createElement('div');card.className='chartcard';
  let chg='';if(pts.length>=2){const pv=pts[pts.length-2].v;const d=(last-pv)/pv*100;chg='<span class="cchg '+(d>=0?'up':'down')+'">'+(d>=0?'▲':'▼')+' '+Math.abs(d).toFixed(1)+'%</span>';}
  const ind=s.indicative?'<span class="indbadge">indicative</span>':'';
  card.innerHTML='<div class="ctitle"><span class="cdot" style="background:'+color+'"></span>'+metal+ind+'</div>'+
   '<div class="cnow">₹'+(last!=null?last.toLocaleString('en-IN'):'—')+' <small>/kg</small>'+chg+'</div>'+
   '<div class="csrc">'+(s.source||'')+'</div>';
  if(pts.length<2){card.innerHTML+='<div class="csrc" style="margin-top:10px">Trend builds as daily data collects.</div>';grid.appendChild(card);return;}
  const idx=pts.map((p,i)=>({x:i,y:p.v}));const fit=lin(idx.slice(-Math.min(6,idx.length)));
  const fc=[];for(let k=1;k<=3;k++){const x=pts.length-1+k;fc.push({m:addMonths(pts[pts.length-1].m,k),v:Math.max(0,fit.a+fit.b*x),x:x})}
  const all=pts.map(p=>p.v).concat(fc.map(p=>p.v));let mn=Math.min.apply(null,all),mx=Math.max.apply(null,all);const pad=(mx-mn)*0.18||1;mn-=pad;mx+=pad;
  const W=320,H=150,L=8,R=8,T=8,Bt=18,totalX=pts.length-1+3;
  const sx=x=>L+(x/totalX)*(W-L-R),sy=v=>T+(1-(v-mn)/(mx-mn))*(H-T-Bt);
  let g='';for(let k=0;k<=2;k++){const gv=mn+(mx-mn)*k/2,yy=sy(gv);g+='<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="var(--grid)" stroke-width="1"/><text x="'+L+'" y="'+(yy-2)+'" class="axlbl">₹'+Math.round(gv)+'</text>'}
  const ap=pts.map((p,i)=>(i?'L':'M')+sx(i).toFixed(1)+' '+sy(p.v).toFixed(1)).join(' ');
  const fp='M'+sx(pts.length-1).toFixed(1)+' '+sy(last).toFixed(1)+' '+fc.map(p=>'L'+sx(p.x).toFixed(1)+' '+sy(p.v).toFixed(1)).join(' ');
  const xl='<text x="'+sx(0)+'" y="'+(H-4)+'" class="axlbl">'+fmtM(pts[0].m)+'</text>'+
   '<text x="'+sx(pts.length-1)+'" y="'+(H-4)+'" class="axlbl" text-anchor="middle">'+fmtM(pts[pts.length-1].m)+'</text>'+
   '<text x="'+sx(totalX)+'" y="'+(H-4)+'" class="axlbl" text-anchor="end">'+fmtM(fc[fc.length-1].m)+'</text>';
  const dots=pts.map((p,i)=>'<circle cx="'+sx(i).toFixed(1)+'" cy="'+sy(p.v).toFixed(1)+'" r="10" fill="transparent" data-m="'+p.m+'" data-v="'+p.v+'" class="hv"/>').join('')+
   fc.map(p=>'<circle cx="'+sx(p.x).toFixed(1)+'" cy="'+sy(p.v).toFixed(1)+'" r="10" fill="transparent" data-m="'+p.m+'" data-v="'+p.v.toFixed(0)+'" data-f="1" class="hv"/>').join('');
  card.innerHTML+='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="'+metal+' price trend">'+g+
   '<path d="'+fp+'" fill="none" stroke="'+color+'" stroke-width="2" stroke-dasharray="4 3" opacity="0.5"/>'+
   '<path d="'+ap+'" fill="none" stroke="'+color+'" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'+
   '<circle cx="'+sx(pts.length-1).toFixed(1)+'" cy="'+sy(last).toFixed(1)+'" r="3" fill="'+color+'"/>'+xl+dots+'</svg><div class="tip"></div>';
  grid.appendChild(card);
  const tip=card.querySelector('.tip');
  card.querySelectorAll('.hv').forEach(c=>{
   c.addEventListener('mouseenter',()=>{const f=c.getAttribute('data-f');tip.textContent=fmtM(c.getAttribute('data-m'))+': ₹'+(+c.getAttribute('data-v')).toLocaleString('en-IN')+(f?' (proj.)':'');tip.style.opacity=1});
   c.addEventListener('mousemove',e=>{const r=card.getBoundingClientRect();tip.style.left=(e.clientX-r.left)+'px';tip.style.top=(e.clientY-r.top)+'px'});
   c.addEventListener('mouseleave',()=>tip.style.opacity=0);
  });
 });
}
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
   '<td class="lm">'+lm+'</td><td class="mono srccell">'+d.src+'</td></tr>';}).join('');
}
drawCharts();
render();
</script></body></html>"""


if __name__ == "__main__":
    main()
