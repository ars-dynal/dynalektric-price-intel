#!/usr/bin/env python3
"""
Max Purchase Limit page (docs/budget.html) — recommendation view.

For EVERY budget in the ERP (newest first), every line is priced at "our
rate" — the company's recent real purchase price (data/item_intel.json),
falling back to the live costing-formula estimate for metal items — and
compared with the budgeted system rate:

  per line   : Budget rate vs Our rate -> variance % -> status + action
  per budget : Budget cost vs Current cost, your limit vs SUGGESTED limit
               (current cost + 8% price-risk buffer)

Budgets whose max purchase limit is NOT set yet are pulled to the top with
the suggested limit — so the team gets the number BEFORE deciding, not after.

Click any budget row to expand its line-by-line detail.

Data: ERP budgets API (live) + item_intel.json (weekly PO intel) +
live LME/NALCO/CRGO via the costing formula. Requires DEPL_CLIENT_* env.
"""
import html as H
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402
import costing  # noqa: E402

ROOT = erp.ROOT
RISK_BUFFER = 0.08          # suggested limit = current cost x (1 + 8%)
MAX_BUDGETS = 150
MAX_LINES = 14


def esc(x):
    return H.escape(str(x if x is not None else "—"))


def cr(x):
    x = x or 0
    return (f"₹{x/1e7:.2f} Cr" if abs(x) >= 1e7 else
            (f"₹{x/1e5:.1f} L" if abs(x) >= 1e5 else f"₹{x:,.0f}"))


def rate_fmt(v):
    return f"₹{v:,.2f}" if v else "—"


def our_rate(item, intel_rec, mi, summary, cost_cfg):
    """Recent real PO rate, else live costing estimate for benchmark metals."""
    if intel_rec:
        if intel_rec.get("avg180"):
            return intel_rec["avg180"], "PO"
        if intel_rec.get("avg12"):
            return intel_rec["avg12"], "PO"
    cat = mi["category"]
    if cat in ("Copper", "Aluminium"):
        fi = costing.finished_for(cat, item["name"], summary, cost_cfg)
        if fi:
            return fi["total_ex_gst"], "est"
    if cat == "CRGO":
        p = (summary.get("crgo_steel") or {}).get("price_per_kg")
        if p:
            return float(p), "est"
    if item.get("default_price"):
        return item["default_price"], "erp"
    return None, None


def line_status(sr, our):
    if not sr:
        return "set", "Rate not set", "Set rate ≈ our rate"
    if not our:
        return "na", "No reference", "—"
    v = (our - sr) / sr * 100
    if v > 10:
        return "rev", "Review", "Increase budget before PO"
    if v > 3:
        return "mon", "Monitor", "Check vendor quotes"
    if v < -15:
        return "gen", "Generous", "Verify rate — well above cost"
    return "ok", "OK", "Proceed"


def main():
    params = erp.load_params()
    summary = erp.load_summary()
    cost_cfg, _ = costing.load_cfg_summary()
    intel_all = {}
    try:
        with open(os.path.join(ROOT, "data", "item_intel.json")) as f:
            intel_all = json.load(f).get("items", {})
    except FileNotFoundError:
        pass
    mi_engine = erp.MaterialIntel(params)

    session = erp.make_session()
    print("Fetching items + budgets...")
    items = erp.fetch_items(session)
    budgets = erp.fetch_budgets(session)
    budgets = [b for b in budgets if b.get("lines")]
    budgets.sort(key=lambda b: (b.get("created_at") or ""), reverse=True)
    print(f"{len(budgets)} budgets with lines.")

    cards, cards_nolimit = [], []
    n_rev = n_nolimit = 0
    tot_var = 0.0
    order = {"ok": 0, "gen": 1, "na": 1, "mon": 2, "set": 3, "rev": 4}

    for b in budgets[:MAX_BUDGETS]:
        lrows, bud_cost, cur_cost, worst = [], 0.0, 0.0, "ok"
        for l in b["lines"]:
            it = items.get(l["item_id"])
            if not it or (l.get("quantity") or 0) <= 0:
                continue
            mi = mi_engine.enrich(it)
            our, osrc = our_rate(it, intel_all.get(it.get("code")), mi, summary, cost_cfg)
            sr, q = l.get("system_rate"), l["quantity"]
            bc = (sr or 0) * q
            cc = (our or sr or 0) * q
            bud_cost += bc
            cur_cost += cc
            key, label, action = line_status(sr, our)
            if order[key] > order[worst]:
                worst = key
            var_txt = f"{(our-sr)/sr*100:+.1f}%" if (sr and our) else "—"
            tag = f'<span class="tag">{osrc}</span>' if osrc else ""
            lrows.append((cc, f'<tr><td class="mono">{esc(it.get("code"))}</td>'
                          f'<td class="iname">{esc(it["name"][:60])}</td>'
                          f'<td class="num">{q:,.2f}</td>'
                          f'<td class="num">{rate_fmt(sr)}</td>'
                          f'<td class="num">{rate_fmt(our)}{tag}</td>'
                          f'<td class="num">{var_txt}</td>'
                          f'<td><span class="st st-{key}"></span>{label}</td>'
                          f'<td class="act">{action}</td></tr>'))

        if not lrows:
            continue
        lrows.sort(key=lambda t: -t[0])
        body = "".join(r for _, r in lrows[:MAX_LINES])
        more = (f'<tr><td colspan="8" class="moreln">… {len(lrows)-MAX_LINES} smaller lines '
                f'included in totals</td></tr>' if len(lrows) > MAX_LINES else "")

        limit = b.get("max_purchase_limit_amount")
        suggested = cur_cost * (1 + RISK_BUFFER)
        variance = cur_cost - bud_cost
        tot_var += max(0.0, variance)
        if not limit:
            n_nolimit += 1
            bkey, blabel, baction = "set", "No limit yet", f"Suggested limit: {cr(suggested)}"
        elif worst in ("rev", "set"):
            n_rev += 1
            bkey, blabel, baction = "rev", "Review", ("Increase budget before PO"
                                                      if worst == "rev" else "Set missing line rates")
        elif worst == "mon":
            bkey, blabel, baction = "mon", "Monitor", "Check vendor quotes"
        else:
            bkey, blabel, baction = "ok", "OK", "Proceed"

        var_pct = f"{variance/bud_cost*100:+.1f}%" if bud_cost else "—"
        card = f'''<details class="bud b-{bkey}"><summary>
<span class="c1"><b>{esc(b.get("budget_number"))}</b><small>{esc(b.get("project_code"))} · created {esc((b.get("created_at") or "")[:10])} · delivery {esc(b.get("delivery_date"))}</small></span>
<span class="c2"><small>Budget cost</small>{cr(bud_cost)}</span>
<span class="c2"><small>Current cost</small>{cr(cur_cost)}</span>
<span class="c2"><small>Variance</small>{cr(variance)} <em>{var_pct}</em></span>
<span class="c2"><small>Your limit</small>{cr(limit) if limit else "—"}</span>
<span class="c2"><small>Suggested</small><b>{cr(suggested)}</b></span>
<span class="c3"><span class="st st-{bkey}"></span>{blabel}</span>
<span class="c4">{baction}</span></summary>
<table><thead><tr><th>Item</th><th>Description</th><th class="num">Qty</th><th class="num">Budget rate</th><th class="num">Our rate</th><th class="num">Var %</th><th>Status</th><th>Action</th></tr></thead>
<tbody>{body}{more}</tbody></table></details>'''
        (cards_nolimit if not limit else cards).append(card)

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    page = (TEMPLATE
            .replace("{{NOLIMIT_BLOCK}}",
                     ('<h2>New budgets — limit not set yet: our prediction</h2>'
                      '<p class="h2sub">Set the max purchase limit from the suggested figure '
                      '(current cost of all lines + 8% price-risk buffer). For a BOM not yet '
                      'budgeted in the ERP, drop its PDF into the repo inbox/ for a full prediction.</p>'
                      + "".join(cards_nolimit)) if cards_nolimit else "")
            .replace("{{CARDS}}", "".join(cards))
            .replace("{{NBUD}}", str(len(cards) + len(cards_nolimit)))
            .replace("{{NREV}}", str(n_rev))
            .replace("{{NNL}}", str(n_nolimit))
            .replace("{{TVAR}}", cr(tot_var))
            .replace("{{GEN}}", gen))
    with open(os.path.join(ROOT, "docs", "budget.html"), "w", encoding="utf-8") as f:
        f.write(page)
    print(f"budget.html: {len(cards)+len(cards_nolimit)} budgets ({n_nolimit} without limit, "
          f"{n_rev} need review, positive variance {cr(tot_var)})")


TEMPLATE = r"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dynalektric - Max Purchase Limit</title><style>
:root{color-scheme:light dark;--bg:#f4f4f2;--surf:#fcfcfb;--tx:#0b0b0b;--tx2:#52514e;--mut:#898781;--bd:rgba(11,11,11,.12);--grid:#e6e5df;
--crit:#c8341f;--warn:#c98500;--good:#008300;--blue:#2a78d6;--critt:#fbe9e9;--warnt:#fff6e2;--goodt:#e7f6e7}
@media(prefers-color-scheme:dark){:root{--bg:#0d0d0d;--surf:#1a1a19;--tx:#fff;--tx2:#c3c2b7;--bd:rgba(255,255,255,.14);--grid:#2c2c2a;
--crit:#e05a45;--warn:#e0a000;--good:#27a827;--critt:rgba(230,103,103,.14);--warnt:rgba(250,178,25,.14);--goodt:rgba(12,163,12,.14)}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:22px 20px 60px}
.brand{display:flex;align-items:baseline;gap:9px;margin-bottom:6px}.brand b{font-size:15px}.brand span{font-size:12px;color:var(--mut)}
.nav{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.nav a{font-size:12.5px;font-weight:600;color:var(--tx2);text-decoration:none;padding:7px 13px;border:1px solid var(--bd);border-radius:8px;background:var(--surf)}
.nav a.active{background:var(--blue);color:#fff;border-color:var(--blue)}
h1{font-size:22px;margin:8px 0 4px}.sub{color:var(--tx2);font-size:13px;margin:0 0 14px;max-width:900px;line-height:1.5}
h2{font-size:15px;margin:22px 0 4px}.h2sub{font-size:12px;color:var(--tx2);margin:0 0 10px;line-height:1.5}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
@media(max-width:800px){.kpis{grid-template-columns:repeat(2,1fr)}}
.kpi{background:var(--surf);border:1px solid var(--bd);border-radius:12px;padding:12px 14px}
.kpi .k{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut)}.kpi .v{font-size:21px;font-weight:700;margin-top:2px}
.bud{background:var(--surf);border:1px solid var(--bd);border-radius:12px;margin-bottom:9px;overflow:hidden}
.bud.b-rev{border-left:4px solid var(--crit)}.bud.b-mon{border-left:4px solid var(--warn)}
.bud.b-ok{border-left:4px solid var(--good)}.bud.b-set{border-left:4px solid var(--blue)}
summary{display:grid;grid-template-columns:2.1fr 1fr 1fr 1.15fr .9fr 1fr .95fr 1.5fr;gap:8px;align-items:center;
padding:11px 14px;cursor:pointer;font-size:12.5px;list-style:none}
summary::-webkit-details-marker{display:none}
summary small{display:block;font-size:10px;color:var(--mut);font-weight:400;text-transform:uppercase;letter-spacing:.03em}
.c1 small{text-transform:none;font-size:10.5px}
.c2{font-variant-numeric:tabular-nums}.c2 em{font-style:normal;font-size:10.5px;color:var(--tx2)}
.c3{white-space:nowrap}.c4{font-size:11.5px;color:var(--tx2)}
.st{width:9px;height:9px;border-radius:50%;display:inline-block;margin-right:5px;vertical-align:-1px}
.st-rev{background:var(--crit)}.st-mon{background:var(--warn)}.st-ok{background:var(--good)}
.st-set{background:var(--blue)}.st-gen,.st-na{background:var(--mut)}
.bud table{width:100%;border-collapse:collapse;border-top:1px solid var(--grid)}
.bud thead th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);padding:7px 10px;border-bottom:1px solid var(--grid)}
.bud tbody td{padding:6px 10px;border-bottom:1px solid var(--grid);font-size:12px}
.bud th.num,.bud td.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--tx2)}
.iname{max-width:330px}.act{font-size:11px;color:var(--tx2)}
.tag{font-size:8.5px;font-weight:800;background:var(--goodt);color:var(--good);border-radius:4px;padding:1px 4px;margin-left:4px;vertical-align:1px}
.moreln{font-size:11px;color:var(--mut);text-align:center}
footer{margin-top:20px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><b>Dynalektric</b><span>Max Purchase Limit — recommendations per budget</span></div>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html">Forward demand</a><a href="./budget.html" class="active">Max Purchase Limit</a></nav>
<h1>Budget vs today's cost — with a recommendation for each</h1>
<p class="sub">Newest budgets first. Every line is priced at <b>our rate</b> — the recent real purchase price (green PO tag), else the live costing-formula estimate — and compared with the budgeted rate. Click a budget to open its line-by-line detail. Suggested limit = current cost + 8% price-risk buffer.</p>
<div class="kpis">
 <div class="kpi"><div class="k">Budgets analysed</div><div class="v">{{NBUD}}</div></div>
 <div class="kpi"><div class="k">Need review</div><div class="v" style="color:var(--crit)">{{NREV}}</div></div>
 <div class="kpi"><div class="k">No limit set yet</div><div class="v" style="color:var(--blue)">{{NNL}}</div></div>
 <div class="kpi"><div class="k">Total upward variance</div><div class="v">{{TVAR}}</div></div>
</div>
{{NOLIMIT_BLOCK}}
<h2>All budgets — newest first</h2>
{{CARDS}}
<footer>Source: DEPL/Trico ERP budgets (live) · item purchase intel (weekly) · LME/NALCO/CRGO via the company costing formula. Generated {{GEN}} UTC. Newest {{NBUD}} budgets shown. For a BOM with no budget yet, drop its PDF into the repo <b>inbox/</b> for a full prediction.</footer>
</div></body></html>"""


if __name__ == "__main__":
    main()
