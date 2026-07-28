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
# Suggested limit = current cost x (1 + buffer%). The buffer covers market
# movement between budget approval and PO release (leads run 21-45 days).
# Policy lives in data/planning_params.json (suggested_limit_buffer_pct);
# 8% fallback if the key is absent.
def _risk_buffer():
    try:
        return float(erp.load_params().get("suggested_limit_buffer_pct", 8.0)) / 100.0
    except Exception:
        return 0.08


RISK_BUFFER = _risk_buffer()
MAX_BUDGETS = 150
MAX_LINES = 14


def esc(x):
    return H.escape(str(x if x is not None else "—"))


def cr(x):
    """Exact rupees, Indian digit grouping, ERP-style — NEVER lakh-rounded.
    Budget decisions here are rupee-sensitive: ₹9,26,601 and ₹9,26,589 both
    read '₹9.3 L' when compacted, hiding real differences."""
    x = x or 0
    return f"-₹{inr(abs(x))}" if x < 0 else f"₹{inr(x)}"


def rate_fmt(v):
    return f"₹{v:,.2f}" if v else "—"


def inr(n):
    """Indian digit grouping: 276450 -> 2,76,450 (matches the ERP screens)."""
    n = int(round(n))
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


def rate_amt(rate, qty):
    """Rate with the line AMOUNT beneath it — same figure the ERP screen shows."""
    if not rate:
        return "—"
    return f'{rate_fmt(rate)}<div class="amt">₹{inr(rate * qty)}</div>' 


def our_rate(item, intel_rec, mi, summary, cost_cfg, drift_pct=5.0):
    """Recent real PO rate, unless the metal market has drifted away from it.

    A real PO is the best anchor only while it still represents today's
    market. For indexed metals (Cu/Al/CRGO) we always compute the live
    costing estimate too; if it differs from the PO by more than
    drift_pct (either direction), the PO is stale — a supplier will quote
    today's metal, not June's — so we switch to the estimate and say why.
    Returns (rate, src_tag, drift_note_or_None).
    """
    po = None
    if intel_rec:
        po = intel_rec.get("avg180") or intel_rec.get("avg12")

    cat = mi["category"]
    bench = None
    if cat in ("Copper", "Aluminium"):
        fi = costing.finished_for(cat, item["name"], summary, cost_cfg)
        if fi:
            bench = fi["total_ex_gst"]
    elif cat == "CRGO":
        p = (summary.get("crgo_steel") or {}).get("price_per_kg")
        if p:
            bench = float(p)

    if po:
        if bench:
            drift = (bench - po) / po * 100
            if abs(drift) > drift_pct:
                word = "risen" if drift > 0 else "fallen"
                return bench, "est", (
                    f"metal market has {word} {drift:+.1f}% vs our PO ₹{po:,.2f} — "
                    f"PO no longer representative, using today's cost ₹{bench:,.2f}"), None
            # Indexed item, PO within threshold of today's market — that IS
            # the proof the PO is still representative.
            return po, "PO", None, {"lv": "high",
                                    "txt": f"index-validated: live estimate within {abs(drift):.1f}% of this PO"}
        return po, "PO", None, po_confidence(intel_rec)
    if bench:
        return bench, "est", None, None
    if item.get("default_price"):
        return item["default_price"], "erp", None, None
    return None, None, None, None


CONF_LABEL = {"high": "High", "med": "Medium", "low": "Low", "stale": "Stale"}


def po_confidence(intel_rec):
    """Trust rating for a PO-based rate on a NON-INDEXED item (relays,
    contactors, sockets...). No market index exists to validate against, so
    the honest proof is the evidence itself: how old the PO is, how many POs
    back it up, and whether more than one vendor has charged similar money.
      < 3 months  -> High     3-6 months -> Medium
      6-12 months -> Low (verify with a quote)
      > 12 months -> Stale (request a fresh quotation)
    Single-PO history caps the rating at Medium — one data point is not a
    market. Two vendors within 5% of each other lifts Low back to Medium."""
    lpod = (intel_rec or {}).get("lpod")
    if not lpod:
        return None
    try:
        age = (datetime.now(timezone.utc).date() - datetime.fromisoformat(lpod[:10]).date()).days
    except ValueError:
        return None
    npo = (intel_rec or {}).get("npo") or 0
    vend = (intel_rec or {}).get("vend") or []
    months = age / 30.4
    if age < 90:
        lv = "high"
    elif age < 180:
        lv = "med"
    elif age < 365:
        lv = "low"
    else:
        lv = "stale"
    if lv == "high" and npo <= 1:
        lv = "med"          # one purchase ever — thin evidence
    if lv == "low" and len(vend) >= 2:
        prices = [v["p"] for v in vend[:2] if v.get("p")]
        if len(prices) == 2 and abs(prices[0] - prices[1]) / max(prices) <= 0.05:
            lv = "med"      # two independent vendors agree — stronger than age alone
    ev = f"PO {months:.0f} month(s) old, {npo} PO(s), {len(vend)} vendor(s)"
    advice = {"high": "", "med": "",
              "low": " — verify with a fresh quote before large POs",
              "stale": " — request a fresh supplier quotation; do not rely on this price"}[lv]
    return {"lv": lv, "txt": ev + advice}


def _src_phrase(osrc, lpod):
    if osrc == "PO":
        return f"our {lpod} PO" if lpod else "our recent PO avg"
    if osrc == "est":
        return "today's costing estimate (LME/NALCO)"
    return "the ERP default price"


def line_status(ref, our, ref_is_proposed, osrc=None, lpod=None, qty=0.0):
    """Status + a WHY sentence with the actual numbers, so the action is
    self-explanatory: which two prices disagree, by how much, worth what."""
    if not ref:
        if our:
            return "set", "Rate not set", f"No rate in ERP — enter ≈ ₹{our:,.2f} ({_src_phrase(osrc, lpod)})"
        return "set", "Rate not set", "No rate in ERP and no price reference — get a quote"
    if not our:
        return "na", "No reference", "No purchase history or benchmark — judge manually"
    v = (our - ref) / ref * 100
    gap_amt = abs(our - ref) * qty
    src = _src_phrase(osrc, lpod)
    if v > 10:
        return ("rev", "Review",
                f"Budget ₹{ref:,.2f} but real cost ₹{our:,.2f} ({src}) — {v:+.0f}%, "
                f"overrun ≈ ₹{inr(gap_amt)} on this line. Increase before PO")
    if v > 3:
        return ("mon", "Monitor",
                f"Real cost ₹{our:,.2f} ({src}) is {v:+.0f}% above budget — ₹{inr(gap_amt)} at risk")
    if ref_is_proposed and v < -3:
        return ("neg", "Negotiate",
                f"Quote ₹{ref:,.2f} vs {src} ₹{our:,.2f} — ask ₹{abs(our-ref):,.0f} less "
                f"(₹{inr(gap_amt)} saving on this line)")
    if v < -15:
        return ("gen", "Generous",
                f"Budget ₹{ref:,.2f} far above real cost ₹{our:,.2f} ({src}) — verify the entry")
    return "ok", "OK", f"Matches {src} within {v:+.1f}% — proceed"


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
    order = {"ok": 0, "gen": 1, "na": 1, "neg": 2, "mon": 2, "set": 3, "rev": 4}

    for b in budgets[:MAX_BUDGETS]:
        lrows, bud_cost, cur_cost, worst = [], 0.0, 0.0, "ok"
        for l in b["lines"]:
            it = items.get(l["item_id"])
            if not it or (l.get("quantity") or 0) <= 0:
                continue
            mi = mi_engine.enrich(it)
            irec = intel_all.get(it.get("code"))
            our, osrc, drift_note, conf = our_rate(it, irec, mi, summary, cost_cfg,
                                                   params.get("po_drift_threshold_pct", 5.0))
            lpod = (irec or {}).get("lpod")
            sr, vr, q = l.get("system_rate"), l.get("vendor_rate"), l["quantity"]
            ref = vr or sr           # the team's PROPOSED rate wins over the old system rate
            bc = (ref or 0) * q
            cc = (our or ref or 0) * q
            bud_cost += bc
            cur_cost += cc
            key, label, action = line_status(ref, our, bool(vr), osrc, lpod, q)
            if drift_note:
                action = f"{action}. ({drift_note})"
            if conf:
                action = (f"{action} · Confidence: {CONF_LABEL[conf['lv']]} ({conf['txt']})")
                # A stale price reference makes any verdict unreliable —
                # never let a >12-month-old PO produce a green "proceed".
                if conf["lv"] == "stale" and key == "ok":
                    key, label = "mon", "Verify"
            if order.get(key, 1) > order.get(worst, 0):
                worst = key
            var_txt = f"{(our-ref)/ref*100:+.1f}%" if (ref and our) else "—"
            tag = f'<span class="tag">{osrc}</span>' if osrc else ""
            if conf:
                tag += f'<span class="cf cf-{conf["lv"]}" title="{esc(conf["txt"])}">{CONF_LABEL[conf["lv"]]}</span>'
            lrows.append((cc, f'<tr><td class="mono">{esc(it.get("code"))}</td>'
                          f'<td class="iname">{esc(it["name"][:60])}</td>'
                          f'<td class="num">{q:,.2f}</td>'
                          f'<td class="num">{rate_fmt(sr)}</td>'
                          f'<td class="num">{rate_amt(vr, q)}</td>'
                          f'<td class="num">{rate_amt(our, q)}{tag}</td>'
                          f'<td class="num">{var_txt}</td>'
                          f'<td><span class="st st-{key}"></span>{label}</td>'
                          f'<td class="act">{action}</td></tr>'))

        if not lrows:
            continue
        lrows.sort(key=lambda t: -t[0])
        body = "".join(r for _, r in lrows[:MAX_LINES])
        more = (f'<tr><td colspan="9" class="moreln">… {len(lrows)-MAX_LINES} smaller lines '
                f'included in totals</td></tr>' if len(lrows) > MAX_LINES else "")

        limit = b.get("max_purchase_limit_amount")
        suggested = cur_cost * (1 + RISK_BUFFER)
        variance = cur_cost - bud_cost
        tot_var += max(0.0, variance)
        # Header verdict policy: actions are triggered by the VALIDATED number
        # (current benchmark = all lines at our rates), never by the buffered
        # suggestion. The buffer is context for setting a NEW limit, not
        # grounds to raise an existing one.
        buf_pct = RISK_BUFFER * 100
        if not limit:
            n_nolimit += 1
            bkey, blabel, baction = "set", "No limit yet", (
                f"Benchmark {cr(cur_cost)} + {buf_pct:g}% buffer → suggest {cr(suggested)}")
        elif limit < cur_cost:
            # The only case where "increase the budget" is justified by totals:
            # the approved ceiling is below today's real cost.
            n_rev += 1
            bkey, blabel, baction = "rev", "Review", (
                f"Limit is {cr(cur_cost - limit)} below today's cost — increase before PO")
        elif worst == "set":
            n_rev += 1
            bkey, blabel, baction = "rev", "Review", "Set missing line rates"
        elif worst == "rev":
            n_rev += 1
            bkey, blabel, baction = "rev", "Review", (
                "Revise overrun lines before PO (total limit still covers today's cost)")
        elif worst == "neg":
            bkey, blabel, baction = "mon", "Negotiate", "Quotes above recent buying price — negotiate"
        elif worst == "mon":
            bkey, blabel, baction = "mon", "Monitor", "Check vendor quotes"
        elif limit < suggested:
            bkey, blabel, baction = "ok", "OK", (
                f"Limit covers today's cost; headroom under the {buf_pct:g}% buffer — watch the market")
        else:
            bkey, blabel, baction = "ok", "OK", "Proceed"

        var_pct = f"{variance/bud_cost*100:+.1f}%" if bud_cost else "—"
        card = f'''<details class="bud b-{bkey}"><summary>
<span class="c1"><b>{esc(b.get("budget_number"))}</b><small>{esc(b.get("project_code"))} · created {esc((b.get("created_at") or "")[:10])} · delivery {esc(b.get("delivery_date"))}</small></span>
<span class="c2"><small>Budget cost</small>{cr(bud_cost)}</span>
<span class="c2"><small>Current benchmark</small><b>{cr(cur_cost)}</b></span>
<span class="c2"><small>Variance</small>{cr(variance)} <em>{var_pct}</em></span>
<span class="c2"><small>Your limit</small>{cr(limit) if limit else "—"}</span>
<span class="c2"><small>+{RISK_BUFFER*100:g}% buffer</small>{cr(suggested)}</span>
<span class="c3"><span class="st st-{bkey}"></span>{blabel}</span>
<span class="c4">{baction}</span></summary>
<table><thead><tr><th>Item</th><th>Description</th><th class="num">Qty</th><th class="num">System rate</th><th class="num">Proposed rate<br><small>amount</small></th><th class="num">Our rate<br><small>amount</small></th><th class="num">Var %</th><th>Status</th><th>Action</th></tr></thead>
<tbody>{body}{more}</tbody></table></details>'''
        (cards_nolimit if not limit else cards).append(card)

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    page = (TEMPLATE
            .replace("{{NOLIMIT_BLOCK}}",
                     ('<h2>New budgets — limit not set yet: our prediction</h2>'
                      '<p class="h2sub">Set the max purchase limit from the suggested figure '
                      '(current benchmark = all lines at our rates, plus the price-risk buffer '
                      'shown separately). For a BOM not yet '
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
.st-set{background:var(--blue)}.st-neg{background:var(--warn)}.st-gen,.st-na{background:var(--mut)}
.bud table{width:100%;border-collapse:collapse;border-top:1px solid var(--grid)}
.bud thead th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut);padding:7px 10px;border-bottom:1px solid var(--grid)}
.bud tbody td{padding:6px 10px;border-bottom:1px solid var(--grid);font-size:12px}
.bud th.num,.bud td.num{text-align:right;font-variant-numeric:tabular-nums}
.mono{font-family:ui-monospace,Consolas,monospace;font-size:11px;color:var(--tx2)}
.iname{max-width:300px}.act{font-size:10.5px;color:var(--tx2);max-width:260px;line-height:1.45}
.tag{font-size:8.5px;font-weight:800;background:var(--goodt);color:var(--good);border-radius:4px;padding:1px 4px;margin-left:4px;vertical-align:1px}
.cf{font-size:8.5px;font-weight:800;border-radius:4px;padding:1px 4px;margin-left:3px;vertical-align:1px;cursor:help}
.cf-high{background:#e3f2e6;color:#0a7a33}.cf-med{background:#fdf3dd;color:#8a6100}
.cf-low{background:#fde8dd;color:#a34d00}.cf-stale{background:#fbe0de;color:#b3261e}
.amt{font-size:10px;color:var(--mut);font-variant-numeric:tabular-nums}
.moreln{font-size:11px;color:var(--mut);text-align:center}
footer{margin-top:20px;font-size:11px;color:var(--mut);border-top:1px solid var(--grid);padding-top:12px;line-height:1.6}
</style></head><body><div class="wrap">
<div class="brand"><b>Dynalektric</b><span>Max Purchase Limit — recommendations per budget</span></div>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html">Forward demand</a><a href="./budget.html" class="active">Max Purchase Limit</a><a href="./backtest.html">Accuracy</a></nav>
<h1>Budget vs today's cost — with a recommendation for each</h1>
<p class="sub">Newest budgets first. Every line is priced at <b>our rate</b> — the recent real purchase price (green PO tag), else the live costing-formula estimate — and compared with the budget's proposed rate (or the system rate when no proposal exists). Click a budget to open its line-by-line detail. <b>Current benchmark</b> = all lines at our rates — the validated figure actions are judged against. The <b>buffer column</b> (benchmark + price-risk %) is context for setting a new limit, not a reason to raise an existing one. Every PO-based rate carries a <b>confidence badge</b>: metals are index-validated against today's LME/NALCO; non-indexed items (relays, contactors, sockets…) are rated by evidence — PO age, number of POs, vendor agreement (High &lt;3mo · Medium 3–6mo · Low 6–12mo · Stale &gt;12mo, hover for the reason).</p>
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
