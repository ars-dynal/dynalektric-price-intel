#!/usr/bin/env python3
"""
Renders docs/history.html — multi-year price rise & fall for every benchmark
series in data/price_history.json: one chart per series plus a per-year
summary (low / high / average / year-end and year-over-year change).

Runs in the daily workflow after update_history.py, and after the one-shot
backfill. Pure static SVG — same free-hosting approach as every other page.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "price_history.json")
OUT = os.path.join(ROOT, "docs", "history.html")

COLORS = {"Copper": "#c65a1e", "Aluminium": "#2a78d6",
          "Aluminium conductor (Hindalco P0610)": "#7048b5",
          "CRGO steel": "#5a6472", "Mild Steel": "#7a6a4f", "Stainless Steel": "#3e7d5a"}


def chart(points, color, w=860, h=210):
    if len(points) < 2:
        return '<p class="mut">Not enough points yet — this series builds forward automatically.</p>'
    vals = [p["v"] for p in points]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or hi * 0.05 or 1
    lo, hi = lo - pad, hi + pad
    n = len(points)
    px = lambda i: 46 + i * (w - 58) / (n - 1)
    py = lambda v: 12 + (hi - v) * (h - 44) / (hi - lo)
    poly = " ".join(f"{px(i):.1f},{py(p['v']):.1f}" for i, p in enumerate(points))
    # gridlines + y labels (4)
    g = ""
    for k in range(4):
        v = lo + (hi - lo) * k / 3
        y = py(v)
        g += (f'<line x1="46" y1="{y:.1f}" x2="{w-12}" y2="{y:.1f}" class="grid"/>'
              f'<text x="42" y="{y+4:.1f}" text-anchor="end" class="ax">₹{v:,.0f}</text>')
    # x labels: every Jan + last point
    for i, p in enumerate(points):
        if p["m"].endswith("-01") or i == n - 1:
            g += f'<text x="{px(i):.1f}" y="{h-6}" text-anchor="middle" class="ax">{p["m"]}</text>'
    dots = "".join(f'<circle cx="{px(i):.1f}" cy="{py(p["v"]):.1f}" r="2.4" fill="{color}">'
                   f'<title>{p["m"]}: ₹{p["v"]:,.2f}/kg</title></circle>'
                   for i, p in enumerate(points))
    return (f'<svg viewBox="0 0 {w} {h}">{g}'
            f'<polyline points="{poly}" fill="none" stroke="{color}" stroke-width="2.4"/>{dots}</svg>')


def year_table(points):
    by_year = {}
    for p in points:
        by_year.setdefault(p["m"][:4], []).append(p["v"])
    rows, prev_end = "", None
    for y in sorted(by_year):
        v = by_year[y]
        end = v[-1]
        yoy = f"{(end - prev_end) / prev_end * 100:+.1f}%" if prev_end else "—"
        cls = "up" if prev_end and end > prev_end else ("dn" if prev_end and end < prev_end else "")
        rows += (f'<tr><td>{y}</td><td class="num">₹{min(v):,.2f}</td><td class="num">₹{max(v):,.2f}</td>'
                 f'<td class="num">₹{sum(v)/len(v):,.2f}</td><td class="num">₹{end:,.2f}</td>'
                 f'<td class="num {cls}">{yoy}</td></tr>')
        prev_end = end
    return (f'<table><thead><tr><th>Year</th><th class="num">Low</th><th class="num">High</th>'
            f'<th class="num">Average</th><th class="num">Year-end</th><th class="num">Change vs prev year-end</th></tr></thead>'
            f'<tbody>{rows}</tbody></table>')


def main():
    with open(HIST) as f:
        hist = json.load(f)
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")

    order = ["Copper", "Aluminium conductor (Hindalco P0610)", "Aluminium",
             "CRGO steel", "Mild Steel", "Stainless Steel"]
    sections = ""
    for name in order:
        s = hist["series"].get(name)
        if not s:
            continue
        pts = s["points"]
        color = COLORS.get(name, "#555")
        latest = f'₹{pts[-1]["v"]:,.2f}/kg ({pts[-1]["m"]})' if pts else "—"
        ind = ' <span class="badge">indicative</span>' if s.get("indicative") else ""
        sections += (f'<section><h2>{name}{ind}</h2>'
                     f'<p class="mut">Latest: <b>{latest}</b> · Source: {s.get("source","—")}</p>'
                     f'{chart(pts, color)}{year_table(pts)}</section>')

    page = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Price history — Dynalektric</title><style>
body{{margin:0;background:#f6f5f2;color:#1c1b19;font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:28px 4vw 60px}}
h1{{font-size:26px;margin:0 0 4px}}h2{{font-size:18px;margin:26px 0 2px}}
.mut{{color:#7a766e;font-size:13px;margin:2px 0 8px}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}
.nav a{{font-size:12.5px;font-weight:600;color:#7a766e;text-decoration:none;padding:7px 13px;border:1px solid #e7e4de;border-radius:8px;background:#fff}}
.nav a.active{{background:#2a78d6;color:#fff;border-color:#2a78d6}}
section{{background:#fff;border:1px solid #e7e4de;border-radius:12px;padding:16px 20px;margin-bottom:16px}}
svg{{width:100%;height:auto}}.grid{{stroke:#eee9e2;stroke-width:1}}.ax{{font-size:9.5px;fill:#a09a90}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;margin-top:8px}}
th,td{{padding:6px 10px;border-top:1px solid #e7e4de;text-align:left}}
th{{color:#7a766e;font-weight:600;font-size:12px;text-transform:uppercase}}
.num{{text-align:right}}.up{{color:#b3261e;font-weight:600}}.dn{{color:#0a7a33;font-weight:600}}
.badge{{font-size:9px;font-weight:800;background:#fdf3dd;color:#8a6100;border-radius:4px;padding:2px 5px;vertical-align:3px}}
footer{{color:#7a766e;font-size:12.5px;margin-top:24px}}</style></head><body>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html">Forward demand</a><a href="./budget.html">Max Purchase Limit</a><a href="./backtest.html">Accuracy</a><a href="./history.html" class="active">Price history</a></nav>
<h1>Raw-material price history</h1>
<p class="mut">Monthly benchmark prices, ₹/kg ex-GST basis. Red year-change = costlier (bad for buying), green = cheaper.
Copper: LME cash settlement × USD-INR. Aluminium conductor: Hindalco P0610 (EC Grade, alloy A0 — the company's conductor reference).
Aluminium: NALCO circular basis. CRGO / MS / SS have no public feed — those series grow from our quotes and POs, honestly, with no invented history.</p>
{sections}
<footer>Generated {gen} UTC · updates daily with the price refresh · backfill via "Backfill price history" workflow</footer>
</body></html>'''
    with open(OUT, "w") as f:
        f.write(page)
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
