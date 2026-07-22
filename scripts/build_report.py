#!/usr/bin/env python3
"""
Builds a monthly "Raw Material Price Intelligence" briefing (CXO/management
style, like the Patil Group reference) for Dynalektric's base metals, from the
live pipeline data:  data/public_summary.json  +  data/price_history.json.

Output: docs/reports/<YYYY-MM>.html  (print-ready A4; export to PDF via a
headless-Chromium print, or open and Ctrl-P).

The numbers (movements, signals, projections, charts) are computed from the
stored history. The commentary/recommendations are template-driven from those
movements plus a short, dated market-context note — clearly separated from the
computed figures so the factual part is always reproducible.
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_month(m):
    y, mo = m.split("-")
    return f"{MON[int(mo)-1]} {y}"


def lin(ys):
    n = len(ys)
    xs = list(range(n))
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x*y for x, y in zip(xs, ys))
    sxx = sum(x*x for x in xs)
    den = (n*sxx - sx*sx) or 1
    b = (n*sxy - sx*sy) / den
    return (sy - b*sx)/n, b


def svg_line(points, color, w=520, h=170):
    """Static SVG line chart (no JS) for print."""
    if len(points) < 2:
        return f'<div class="nochart">Indicative only — no historical series yet.</div>'
    vals = [p["v"] for p in points]
    mn, mx = min(vals), max(vals)
    pad = (mx - mn) * 0.18 or 1
    mn -= pad; mx += pad
    L, R, T, B = 40, 12, 12, 26
    n = len(points)
    sx = lambda i: L + (i/(n-1)) * (w - L - R)
    sy = lambda v: T + (1 - (v-mn)/(mx-mn)) * (h - T - B)
    grid = ""
    for k in range(3):
        gv = mn + (mx-mn)*k/2
        yy = sy(gv)
        grid += f'<line x1="{L}" y1="{yy:.0f}" x2="{w-R}" y2="{yy:.0f}" stroke="#e1e0d9" stroke-width="1"/>'
        grid += f'<text x="{L-4}" y="{yy+3:.0f}" text-anchor="end" class="cax">₹{gv:.0f}</text>'
    path = " ".join(("M" if i == 0 else "L") + f"{sx(i):.1f} {sy(p['v']):.1f}" for i, p in enumerate(points))
    labels = (f'<text x="{sx(0):.0f}" y="{h-6}" class="cax">{fmt_month(points[0]["m"])}</text>'
              f'<text x="{sx(n-1):.0f}" y="{h-6}" text-anchor="end" class="cax">{fmt_month(points[-1]["m"])}</text>')
    dot = f'<circle cx="{sx(n-1):.1f}" cy="{sy(points[-1]["v"]):.1f}" r="3.5" fill="{color}"/>'
    return (f'<svg viewBox="0 0 {w} {h}" class="chart">{grid}'
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2.2" '
            f'stroke-linejoin="round" stroke-linecap="round"/>{dot}{labels}</svg>')


def signal(pct):
    a = abs(pct)
    if a >= 25:
        return ("ALERT", "alert")
    if a >= 8:
        return ("WATCH", "watch")
    return ("STABLE", "stable")


def main():
    with open(os.path.join(ROOT, "data", "public_summary.json")) as f:
        s = json.load(f)
    with open(os.path.join(ROOT, "data", "price_history.json")) as f:
        hist = json.load(f)

    month = sys.argv[1] if len(sys.argv) > 1 else datetime.now(timezone.utc).strftime("%Y-%m")
    color = {"Copper": "#c0532a", "Aluminium": "#2a6bbf", "CRGO steel": "#7a869a"}

    metals = {}
    for name, sd in hist["series"].items():
        pts = sd["points"]
        if not pts:
            continue
        cur = pts[-1]["v"]
        prev = pts[-2]["v"] if len(pts) > 1 else cur
        first = pts[0]["v"]
        mom = (cur - prev)/prev*100 if prev else 0
        span = (cur - first)/first*100 if first else 0
        span_label = f"{fmt_month(pts[0]['m'])}→now"
        proj = None
        if len(pts) >= 4:
            a, b = lin([p["v"] for p in pts[-6:]])
            proj = a + b*(len(pts)-1+3)
        sig = signal(span) if not sd.get("indicative") else ("MONITOR", "monitor")
        metals[name] = dict(cur=cur, mom=mom, span=span, span_label=span_label,
                            proj=proj, sig=sig, indicative=sd.get("indicative"),
                            src=sd.get("source", ""), svg=svg_line(pts, color.get(name, "#888")))

    # ---- narrative (template-driven from movements + dated market note) ----
    cu, al = metals.get("Copper"), metals.get("Aluminium")
    exec_summary = (
        f"Aluminium remains the dominant cost story for Dynalektric: NALCO ingot is up "
        f"<b>{al['span']:+.0f}%</b> over the last year to ₹{al['cur']:,.0f}/kg, a multi-year high. "
        f"Copper sits near record 2026 levels at ₹{cu['cur']:,.0f}/kg (LME cash, {cu['span']:+.1f}% since January) "
        f"on a persistent supply-deficit narrative. CRGO core-lamination steel has no live benchmark and "
        f"is tracked only on an indicative basis — a visibility gap on a top-three transformer cost."
    )
    why = [
        ("COPPER", "watch",
         f"LME cash near record highs through 2026 on institutional rotation and a structural refined-copper "
         f"deficit. Goldman Sachs expects a modest decline from record highs later in the year, but the supply "
         f"crunch keeps the floor elevated. For Dynalektric, copper-wound budgets set on legacy ERP standards "
         f"(~₹900/kg) now sit well below the ₹{cu['cur']:,.0f}/kg metal — a real exposure on copper-wound orders."),
        ("ALUMINIUM", "alert",
         f"NALCO ingot has rallied to a multi-year high, +{al['span']:.0f}% year-on-year, tracking LME strength "
         f"and firm domestic demand. NALCO guides that aluminium may ease into FY27 as alumina softens, so the "
         f"current level is likely a near-term peak zone rather than a new floor. Aluminium-conductor budgets on "
         f"old rates are the most behind of any metal."),
        ("CRGO STEEL", "monitor",
         "No free daily benchmark exists (SM Steels/IndiaMART are quote-on-request; SteelMint is paid). The ₹230/kg "
         "shown is indicative. This is a top-three transformer input running with zero price visibility — "
         "establishing a monthly supplier-quote or a paid feed is the single highest-value data gap to close."),
    ]
    recos = [
        ("HIGH", "Aluminium",
         f"Update budgeted aluminium rates toward ₹{al['cur']:,.0f}/kg (basic). Secure Q2–Q3 volumes at or near "
         f"current levels before any FY27 easing; do not budget forward jobs on pre-2026 rates."),
        ("HIGH", "Copper",
         f"Refresh copper standards to today's ₹{cu['cur']:,.0f}/kg metal basis and add forward cover on large "
         f"copper-wound orders — supply-deficit risk skews the near term upward."),
        ("MEDIUM", "CRGO steel",
         "Establish a price feed (monthly supplier quote or a paid index). Until then treat the ₹230/kg as "
         "directional only and validate every CRGO-heavy quote against a live number."),
    ]
    outlook = [
        ("Bear case", "bear",
         "Copper eases from record highs (Goldman view); aluminium softens as NALCO's FY27 easing begins. "
         "Blended metal input cost −3 to −5% over the quarter."),
        ("Base case (most likely)", "base",
         f"Copper holds ~₹{cu['cur']:,.0f}/kg ±3%; aluminium holds ₹{al['cur']:,.0f}/kg, flat-to-slightly-up. "
         "Input costs broadly stable at today's elevated level."),
        ("Bull case", "bull",
         "Copper's supply deficit deepens and breaks higher; aluminium extends its rally. Blended input cost "
         "+5 to +8%, pressuring any fixed-price order booked on old rates."),
    ]

    generated = datetime.now(timezone.utc).strftime("%d %b %Y")
    rows_key = ""
    for name, m in metals.items():
        sig_txt, sig_cls = m["sig"]
        proj = f"₹{m['proj']:,.0f}" if m["proj"] else "—"
        rows_key += (f'<tr><td class="mname">{name}</td>'
                     f'<td class="rnum">₹{m["cur"]:,.0f}/kg</td>'
                     f'<td class="rnum">{m["mom"]:+.1f}%</td>'
                     f'<td class="rnum">{m["span"]:+.0f}%<div class="sub">{m["span_label"]}</div></td>'
                     f'<td class="rnum">{proj}<div class="sub">naïve proj.</div></td>'
                     f'<td><span class="sig sig-{sig_cls}">{sig_txt}</span></td></tr>')

    why_html = "".join(
        f'<div class="wrow"><div class="wtag wtag-{c}">{t}</div><div class="wtxt">{x}</div></div>'
        for t, c, x in why)
    reco_html = "".join(
        f'<tr><td><span class="pri pri-{p.lower().split()[0]}">{p}</span></td>'
        f'<td class="mname">{mat}</td><td class="ract">{act}</td></tr>'
        for p, mat, act in recos)
    outlook_html = "".join(
        f'<div class="ocard oc-{c}"><div class="oh">{t}</div><div class="ot">{x}</div></div>'
        for t, c, x in outlook)
    charts_html = "".join(
        f'<div class="chartblock"><div class="ch-t"><span class="cdot" style="background:{color.get(n,"#888")}"></span>'
        f'{n} — ₹{m["cur"]:,.0f}/kg</div><div class="ch-s">{m["src"]}</div>{m["svg"]}</div>'
        for n, m in metals.items())

    html = REPORT.replace("{{MONTH}}", fmt_month(month)).replace("{{GENERATED}}", generated) \
        .replace("{{EXEC}}", exec_summary).replace("{{ROWS_KEY}}", rows_key) \
        .replace("{{WHY}}", why_html).replace("{{RECOS}}", reco_html) \
        .replace("{{OUTLOOK}}", outlook_html).replace("{{CHARTS}}", charts_html)

    outdir = os.path.join(ROOT, "docs", "reports")
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f"{month}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {out}")


REPORT = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Dynalektric — RM Price Intelligence — {{MONTH}}</title>
<style>
@page{size:A4;margin:14mm}
*{box-sizing:border-box}
body{margin:0;font-family:"Segoe UI",system-ui,Arial,sans-serif;color:#1a1a1a;font-size:12px;line-height:1.5}
.page{max-width:820px;margin:0 auto;padding:22px}
.hdrbar{background:#6b1f1f;color:#fff;padding:6px 16px;font-size:11px;letter-spacing:.5px;font-weight:600;display:flex;justify-content:space-between}
.hdrbar .conf{color:#e8c9a0}
.hero{background:#6b1f1f;color:#fff;padding:26px 20px;text-align:center;margin-bottom:20px}
.hero h1{margin:0;font-size:26px;letter-spacing:.5px}.hero h1 span{color:#e8b96a}
.hero .meta{font-size:12px;color:#e8c9a0;margin-top:8px}
h2{font-size:14px;color:#6b1f1f;border-bottom:2px solid #e8b96a;padding-bottom:4px;margin:22px 0 12px;letter-spacing:.3px}
.exec{font-size:12.5px;line-height:1.65}
table{width:100%;border-collapse:collapse;margin:6px 0}
th{background:#f3ede4;color:#6b1f1f;font-size:10.5px;text-transform:uppercase;letter-spacing:.04em;text-align:left;padding:7px 9px;border-bottom:2px solid #e8b96a}
td{padding:7px 9px;border-bottom:1px solid #eee;font-size:12px;vertical-align:top}
.mname{font-weight:650}.rnum{text-align:right;font-variant-numeric:tabular-nums;font-weight:600}
.sub{font-size:9.5px;color:#999;font-weight:400}
.sig{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;white-space:nowrap}
.sig-alert{background:#fbe4e4;color:#b12a2a}.sig-watch{background:#fff3d9;color:#a06a00}.sig-stable{background:#e6f5e6;color:#227a22}.sig-monitor{background:#eee;color:#666}
.wrow{display:flex;gap:12px;margin-bottom:10px}
.wtag{flex:0 0 92px;font-size:10px;font-weight:700;color:#fff;padding:6px 8px;border-radius:5px;text-align:center;height:fit-content}
.wtag-alert{background:#b12a2a}.wtag-watch{background:#c98500}.wtag-monitor{background:#7a869a}
.wtxt{font-size:12px;line-height:1.55}
.pri{font-size:10px;font-weight:700;padding:2px 8px;border-radius:4px;color:#fff}
.pri-high{background:#b12a2a}.pri-medium{background:#c98500}.pri-low{background:#227a22}
.ract{font-size:11.5px;line-height:1.5}
.outlook{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:6px}
.ocard{border:1px solid #e6e0d5;border-radius:8px;padding:11px 12px}
.oc-bear{background:#fbf0f0}.oc-base{background:#fdf7ea}.oc-bull{background:#eef6ee}
.oh{font-size:11px;font-weight:700;color:#6b1f1f;margin-bottom:5px}.ot{font-size:11px;line-height:1.5}
.charts{display:grid;grid-template-columns:1fr;gap:14px;margin-top:6px}
.chartblock{border:1px solid #eee;border-radius:8px;padding:10px 12px}
.ch-t{font-size:12.5px;font-weight:650;display:flex;align-items:center;gap:7px}
.cdot{width:9px;height:9px;border-radius:2px;display:inline-block}
.ch-s{font-size:10px;color:#999;margin:2px 0 4px}
.chart{width:100%;height:auto}.cax{fill:#999;font-size:9px}
.nochart{font-size:11px;color:#999;font-style:italic;padding:14px 0}
.foot{margin-top:24px;border-top:1px solid #ddd;padding-top:10px;font-size:9.5px;color:#888;line-height:1.6}
</style></head><body>
<div class="hdrbar"><span>RAW MATERIAL PRICE INTELLIGENCE</span><span class="conf">CONFIDENTIAL · Dynalektric</span></div>
<div class="hero"><h1>COMMODITY PRICE<br><span>TREND ANALYSIS</span></h1>
<div class="meta">{{MONTH}} · Management Briefing · Generated {{GENERATED}}<br>Metals: Copper · Aluminium · CRGO Steel · Source: LME, NALCO (live pipeline)</div></div>
<div class="page">
<h2>Executive Summary</h2>
<p class="exec">{{EXEC}}</p>

<h2>Key Movements</h2>
<table><thead><tr><th>Metal</th><th style="text-align:right">Current</th><th style="text-align:right">MoM</th><th style="text-align:right">Trend</th><th style="text-align:right">3-mo proj.</th><th>Signal</th></tr></thead>
<tbody>{{ROWS_KEY}}</tbody></table>
<div class="sub" style="margin-top:4px">Signal on trend change: ALERT ≥25% · WATCH ≥8% · STABLE &lt;8% · MONITOR = no live benchmark. "Naïve proj." is a mechanical linear extrapolation, not a market forecast.</div>

<h2>Why Prices Are Moving</h2>
{{WHY}}

<h2>Procurement Recommendations</h2>
<table><thead><tr><th>Priority</th><th>Metal</th><th>Action</th></tr></thead><tbody>{{RECOS}}</tbody></table>

<h2>Outlook — Next Quarter</h2>
<div class="outlook">{{OUTLOOK}}</div>

<h2>Price Trend (last year &amp; current year)</h2>
<div class="charts">{{CHARTS}}</div>

<div class="foot">
Prices: LME copper cash (westmetall.com) converted at live USD/INR · NALCO aluminium ingot circular (nalcoindia.com), history via snalco.com rebased to basic-price basis · CRGO indicative (no free feed).
Market context: Goldman Sachs (copper 2026 outlook), Business Standard / AlCircle (NALCO/aluminium). Figures computed from Dynalektric's price-history store; commentary is template-driven from those movements plus dated market notes. Auto-generated — not a contractual price-variation calculation.
</div>
</div></body></html>"""


if __name__ == "__main__":
    main()
