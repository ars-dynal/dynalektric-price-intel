#!/usr/bin/env python3
"""
Forward Material Requirements + Max Purchase Limit exposure (order-book based).

For each metal it combines three ERP sources into one procurement view:

  budgets   -> open (pending) demand = bom_budget_items quantities, split into
               overdue (delivery date past) vs upcoming, and each line's
               system_rate / vendor_rate.
  inventory -> current stock on hand.
  live price-> today's metal benchmark.

Outputs, per metal:
  * open demand (kg + Rs), overdue vs upcoming
  * stock on hand + months of cover (vs the consumption run-rate)
  * NET to buy = max(0, open demand - stock), priced live
  * Max Purchase Limit exposure = budget lines whose system_rate is below today's
    metal price (under-costed), with the Rs shortfall + count of unset (0) rates
  * a plain recommendation, paired with the buy-timing signal

Aggregate page -> docs/demand.html (publishable). Per-budget detail ->
data/material_demand.json (PRIVATE artifact, git-ignored). Reads DEPL_CLIENT_*
from env. KGS-priced metal items only.
"""
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
_RETRY = Retry(total=6, connect=6, read=3, backoff_factor=8,
               status_forcelist=(429, 500, 502, 503, 504),
               allowed_methods=('GET', 'POST'))
def _rsess():
    s = requests.Session()
    s.mount('https://', HTTPAdapter(max_retries=_RETRY))
    return s

BASE = "https://depl.consult-trico.com"
CATEGORY_MAP = {"AL-222-": "Aluminium", "CU-222-": "Copper"}
COL = {"Copper": "#eb6834", "Aluminium": "#2a78d6"}
# Consumption run-rate (kg/month) for months-of-cover; approx from PO history.
RUN_RATE = {"Copper": 2338, "Aluminium": 2437}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")
SIGNALS = os.path.join(ROOT, "data", "buy_signals.json")
OUT_JSON = os.path.join(ROOT, "data", "material_demand.json")
OUT_HTML = os.path.join(ROOT, "docs", "demand.html")
PER_PAGE = 100
HORIZON = 6
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def auth():
    r = _rsess().post(f"{BASE}/oauth/token", json={
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
            y, mo = s[:7].split("-"); return f"{int(y):04d}-{int(mo):02d}"
        except ValueError:
            return None
    return None


def add_months(m, k):
    y, mo = map(int, m.split("-")); mo += k
    y += (mo - 1) // 12; mo = (mo - 1) % 12 + 1
    return f"{y:04d}-{mo:02d}"


def fmt_m(m):
    y, mo = m.split("-"); return f"{MON[int(mo)-1]} '{y[2:]}"


def build(budget_detail, stock, price, cur_month):
    """Core computation. budget_detail: [{delivery_month, metal_lines:[{metal,kg,system_rate,vendor_rate}]}]."""
    horizon = [add_months(cur_month, k) for k in range(HORIZON)]
    agg = {m: {"open": 0.0, "overdue": 0.0, "upcoming": 0.0, "monthly": defaultdict(float),
               "lines": 0, "uc_lines": 0, "uc_short": 0.0, "unset": 0} for m in CATEGORY_MAP.values()}
    for b in budget_detail:
        dm = b.get("delivery_month")
        for l in b.get("metal_lines", []):
            m = l["metal"]; kg = l.get("kg") or 0.0; sr = l.get("system_rate")
            a = agg[m]; lv = price[m]
            a["open"] += kg; a["lines"] += 1
            if dm and dm >= cur_month:
                a["upcoming"] += kg
                if dm in horizon:
                    a["monthly"][dm] += kg
            else:
                a["overdue"] += kg
            if not sr:
                a["unset"] += 1
            elif sr < lv:
                a["uc_lines"] += 1; a["uc_short"] += (lv - sr) * kg

    by_metal = {}
    for m, a in agg.items():
        st = stock.get(m, 0.0)
        net = max(0.0, a["open"] - st)
        rr = RUN_RATE.get(m, 0) or 1
        by_metal[m] = {
            "open_kg": round(a["open"], 1), "overdue_kg": round(a["overdue"], 1),
            "upcoming_kg": round(a["upcoming"], 1),
            "monthly": [{"m": mm, "kg": round(a["monthly"].get(mm, 0.0), 1)} for mm in horizon],
            "stock_kg": round(st, 1), "cover_months": round(st / rr, 1),
            "net_kg": round(net, 1), "net_spend_inr": round(net * price[m], 0),
            "open_value_inr": round(a["open"] * price[m], 0),
            "undercost": {"lines": a["uc_lines"], "total_lines": a["lines"],
                          "unset": a["unset"], "shortfall_inr": round(a["uc_short"], 0)},
            "price_per_kg": price[m],
        }
    return {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "current_month": cur_month, "horizon_months": horizon,
        "budgets_counted": len(budget_detail), "live_prices": price,
        "method": "Open (pending) budget demand vs inventory + live price; Max Purchase Limit "
                  "exposure = budget lines with system_rate below today's metal price.",
        "by_metal": by_metal, "budgets": budget_detail,
    }


def svg_bars(months, series):
    if not months or not any(v for s in series.values() for v in s.values()):
        return '<div class="csrc">Almost all open demand is backlog (past delivery dates) — little is scheduled forward. See the numbers above.</div>'
    W, H, L, R, T, B = 640, 190, 46, 10, 12, 26
    allv = [v for s in series.values() for v in s.values()] or [1]
    mx = max(allv) * 1.15 or 1
    n = len(months); metals = list(series); gw = (W - L - R) / n; bw = min(24, gw / (len(metals) + 1))
    sy = lambda v: T + (1 - v / mx) * (H - T - B)
    g = ""
    for k in range(3):
        gv = mx * k / 2; yy = sy(gv)
        g += f'<line x1="{L}" y1="{yy:.0f}" x2="{W-R}" y2="{yy:.0f}" stroke="var(--grid)"/><text x="{L-4}" y="{yy+3:.0f}" text-anchor="end" class="cax">{gv:,.0f}</text>'
    bars = ""
    for i, m in enumerate(months):
        cx = L + gw * i + gw / 2
        for j, metal in enumerate(metals):
            v = series[metal].get(m, 0)
            if v <= 0:
                continue
            x = cx - (len(metals) * bw) / 2 + j * bw
            bars += f'<rect x="{x:.1f}" y="{sy(v):.1f}" width="{bw-2:.1f}" height="{max(0,(H-T-B)-(sy(v)-T)):.1f}" rx="2" fill="{COL.get(metal)}"/>'
        bars += f'<text x="{cx:.0f}" y="{H-6}" text-anchor="middle" class="cax">{fmt_m(m)}</text>'
    return f'<svg viewBox="0 0 {W} {H}">{g}{bars}</svg>'


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



def render_html(out, signals):
    bm = out["by_metal"]
    net_total = sum(m["net_spend_inr"] for m in bm.values())
    uc_total = sum(m["undercost"]["shortfall_inr"] for m in bm.values())
    stocked = all(m["net_kg"] <= 0 for m in bm.values())
    banner = (f"Well stocked — open demand is fully covered by inventory (net to buy ≈ ₹0). "
              f"Signal is HOLD and you hold months of cover, so <b>hold off buying and run stock down.</b>"
              if stocked else f"Net metal to buy: {cr(net_total)}.")
    cards = ""
    for m, d in bm.items():
        sg = (signals.get("signals", {}).get(m) or {}); sig = sg.get("signal", "—")
        sc = {"BUY": "#0ca30c", "HOLD": "#d03b3b", "NEUTRAL": "#c98500"}.get(sig, "#898781")
        rec = ("covered by stock — no near-term buy" if d["net_kg"] <= 0 else f"buy ~{d['net_kg']:,.0f} kg ({cr(d['net_spend_inr'])})")
        cards += f'''<div class="mc" style="border-top:3px solid {COL.get(m)}">
          <div class="mct"><span class="dot" style="background:{COL.get(m)}"></span>{m}
            <span class="sig" style="background:{sc}">{sig}</span></div>
          <div class="grid3">
            <div><div class="k">Open demand</div><div class="v">{d['open_kg']:,.0f} kg</div><div class="s">{cr(d['open_value_inr'])} · {d['overdue_kg']:,.0f} backlog</div></div>
            <div><div class="k">In stock</div><div class="v">{d['stock_kg']:,.0f} kg</div><div class="s">{d['cover_months']} mo cover</div></div>
            <div><div class="k">Net to buy</div><div class="v" style="color:{'#0ca30c' if d['net_kg']<=0 else 'var(--tx)'}">{d['net_kg']:,.0f} kg</div></div>
          </div>
          <div class="rec"><b>Recommendation:</b> {rec} · signal <b>{sig}</b></div>
        </div>'''
    # Max Purchase Limit exposure section
    exp = ""
    for m, d in bm.items():
        u = d["undercost"]
        exp += f'''<div class="exprow"><span class="dot" style="background:{COL.get(m)}"></span>
          <b>{m}</b>: {u['lines']} of {u['total_lines']} budget lines priced below today's ₹{d['price_per_kg']:,.0f}/kg metal
          → <b>{cr(u['shortfall_inr'])}</b> shortfall if procured now · {u['unset']} lines have no rate set (₹0).</div>'''
    series = {m: {p["m"]: p["kg"] for p in d["monthly"]} for m, d in bm.items()}
    chart = svg_bars(out["horizon_months"], series)
    return HTML.replace("{{BANNER}}", banner).replace("{{CARDS}}", cards) \
               .replace("{{UCTOT}}", cr(uc_total)).replace("{{EXP}}", exp) \
               .replace("{{CHART}}", chart).replace("{{NBUD}}", str(out["budgets_counted"])) \
               .replace("{{GEN}}", out["generated_at_utc"][:16].replace("T", " "))


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
    session = _rsess()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    item_meta = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        metal = CATEGORY_MAP.get((rec.get("item_category") or {}).get("category_code"))
        if metal:
            item_meta[rec["id"]] = (metal, (rec.get("uom") or {}).get("name"))
    stock = defaultdict(float)
    for inv in paginate(session, "/api/external/inventory", {"per_page": PER_PAGE}):
        meta = item_meta.get(inv.get("item_id"))
        if meta and meta[1] == "KGS":
            stock[meta[0]] += fnum(inv.get("total_qty")) or 0.0

    budget_detail = []
    for b in paginate(session, "/api/external/budgets", {"per_page": PER_PAGE}):
        proj = b.get("project") or {}
        lines = []
        for it in (b.get("bom_budget_items") or []):
            meta = item_meta.get(it.get("item_id"))
            if not meta or meta[1] != "KGS":
                continue
            qty = fnum(it.get("quantity")) or 0.0
            if qty <= 0:
                continue
            lines.append({"metal": meta[0], "kg": round(qty, 1),
                          "system_rate": fnum(it.get("system_rate")), "vendor_rate": fnum(it.get("vendor_rate"))})
        if lines:
            budget_detail.append({"budget_number": b.get("budget_number"),
                                  "project_code": proj.get("project_code"),
                                  "delivery_month": month_of(proj.get("delivery_date")),
                                  "status": b.get("status"),
                                  "max_purchase_limit_amount": fnum(b.get("max_purchase_limit_amount")),
                                  "metal_lines": lines})

    cur_month = datetime.now(timezone.utc).strftime("%Y-%m")
    out = build(budget_detail, dict(stock), price, cur_month)
    with open(OUT_JSON, "w") as f:
        json.dump(out, f, indent=2)
    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write(render_html(out, signals))
    for m, d in out["by_metal"].items():
        print(f"{m}: open {d['open_kg']:,.0f} kg, stock {d['stock_kg']:,.0f} ({d['cover_months']}mo), "
              f"net {d['net_kg']:,.0f}; under-costed {d['undercost']['lines']}/{d['undercost']['total_lines']} "
              f"= Rs {d['undercost']['shortfall_inr']:,.0f}")
    print(f"Wrote {OUT_JSON} (private) and {OUT_HTML}")


HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dynalektric - Forward Requirements & Budget Exposure</title><style>
:root{color-scheme:light dark;--bg:#f9f9f7;--surf:#fff;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.1);--grid:#e6e5df;--warnt:#fff3d9;--warn:#c98500}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--bd:rgba(255,255,255,.12);--grid:#2c2c2a;--warnt:rgba(250,178,25,.14)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:24px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand b{font-size:15px}.brand span{font-size:12px;color:var(--mut)}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.nav a{font-size:12.5px;font-weight:600;color:var(--tx2);text-decoration:none;padding:7px 13px;border:1px solid var(--bd);border-radius:8px;background:var(--surf)}
.nav a.active{background:#2a78d6;color:#fff;border-color:#2a78d6}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13px;margin:0 0 16px;max-width:840px;line-height:1.5}
h2{font-size:15px;margin:26px 0 10px}
.banner{background:var(--surf);border:1px solid var(--bd);border-left:4px solid #0ca30c;border-radius:12px;padding:14px 16px;font-size:13.5px;line-height:1.55;margin-bottom:18px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:16px}@media(max-width:720px){.cards{grid-template-columns:1fr}}
.mc{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:16px}
.mct{font-size:14px;font-weight:650;display:flex;align-items:center;gap:7px}.dot{width:10px;height:10px;border-radius:2px;display:inline-block}
.sig{margin-left:auto;color:#fff;font-size:10px;font-weight:800;padding:2px 8px;border-radius:6px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:12px 0}
.k{font-size:10.5px;color:var(--mut);text-transform:uppercase;letter-spacing:.04em}.v{font-size:17px;font-weight:650;margin-top:2px}.s{font-size:11px;color:var(--tx2)}
.rec{font-size:12px;color:var(--tx2);line-height:1.5;border-top:1px solid var(--grid);padding-top:9px}
.expbox{background:var(--warnt);border:1px solid color-mix(in srgb,var(--warn) 40%,var(--bd));border-radius:12px;padding:14px 16px}
.expbox .eh{font-size:14px;font-weight:700;margin-bottom:8px}.exprow{font-size:12.5px;line-height:1.7}
.chartcard{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:14px;margin-top:10px}.chartcard .ct{font-size:13px;font-weight:650;margin-bottom:8px}
.chartcard svg{width:100%;height:auto}.cax{fill:var(--mut);font-size:9px}.csrc{font-size:12px;color:var(--tx2);padding:10px 0}
.leg{display:flex;gap:14px;font-size:11px;color:var(--tx2);margin-top:6px}.leg i{width:9px;height:9px;border-radius:2px;display:inline-block;margin-right:4px}
footer{margin-top:24px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><b>Dynalektric</b><span>Forward Requirements &amp; Max Purchase Limit exposure</span></div>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html" class="active">Forward demand</a><a href="./budget.html">Max Purchase Limit</a><a href="./backtest.html">Accuracy</a></nav>
<h1>What to buy, what's covered, and which budgets are under-costed</h1>
<p class="sub">Open (pending) budget demand vs current inventory, priced live, paired with the buy signal — across {{NBUD}} budgets with metal lines. Plus every budget line whose rate sits below today's metal cost.</p>
<div class="banner">{{BANNER}}</div>
<div class="cards">{{CARDS}}</div>
<h2>Max Purchase Limit exposure — {{UCTOT}} under-costed</h2>
<div class="expbox"><div class="eh">Budget rates below today's metal price</div>{{EXP}}
<div style="font-size:11.5px;color:var(--tx2);margin-top:8px">These budgets' <b>system rate</b> can't cover the raw metal at current prices — revisit the max purchase limit before procuring, or they'll overrun.</div></div>
<h2>Upcoming demand by delivery month (kg)</h2>
<div class="chartcard"><div class="ct">Scheduled forward demand (most open demand is backlog, shown as "open demand" above)</div>{{CHART}}
<div class="leg"><span><i style="background:#eb6834"></i>Copper</span><span><i style="background:#2a78d6"></i>Aluminium</span></div></div>
<footer>Source: DEPL/Trico ERP budgets + inventory + live LME/NALCO prices. Generated {{GEN}} UTC. Aggregate view; per-budget detail stays private. <a href="./items.html" style="color:#2a78d6">Live prices →</a> · <a href="./consumption.html" style="color:#2a78d6">Consumption →</a></footer>
</div></body></html>"""


if __name__ == "__main__":
    main()
