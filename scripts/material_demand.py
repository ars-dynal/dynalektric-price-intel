#!/usr/bin/env python3
"""
Forward Material Requirements Forecast (order-book based).

Instead of averaging past purchases, this projects what you will ACTUALLY need
from committed orders:

  budgets  -> each has a project delivery_date and bom_budget_items[]
              (raw materials with a quantity for that order)
  items    -> maps each raw material to its base metal (AL-222-/CU-222-) + UOM
  inventory-> current stock on hand per metal

For each metal we build gross forward demand by delivery month, subtract current
stock to get the NET quantity to buy, price it at today's live rate, and pair it
with the buy-timing signal to produce a plain recommendation.

OUTPUTS
  data/material_demand.json  -> detailed, per-budget (PRIVATE; git-ignored, artifact only)
  docs/demand.html           -> aggregate per-metal page (kg/month + net + spend), publishable

Aggregate output carries no budget numbers, vendors or rupee line rates.
Reads DEPL_CLIENT_* from env. KGS-priced metal items only (kg is the common unit).
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

BASE = "https://depl.consult-trico.com"
CATEGORY_MAP = {"AL-222-": "Aluminium", "CU-222-": "Copper"}
COL = {"Copper": "#eb6834", "Aluminium": "#2a78d6"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")
SIGNALS = os.path.join(ROOT, "data", "buy_signals.json")
OUT_JSON = os.path.join(ROOT, "data", "material_demand.json")
OUT_HTML = os.path.join(ROOT, "docs", "demand.html")
PER_PAGE = 100
HORIZON = 6  # months shown on the forward chart


def auth():
    r = requests.post(f"{BASE}/oauth/token", json={
        "grant_type": "client_credentials",
        "client_id": os.environ["DEPL_CLIENT_ID"],
        "client_secret": os.environ["DEPL_CLIENT_SECRET"],
    }, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def paginate(session, path, params):
    url = f"{BASE}{path}"
    want = dict(params)
    while url:
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query)); q.update(want)
        full = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
        r = session.get(full, timeout=40); r.raise_for_status()
        pg = r.json().get("data", {})
        for rec in pg.get("data", []):
            yield rec
        url = pg.get("next_page_url")
        time.sleep(0.15)


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def month_of(s):
    s = (s or "").strip()
    if not s:
        return None
    if "/" in s:
        p = s.split("/")
        if len(p) == 3 and len(p[2]) == 4:
            try:
                return f"{int(p[2]):04d}-{int(p[1]):02d}"
            except ValueError:
                return None
    if "-" in s and len(s) >= 7:
        try:
            y, mo = s[:7].split("-")
            return f"{int(y):04d}-{int(mo):02d}"
        except ValueError:
            return None
    return None


def add_months(m, k):
    y, mo = map(int, m.split("-")); mo += k
    y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
def fmt_m(m):
    y, mo = m.split("-"); return f"{MON[int(mo)-1]} '{y[2:]}"


def svg_bars(months, series_by_metal, cur_month):
    """Grouped monthly forward-demand bars (kg), one colour per metal."""
    if not months:
        return '<div class="csrc">No dated forward demand found.</div>'
    W, H, L, R, T, B = 640, 200, 46, 10, 12, 26
    allv = [v for s in series_by_metal.values() for v in s.values()] or [1]
    mx = max(allv) * 1.12 or 1
    n = len(months)
    metals = list(series_by_metal)
    gw = (W - L - R) / n
    bw = min(26, gw / (len(metals) + 1))
    sy = lambda v: T + (1 - v / mx) * (H - T - B)
    g = ""
    for k in range(3):
        gv = mx * k / 2; yy = sy(gv)
        g += f'<line x1="{L}" y1="{yy:.0f}" x2="{W-R}" y2="{yy:.0f}" stroke="var(--grid)"/>'
        g += f'<text x="{L-4}" y="{yy+3:.0f}" text-anchor="end" class="cax">{gv:,.0f}</text>'
    bars = ""
    for i, m in enumerate(months):
        cx = L + gw * i + gw / 2
        for j, metal in enumerate(metals):
            v = series_by_metal[metal].get(m, 0)
            if v <= 0:
                continue
            x = cx - (len(metals) * bw) / 2 + j * bw
            h = (H - T - B) - (sy(v) - T)
            bars += f'<rect x="{x:.1f}" y="{sy(v):.1f}" width="{bw-2:.1f}" height="{max(0,h):.1f}" rx="2" fill="{COL.get(metal,"#888")}"/>'
        bars += f'<text x="{cx:.0f}" y="{H-6}" text-anchor="middle" class="cax">{fmt_m(m)}</text>'
    return f'<svg viewBox="0 0 {W} {H}">{g}{bars}</svg>'


def render_html(out, signals):
    metals = out["by_metal"]
    tot_spend = sum(m["net_spend_inr"] for m in metals.values())
    def cr(x):
        return (f"₹{x/1e7:.2f} Cr" if x >= 1e7 else (f"₹{x/1e5:.1f} L" if x >= 1e5 else f"₹{x:,.0f}"))
    # forward months across horizon
    months = out["horizon_months"]
    series = {mt: {p["m"]: p["kg"] for p in d["monthly"]} for mt, d in metals.items()}
    cards = ""
    for mt, d in metals.items():
        sg = (signals.get("signals", {}).get(mt) or {})
        sig = sg.get("signal", "—")
        sigcls = {"BUY": "#0ca30c", "HOLD": "#d03b3b", "NEUTRAL": "#c98500"}.get(sig, "#898781")
        cover = "covered by stock" if d["net_kg"] <= 0 else f"buy ~{d['net_kg']:,.0f} kg"
        cards += f'''<div class="mc" style="border-top:3px solid {COL.get(mt,'#888')}">
          <div class="mct"><span class="dot" style="background:{COL.get(mt,'#888')}"></span>{mt}
            <span class="sig" style="background:{sigcls}">{sig}</span></div>
          <div class="grid3">
            <div><div class="k">Committed demand</div><div class="v">{d['gross_kg']:,.0f} kg</div><div class="s">next {HORIZON} mo</div></div>
            <div><div class="k">In stock</div><div class="v">{d['stock_kg']:,.0f} kg</div></div>
            <div><div class="k">Net to buy</div><div class="v" style="color:{'#0ca30c' if d['net_kg']<=0 else 'var(--tx)'}">{max(0,d['net_kg']):,.0f} kg</div><div class="s">{cr(d['net_spend_inr'])}</div></div>
          </div>
          <div class="rec"><b>Recommendation:</b> {cover} · signal <b>{sig}</b> — {sg.get('action','')}</div>
        </div>'''
    chart = svg_bars(months, series, out.get("current_month"))
    return HTML.replace("{{TOT}}", cr(tot_spend)).replace("{{CARDS}}", cards) \
               .replace("{{CHART}}", chart).replace("{{GEN}}", out["generated_at_utc"][:16].replace("T", " ")) \
               .replace("{{NBUD}}", str(out["budgets_counted"]))


def main():
    with open(SUMMARY) as f:
        summary = json.load(f)
    price = {"Aluminium": summary["aluminium"]["price_per_kg"], "Copper": summary["copper"]["price_per_kg"]}
    try:
        with open(SIGNALS) as f:
            signals = json.load(f)
    except FileNotFoundError:
        signals = {"signals": {}}

    token = auth()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    # items -> id -> (metal, uom)
    item_meta = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        metal = CATEGORY_MAP.get((rec.get("item_category") or {}).get("category_code"))
        if metal:
            item_meta[rec["id"]] = (metal, (rec.get("uom") or {}).get("name"))
    print(f"Metal items: {len(item_meta)}")

    # inventory -> current stock kg per metal (KGS only)
    stock = defaultdict(float)
    for inv in paginate(session, "/api/external/inventory", {"per_page": PER_PAGE}):
        meta = item_meta.get(inv.get("item_id"))
        if meta and meta[1] == "KGS":
            stock[meta[0]] += fnum(inv.get("total_qty")) or 0.0
    print("Stock kg:", {k: round(v) for k, v in stock.items()})

    cur_month = datetime.now(timezone.utc).strftime("%Y-%m")
    demand = defaultdict(lambda: defaultdict(float))  # metal -> {month: kg}
    undated = defaultdict(float)
    budget_detail = []
    n_budgets = 0

    for b in paginate(session, "/api/external/budgets", {"per_page": PER_PAGE}):
        proj = b.get("project") or {}
        dmon = month_of(proj.get("delivery_date"))
        lines = []
        for it in (b.get("bom_budget_items") or []):
            meta = item_meta.get(it.get("item_id"))
            if not meta or meta[1] != "KGS":
                continue
            metal = meta[0]
            qty = fnum(it.get("quantity")) or 0.0
            if qty <= 0:
                continue
            lines.append({"metal": metal, "kg": round(qty, 1),
                          "system_rate": fnum(it.get("system_rate")),
                          "vendor_rate": fnum(it.get("vendor_rate"))})
            if dmon and dmon >= cur_month:
                demand[metal][dmon] += qty
            elif not dmon:
                undated[metal] += qty
        if lines:
            n_budgets += 1
            budget_detail.append({"budget_number": b.get("budget_number"),
                                  "project_code": proj.get("project_code"),
                                  "delivery_month": dmon, "status": b.get("status"),
                                  "max_purchase_limit_amount": fnum(b.get("max_purchase_limit_amount")),
                                  "metal_lines": lines})

    horizon_months = [add_months(cur_month, k) for k in range(0, HORIZON)]
    by_metal = {}
    for metal in CATEGORY_MAP.values():
        monthly = [{"m": m, "kg": round(demand[metal].get(m, 0.0), 1)} for m in horizon_months]
        gross = sum(p["kg"] for p in monthly) + undated.get(metal, 0.0)
        st = stock.get(metal, 0.0)
        net = max(0.0, gross - st)
        by_metal[metal] = {
            "monthly": monthly,
            "undated_kg": round(undated.get(metal, 0.0), 1),
            "gross_kg": round(gross, 1),
            "stock_kg": round(st, 1),
            "net_kg": round(net, 1),
            "net_spend_inr": round(net * price.get(metal, 0), 0),
            "price_per_kg": price.get(metal),
        }

    out = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_month": cur_month,
        "horizon_months": horizon_months,
        "budgets_counted": n_budgets,
        "method": "Forward demand from budget bom_budget_items x project delivery_date, "
                  "net of current inventory, priced at today's live metal rate.",
        "live_prices": price,
        "by_metal": by_metal,
        "budgets": budget_detail,  # PRIVATE detail (git-ignored file)
    }
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(render_html(out, signals))

    print(f"Budgets with metal lines: {n_budgets}")
    for m, d in by_metal.items():
        print(f"  {m}: gross {d['gross_kg']:,.0f} kg, stock {d['stock_kg']:,.0f}, "
              f"net {d['net_kg']:,.0f} kg (~Rs {d['net_spend_inr']:,.0f})")
    print(f"Wrote {OUT_JSON} (private) and {OUT_HTML} (aggregate)")


HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dynalektric - Forward Material Requirements</title><style>
:root{color-scheme:light dark;--bg:#f9f9f7;--surf:#fff;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.1);--grid:#e6e5df}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--bd:rgba(255,255,255,.12);--grid:#2c2c2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand b{font-size:15px}.brand span{font-size:12px;color:var(--mut)}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13px;margin:0 0 18px;max-width:820px;line-height:1.5}
.tot{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:16px 18px;margin-bottom:20px;display:flex;align-items:baseline;gap:12px}
.tot .big{font-size:30px;font-weight:700}.tot .lbl{font-size:13px;color:var(--tx2)}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:720px){.cards{grid-template-columns:1fr}}
.mc{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:16px}
.mct{font-size:14px;font-weight:650;display:flex;align-items:center;gap:7px}.dot{width:10px;height:10px;border-radius:2px}
.sig{margin-left:auto;color:#fff;font-size:10px;font-weight:800;padding:2px 8px;border-radius:6px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:12px 0}
.k{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}.v{font-size:17px;font-weight:650;margin-top:2px}.s{font-size:11.5px;color:var(--tx2)}
.rec{font-size:12px;color:var(--tx2);line-height:1.5;border-top:1px solid var(--grid);padding-top:9px}
.chartcard{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:14px;margin-top:16px}
.chartcard .ct{font-size:13px;font-weight:650;margin-bottom:8px}.chartcard svg{width:100%;height:auto}.cax{fill:var(--mut);font-size:9px}
.leg{display:flex;gap:14px;font-size:11px;color:var(--tx2);margin-top:6px}.leg i{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px}
footer{margin-top:24px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><b>Dynalektric</b><span>Forward Material Requirements — from the order book</span></div>
<h1>What we'll need to buy, from committed orders</h1>
<p class="sub">Forward demand = each budget's raw-material quantities scheduled by its project delivery date, netted against current stock, priced at today's live metal rate, and paired with the buy-timing signal. Covers the next {{NBUD}} budgets with metal lines.</p>
<div class="tot"><span class="big">{{TOT}}</span><span class="lbl">net metal to buy over the next 6 months, at today's prices</span></div>
<div class="cards">{{CARDS}}</div>
<div class="chartcard"><div class="ct">Committed demand by delivery month (kg)</div>{{CHART}}
<div class="leg"><span><i style="background:#eb6834"></i>Copper</span><span><i style="background:#2a78d6"></i>Aluminium</span></div></div>
<footer>Source: DEPL/Trico ERP budgets (bom_budget_items x delivery_date) + inventory + live LME/NALCO prices. Generated {{GEN}} UTC. Aggregate view — per-budget detail stays private. <a href="./items.html" style="color:#2a78d6">Live prices →</a> · <a href="./consumption.html" style="color:#2a78d6">Consumption →</a></footer>
</div></body></html>"""


if __name__ == "__main__":
    main()
