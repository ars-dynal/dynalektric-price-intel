#!/usr/bin/env python3
"""
Renders the PRIVATE executive procurement dashboard from the engines' outputs:

  data/bom_analysis.json    (bom_engine.py)      — budget bands, categories
  data/purchase_plan.json   (purchase_planner.py) — actions, alerts
  data/vendor_scores.json   (vendor_intel.py)     — optional, vendor leaderboards
  data/public_summary.json                        — live benchmark prices

Output: data/procurement_dashboard.html — CONTAINS REAL ITEM CODES, RUPEE
FIGURES AND VENDOR NAMES. It is git-ignored and must only ever leave the
runner as a PRIVATE workflow artifact (the procurement-plan workflow uploads
it). Never publish it to docs/ or GitHub Pages.

Usage: python3 scripts/render_procurement_dashboard.py
"""
import html
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = lambda *p: os.path.join(ROOT, "data", *p)
OUT = D("procurement_dashboard.html")

ACTION_STYLE = {"BUY_NOW": ("b-now", "BUY NOW"), "BUY_SOON": ("b-soon", "BUY SOON"),
                "MONITOR": ("b-mon", "MONITOR"), "DELAY": ("b-del", "DELAY")}
CAT_ORDER = ["Copper", "CRGO", "Aluminium", "Oil", "Hardware", "Consumables", "Finished Goods", "Others"]


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def _inr(n):
    n = int(round(n or 0))
    sign = "-" if n < 0 else ""
    d = str(abs(n))
    if len(d) <= 3:
        return sign + d
    head, tail = d[:-3], d[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts) + "," + tail


def cr(x):
    """Exact rupees, Indian digit grouping — never lakh-rounded (rupee-sensitive)."""
    x = x or 0
    return f"-₹{_inr(abs(x))}" if x < 0 else f"₹{_inr(x)}"



def esc(s):
    return html.escape(str(s if s is not None else "—"))


def main():
    analysis = load(D("bom_analysis.json"))
    if not analysis:
        raise SystemExit("data/bom_analysis.json missing — run scripts/bom_engine.py first")
    plan = load(D("purchase_plan.json"), {"summary_by_action": {}, "alerts": [], "plan": []})
    vendors = load(D("vendor_scores.json"), {"by_category": {}})
    summary = load(D("public_summary.json"), {})

    grand = analysis.get("grand_total", {})
    cats = analysis.get("category_summary", {})
    docs = analysis.get("documents", [])
    over_limit = [d for d in docs if (d.get("expected_vs_limit") or 0) > 0]
    alerts = plan.get("alerts", [])
    actions = plan.get("summary_by_action", {})
    now = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # ---- category bars ----
    max_cost = max((c.get("expected_cost", 0) for c in cats.values()), default=1) or 1
    cat_rows = ""
    for cat in CAT_ORDER:
        c = cats.get(cat)
        if not c:
            continue
        w = max(2, c.get("expected_cost", 0) / max_cost * 100)
        cat_rows += (f'<div class="bar-row"><span class="lbl">{esc(cat)}</span>'
                     f'<div class="track"><span class="fill" style="width:{w:.0f}%"></span></div>'
                     f'<span class="num">{cr(c.get("expected_cost"))} · net {c.get("net_buy_qty", 0):,.0f}</span></div>')

    # ---- action table ----
    act_rows = ""
    for a in ("BUY_NOW", "BUY_SOON", "MONITOR", "DELAY"):
        if a in actions:
            cls, label = ACTION_STYLE[a]
            v = actions[a]
            act_rows += (f'<tr><td><span class="badge {cls}">{label}</span></td>'
                         f'<td class="r">{v.get("lines", 0)}</td>'
                         f'<td class="r">{cr(v.get("expected_spend_inr"))}</td></tr>')

    # ---- top purchase lines ----
    line_rows = ""
    for p in plan.get("plan", [])[:15]:
        cls, label = ACTION_STYLE.get(p.get("action"), ("b-mon", p.get("action", "—")))
        dd = p.get("days_to_latest_order")
        line_rows += (f'<tr><td class="mono">{esc(p.get("item_code"))}</td>'
                      f'<td>{esc(p.get("category"))}</td>'
                      f'<td class="r">{p.get("net_buy_qty", 0):,.1f}</td>'
                      f'<td class="r">{cr(p.get("expected_cost"))}</td>'
                      f'<td>{esc(p.get("project_code"))}</td>'
                      f'<td class="r">{esc(dd) if dd is not None else "—"}</td>'
                      f'<td><span class="badge {cls}">{label}</span></td></tr>')
    if not line_rows:
        line_rows = '<tr><td colspan="7" class="muted">No net-to-buy lines — demand fully covered by stock.</td></tr>'

    # ---- alerts ----
    alert_rows = ""
    for al in alerts[:12]:
        dot = {"BUDGET_LIMIT_EXCEEDED": "var(--warn)", "DELIVERY_RISK": "var(--crit)",
               "CRITICAL_SHORTAGE": "var(--crit)"}.get(al.get("type"), "var(--warn)")
        who = al.get("item_code") or al.get("budget_number") or ""
        alert_rows += (f'<div class="alert"><span class="dot" style="background:{dot}"></span>'
                       f'<span><b>{esc(al.get("type", "").replace("_", " ").title())}</b> '
                       f'{("· " + esc(who)) if who else ""} — {esc(al.get("detail"))}</span></div>')
    if not alert_rows:
        alert_rows = '<div class="alert"><span class="dot" style="background:var(--good)"></span><span>No active alerts.</span></div>'

    # ---- vendor leaderboards ----
    vend_html = ""
    for cat in ("Copper", "Aluminium", "CRGO"):
        block = (vendors.get("by_category") or {}).get(cat)
        if not block:
            continue
        rows = "".join(
            f'<tr><td>{esc(v.get("vendor_name"))}</td><td class="r"><b>{v.get("score", 0):.0f}</b></td>'
            f'<td class="r">{("%+.1f%%" % v["vs_category_median_pct"]) if v.get("vs_category_median_pct") is not None else "—"}</td></tr>'
            for v in block.get("vendors", [])[:3])
        vend_html += (f'<h4>{esc(cat)}</h4><table><tr><th>Vendor</th><th class="r">Score</th>'
                      f'<th class="r">vs median</th></tr>{rows}</table>')
    if not vend_html:
        vend_html = '<p class="muted">Run scripts/vendor_intel.py to populate vendor scores.</p>'

    prices = " · ".join(
        f"{name} ₹{(summary.get(key) or {}).get('price_per_kg', 0):,.0f}/kg"
        for name, key in (("Al", "aluminium"), ("Cu", "copper"), ("CRGO", "crgo_steel"))
        if (summary.get(key) or {}).get("price_per_kg"))

    page = TEMPLATE
    for k, v in {
        "{{NOW}}": now, "{{PRICES}}": prices or "—",
        "{{EXPECTED}}": cr(grand.get("expected")), "{{MIN}}": cr(grand.get("min")), "{{MAX}}": cr(grand.get("max")),
        "{{NDOCS}}": str(len(docs)), "{{NOVER}}": str(len(over_limit)), "{{NALERTS}}": str(len(alerts)),
        "{{SOURCE}}": esc(analysis.get("source")), "{{CATS}}": cat_rows, "{{ACTIONS}}": act_rows,
        "{{LINES}}": line_rows, "{{ALERTS}}": alert_rows, "{{VENDORS}}": vend_html,
    }.items():
        page = page.replace(k, v)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Wrote {OUT} (PRIVATE — git-ignored; deliver only as a private workflow artifact)")


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dynalektric — AI Procurement Dashboard (PRIVATE)</title><style>
.viz-root{color-scheme:light;--bg:#f4f4f2;--surf:#fcfcfb;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;
 --bd:rgba(11,11,11,.12);--grid:#e6e5df;--s1:#2a78d6;--good:#008300;--warn:#c98500;--crit:#c8341f}
@media(prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{color-scheme:dark;
 --bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--mut:#8f8e86;--bd:rgba(255,255,255,.14);
 --grid:#2c2c2a;--s1:#3987e5;--good:#27a827;--warn:#e0a000;--crit:#e05a45}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--tx)}
.viz-root{background:var(--bg);min-height:100vh;padding:22px 18px 60px}.wrap{max-width:1160px;margin:0 auto}
.priv{display:inline-block;background:var(--crit);color:#fff;font-size:10px;font-weight:800;letter-spacing:.08em;padding:3px 9px;border-radius:6px;text-transform:uppercase}
h1{font-size:20px;margin:10px 0 2px}.sub{color:var(--tx2);font-size:12.5px;margin:0 0 16px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:14px}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:13px 14px}
.kpi .k{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}
.kpi .v{font-size:20px;font-weight:700;margin-top:3px}.kpi .d{font-size:11px;color:var(--tx2);margin-top:2px}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:12px}@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:15px;margin-bottom:12px}
.card h3{font-size:13px;margin:0 0 10px}.card h4{font-size:11.5px;margin:12px 0 6px;color:var(--tx2)}
.bar-row{display:grid;grid-template-columns:110px 1fr 150px;gap:10px;align-items:center;font-size:12px;margin:7px 0}
.bar-row .lbl{color:var(--tx2)}.track{height:14px;background:var(--grid);border-radius:4px;overflow:hidden}
.fill{display:block;height:100%;border-radius:4px;background:var(--s1)}
.num{font-variant-numeric:tabular-nums;text-align:right;color:var(--tx2);font-size:11px}
table{width:100%;border-collapse:collapse;font-size:12px}
th{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);text-align:left;padding:6px 8px;border-bottom:1px solid var(--bd)}
td{padding:7px 8px;border-bottom:1px solid var(--grid)}td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:11.5px}.muted{color:var(--mut)}
.badge{font-size:10px;font-weight:700;padding:2px 8px;border-radius:6px;color:#fff;white-space:nowrap}
.b-now{background:var(--crit)}.b-soon{background:var(--warn)}.b-del{background:var(--s1)}.b-mon{background:var(--mut)}
.alert{display:flex;gap:9px;font-size:12px;padding:8px 0;border-bottom:1px solid var(--grid);line-height:1.45}
.alert:last-child{border-bottom:0}.dot{width:8px;height:8px;border-radius:50%;margin-top:4px;flex:none}
footer{margin-top:20px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="viz-root"><div class="wrap">
<span class="priv">Private — contains item codes, budgets & vendors. Do not publish.</span>
<h1>AI Procurement Dashboard</h1>
<p class="sub">Source: {{SOURCE}} · generated {{NOW}} · live prices: {{PRICES}}</p>
<div class="kpis">
  <div class="kpi"><div class="k">Expected budget</div><div class="v">{{EXPECTED}}</div><div class="d">net-to-buy, all scope</div></div>
  <div class="kpi"><div class="k">Budget band</div><div class="v">{{MIN}} – {{MAX}}</div><div class="d">min → max (risk-adjusted)</div></div>
  <div class="kpi"><div class="k">Budgets analysed</div><div class="v">{{NDOCS}}</div><div class="d">with material lines</div></div>
  <div class="kpi"><div class="k">Over max limit</div><div class="v">{{NOVER}}</div><div class="d">expected cost &gt; limit</div></div>
  <div class="kpi"><div class="k">Active alerts</div><div class="v">{{NALERTS}}</div><div class="d">delivery / shortage / budget</div></div>
</div>
<div class="grid"><div>
  <div class="card"><h3>Expected cost by category (net-to-buy)</h3>{{CATS}}</div>
  <div class="card"><h3>Purchase plan — top priority lines</h3>
    <table><tr><th>Item</th><th>Category</th><th class="r">Net qty</th><th class="r">Cost</th><th>Project</th><th class="r">Days to order</th><th>Action</th></tr>
    {{LINES}}</table></div>
</div><div>
  <div class="card"><h3>Plan summary</h3>
    <table><tr><th>Action</th><th class="r">Lines</th><th class="r">Value</th></tr>{{ACTIONS}}</table></div>
  <div class="card"><h3>Alerts</h3>{{ALERTS}}</div>
  <div class="card"><h3>Vendor leaderboard</h3>{{VENDORS}}</div>
</div></div>
<footer>Built by render_procurement_dashboard.py from bom_analysis.json + purchase_plan.json + vendor_scores.json.
Distributed only as a private GitHub Actions artifact. Decision support, not a guarantee.</footer>
</div></div></body></html>"""


if __name__ == "__main__":
    main()
