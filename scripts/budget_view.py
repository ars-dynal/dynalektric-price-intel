#!/usr/bin/env python3
"""
Max Purchase Limit calculator — renders docs/budget.html from ERP budgets.

For every budget, using today's FINISHED landed cost (costing formula) per metal:
  * material cost at finished cost = sum(finished x qty) over its metal lines
  * flags lines whose budgeted system_rate is below today's finished cost
    (the max purchase limit is set too low -> the order will overrun)
  * vendor view: how each vendor_rate compares to finished cost

Answers the original question: "help deciding max purchase limit". The correct
limit for a budget's metal content is the finished cost x quantity at today's
prices; anything budgeted below that is flagged.

Renders an aggregate-friendly page (budget numbers + your own rates; no vendor
identities). Copper figures are estimates until copper costing params are set.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import costing  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COL = {"Copper": "#eb6834", "Aluminium": "#2a78d6"}


def finished_by_metal():
    cfg, summ = costing.load_cfg_summary()
    fin = {}
    for m in ("Copper", "Aluminium"):
        prof = costing.DEFAULT_PROFILE[m]
        if prof in cfg.get("profiles", {}):
            c = costing.finished_cost(dict(cfg["profiles"][prof], name=prof,
                                           gst_pct=cfg.get("gst_pct", 18)), summ)
            fin[m] = {"ex": c["total_ex_gst"], "conf": c["confirmed"]}
    return fin


def cr(x):
    return (f"₹{x/1e7:.2f} Cr" if abs(x) >= 1e7 else (f"₹{x/1e5:.1f} L" if abs(x) >= 1e5 else f"₹{x:,.0f}"))


def analyse(budgets, fin):
    rows = []
    tot_short = 0.0
    tot_under_lines = 0
    ven_above = ven_below = ven_n = 0
    for b in budgets:
        cost_fin = 0.0
        short = 0.0
        under_lines = 0
        kg = 0.0
        for l in b.get("metal_lines", []):
            m = l["metal"]
            f = fin.get(m)
            if not f:
                continue
            q = l.get("kg") or 0.0
            kg += q
            cost_fin += f["ex"] * q
            sr = l.get("system_rate")
            if sr is not None and sr < f["ex"]:
                under_lines += 1
                short += (f["ex"] - sr) * q
            vr = l.get("vendor_rate")
            if vr is not None:
                ven_n += 1
                if vr > f["ex"]:
                    ven_above += 1
                else:
                    ven_below += 1
        if kg <= 0:
            continue
        tot_short += short
        tot_under_lines += under_lines
        rows.append({
            "budget": b.get("budget_number"), "project": b.get("project_code"),
            "delivery": b.get("delivery_month"), "kg": round(kg, 1),
            "cost_fin": round(cost_fin, 0), "limit": b.get("max_purchase_limit_amount"),
            "under_lines": under_lines, "short": round(short, 0),
        })
    rows.sort(key=lambda r: -r["short"])
    return rows, {"budgets": len(rows), "under_lines": tot_under_lines, "shortfall": round(tot_short, 0),
                  "ven_above": ven_above, "ven_below": ven_below, "ven_n": ven_n}


def render(budgets, fin, generated="", note=""):
    rows, s = analyse(budgets, fin)
    fin_cu = fin.get("Copper", {}); fin_al = fin.get("Aluminium", {})
    finref = (f'Copper finished ₹{fin_cu.get("ex",0):,.0f}/kg{"" if fin_cu.get("conf") else " (est.)"} · '
              f'Aluminium finished ₹{fin_al.get("ex",0):,.0f}/kg')
    trs = ""
    for r in rows[:120]:
        flag = (f'<span class="bbadge b-under">{r["under_lines"]} under-costed</span>'
                if r["under_lines"] else '<span class="bbadge b-ok">OK</span>')
        lim = cr(r["limit"]) if r["limit"] else "—"
        trs += (f'<tr><td class="mono">{r["budget"]}</td><td class="mono">{r["project"] or ""}</td>'
                f'<td class="mono">{r["delivery"] or "—"}</td><td class="num">{r["kg"]:,.0f}</td>'
                f'<td class="num">{cr(r["cost_fin"])}</td><td class="num">{lim}</td>'
                f'<td>{flag}</td><td class="num">{cr(r["short"]) if r["short"] else "—"}</td></tr>')
    ven = (f'{s["ven_above"]} of {s["ven_n"]} vendor quotes are ABOVE finished cost, '
           f'{s["ven_below"]} at/below.' if s["ven_n"] else "No vendor rates.")
    return HTML.replace("{{SHORT}}", cr(s["shortfall"])).replace("{{NUNDER}}", str(s["under_lines"])) \
               .replace("{{NBUD}}", str(s["budgets"])).replace("{{FINREF}}", finref) \
               .replace("{{VEN}}", ven).replace("{{ROWS}}", trs) \
               .replace("{{GEN}}", generated).replace("{{NOTE}}", note)


HTML = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dynalektric - Max Purchase Limit</title><style>
:root{color-scheme:light dark;--bg:#f9f9f7;--surf:#fff;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.1);--grid:#e6e5df;--crit:#d03b3b;--good:#0ca30c;--critt:#fbe9e9;--goodt:#e7f6e7}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--bd:rgba(255,255,255,.12);--grid:#2c2c2a;--critt:rgba(208,59,59,.16);--goodt:rgba(12,163,12,.16)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:22px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand b{font-size:15px}.brand span{font-size:12px;color:var(--mut)}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.nav a{font-size:12.5px;font-weight:600;color:var(--tx2);text-decoration:none;padding:7px 13px;border:1px solid var(--bd);border-radius:8px;background:var(--surf)}
.nav a.active{background:#eb6834;color:#fff;border-color:#eb6834}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13px;margin:0 0 16px;max-width:860px;line-height:1.5}
.tot{background:var(--surf);border:1px solid var(--bd);border-left:4px solid var(--crit);border-radius:12px;padding:14px 16px;margin-bottom:14px;font-size:13.5px;line-height:1.5}
.tot b{font-size:26px;color:var(--crit)}
.ref{font-size:12px;color:var(--tx2);margin-bottom:14px}
table{width:100%;border-collapse:collapse;background:var(--surf);border:1px solid var(--bd);border-radius:12px;overflow:hidden}
thead th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);font-weight:600;padding:9px 10px;border-bottom:1px solid var(--grid);white-space:nowrap}
th.num,td.num{text-align:right}tbody td{padding:7px 10px;border-bottom:1px solid var(--grid);font-size:12.5px}
.mono{font-variant-numeric:tabular-nums;color:var(--tx2);font-size:12px}.num{font-variant-numeric:tabular-nums}
.bbadge{font-size:10.5px;font-weight:700;padding:2px 8px;border-radius:5px;white-space:nowrap}
.b-under{background:var(--critt);color:var(--crit)}.b-ok{background:var(--goodt);color:var(--good)}
footer{margin-top:20px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><b>Dynalektric</b><span>Max Purchase Limit — budgets vs today's finished cost</span></div>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html">Forward demand</a><a href="./budget.html" class="active">Max Purchase Limit</a></nav>
<h1>Which budgets are priced below today's cost?</h1>
<p class="sub">For every budget, the correct material cost is <b>finished cost × quantity</b> at today's prices. Lines whose budgeted rate sits below that are flagged — the purchase limit is set too low and the order will overrun. {{NOTE}}</p>
<div class="tot"><b>{{SHORT}}</b> total shortfall across {{NUNDER}} under-costed budget lines in {{NBUD}} budgets, if procured at today's finished cost.</div>
<div class="ref">Finished cost basis: {{FINREF}}. Vendor check: {{VEN}}</div>
<table><thead><tr><th>Budget</th><th>Project</th><th>Delivery</th><th class="num">Metal kg</th><th class="num">Cost @ finished</th><th class="num">Your limit</th><th>Status</th><th class="num">Shortfall</th></tr></thead>
<tbody>{{ROWS}}</tbody></table>
<footer>Source: DEPL/Trico ERP budgets + live LME/NALCO + company costing formula. Generated {{GEN}} UTC. Copper is estimated until copper costing params are confirmed. Top 120 budgets by shortfall shown. <a href="./items.html" style="color:#eb6834">Items →</a></footer>
</div></body></html>"""


def main():
    fin = finished_by_metal()
    path = os.path.join(ROOT, "data", "material_demand.json")
    with open(path) as f:
        budgets = json.load(f).get("budgets", [])
    from datetime import datetime, timezone
    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    html = render(budgets, fin, generated=gen)
    with open(os.path.join(ROOT, "docs", "budget.html"), "w", encoding="utf-8") as f:
        f.write(html)
    rows, s = analyse(budgets, fin)
    print(f"budget.html: {s['budgets']} budgets, {s['under_lines']} under-costed lines, "
          f"shortfall {cr(s['shortfall'])}")


if __name__ == "__main__":
    main()
