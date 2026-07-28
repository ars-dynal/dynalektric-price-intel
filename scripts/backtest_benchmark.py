#!/usr/bin/env python3
"""
Benchmark accuracy back-test (docs/backtest.html).

Question answered: for budgets whose delivery date has already passed
(project finished, purchases done), how well would our CURRENT BENCHMARK
rate have predicted the price actually paid?

Method — strictly out-of-sample, no peeking:
  predicted rate = what the PO-anchor ladder knew ON THE DAY the budget was
                   created (POs strictly BEFORE created_at; fresh 180-day
                   average first, else the trailing 12-month average)
  actual rate    = qty-weighted average price of the REAL POs placed for that
                   item AFTER budget creation, up to delivery + 45 days
  error %        = (predicted - actual) / actual

Lines with no prior PO are reported as "uncovered" (at the time, the system
would have priced them from the live metal estimate or the ERP default —
those can't be reconstructed honestly without historical index data, so they
are counted for coverage but excluded from the accuracy score).

As a baseline, the same error is computed for the TEAM'S OWN budget rate
(vendor_rate/proposed, else system_rate) on the same lines — so the page
shows whether the benchmark beats the manual budget, not just how it scores
in isolation.

Writes: data/backtest.json + docs/backtest.html. Requires DEPL_CLIENT_* env.
"""
import html as H
import json
import os
import statistics
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402

ROOT = erp.ROOT
OUT_JSON = os.path.join(ROOT, "data", "backtest.json")
OUT_HTML = os.path.join(ROOT, "docs", "backtest.html")

FRESH_DAYS = 180          # same "fresh PO" window the live engines use
ACTUAL_GRACE_DAYS = 45    # POs up to delivery + grace count as this project's buying


def inr(n):
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


def rupees(x):
    x = x or 0
    return f"-₹{inr(abs(x))}" if x < 0 else f"₹{inr(x)}"


def esc(x):
    return H.escape(str(x if x is not None else "—"))


def predicted_at(pos, created):
    """PO-anchor prediction using only information available before `created`."""
    prior = [p for p in pos if p["date"] and p["date"] < created]
    if not prior:
        return None, None
    fresh_cut = (datetime.fromisoformat(created) - timedelta(days=FRESH_DAYS)).date().isoformat()
    fresh = [p["price"] for p in prior if p["date"] >= fresh_cut]
    if fresh:
        return statistics.mean(fresh), f"avg of {len(fresh)} PO(s) in the 180d before budget"
    yr_cut = (datetime.fromisoformat(created) - timedelta(days=365)).date().isoformat()
    yr = [p["price"] for p in prior if p["date"] >= yr_cut] or [p["price"] for p in prior]
    return statistics.mean(yr), f"avg of {len(yr)} older PO(s) before budget"


def actual_after(pos, created, window_end):
    """Qty-weighted average of real PO prices in (created .. window_end]."""
    hits = [p for p in pos if p["date"] and created <= p["date"] <= window_end]
    if not hits:
        return None, 0
    wsum = sum((p["qty"] or 0) for p in hits)
    if wsum > 0:
        avg = sum(p["price"] * (p["qty"] or 0) for p in hits) / wsum
    else:
        avg = statistics.mean(p["price"] for p in hits)
    return avg, len(hits)


def main():
    session = erp.make_session()
    print("Fetching items, budgets, PO history (24 mo)...")
    items = erp.fetch_items(session)
    budgets = erp.fetch_budgets(session)
    po = erp.fetch_po_history(session, since_days=730)

    today = datetime.now(timezone.utc).date().isoformat()
    done = [b for b in budgets
            if b.get("lines") and b.get("delivery_date") and b["delivery_date"][:10] < today
            and b.get("created_at")]
    done.sort(key=lambda b: (b.get("created_at") or ""), reverse=True)
    print(f"{len(done)} completed budgets (delivery date passed) of {len(budgets)} total.")

    all_errs, all_team_errs, beat = [], [], 0
    cards, results = [], []
    n_lines_total = n_eval = n_uncovered = n_nopurchase = 0

    for b in done:
        created = b["created_at"][:10]
        wend = (datetime.fromisoformat(b["delivery_date"][:10])
                + timedelta(days=ACTUAL_GRACE_DAYS)).date().isoformat()
        rows, errs, team_errs = [], [], []
        pred_total = act_total = team_total = 0.0
        cov_total = unc = nop = 0

        for l in b["lines"]:
            it = items.get(l["item_id"])
            q = l.get("quantity") or 0
            if not it or q <= 0:
                continue
            cov_total += 1
            pos = po.get(l["item_id"], [])
            actual, n_po = actual_after(pos, created, wend)
            if actual is None:
                nop += 1
                continue
            pred, basis = predicted_at(pos, created)
            team = l.get("vendor_rate") or l.get("system_rate")
            if pred is None:
                unc += 1
                continue
            err = (pred - actual) / actual * 100
            errs.append(err)
            terr = ((team - actual) / actual * 100) if team else None
            if terr is not None:
                team_errs.append(terr)
                if abs(err) < abs(terr):
                    beat += 1
            pred_total += pred * q
            act_total += actual * q
            if team:
                team_total += team * q
            rows.append({"code": it.get("code"), "name": it.get("name"),
                         "qty": q, "pred": round(pred, 2), "basis": basis,
                         "actual": round(actual, 2), "n_po": n_po,
                         "team": team, "err": round(err, 1),
                         "team_err": (round(terr, 1) if terr is not None else None)})

        n_lines_total += cov_total
        n_uncovered += unc
        n_nopurchase += nop
        n_eval += len(errs)
        all_errs.extend(errs)
        all_team_errs.extend(team_errs)
        if not rows:
            continue

        mape = statistics.mean(abs(e) for e in errs)
        results.append({"budget": b["budget_number"], "created": created,
                        "delivery": b["delivery_date"][:10], "lines_total": cov_total,
                        "lines_evaluated": len(errs), "uncovered": unc,
                        "not_purchased": nop,
                        "predicted_total": round(pred_total, 2),
                        "actual_total": round(act_total, 2),
                        "gap": round(pred_total - act_total, 2),
                        "mape_pct": round(mape, 1), "lines": rows})

        rows.sort(key=lambda r: -abs(r["err"]))
        def _row(r):
            cls = "good" if abs(r["err"]) <= 5 else ("mid" if abs(r["err"]) <= 10 else "bad")
            terr = f'{r["team_err"]:+.1f}%' if r["team_err"] is not None else "—"
            return (
                f'<tr><td class="mono">{esc(r["code"])}</td><td class="iname">{esc((r["name"] or "")[:55])}</td>'
                f'<td class="num">{r["qty"]:,.2f}</td>'
                f'<td class="num">₹{r["pred"]:,.2f}<div class="sub2">{esc(r["basis"])}</div></td>'
                f'<td class="num">₹{r["actual"]:,.2f}<div class="sub2">{r["n_po"]} PO(s) after budget</div></td>'
                f'<td class="num {cls}">{r["err"]:+.1f}%</td>'
                f'<td class="num">{terr}</td></tr>')

        body = "".join(_row(r) for r in rows[:10])
        more = (f'<tr><td colspan="7" class="moreln">… {len(rows)-10} more lines in totals</td></tr>'
                if len(rows) > 10 else "")
        gap = pred_total - act_total
        gpct = gap / act_total * 100 if act_total else 0
        cards.append(f'''<details class="bud"><summary>
<span class="c1"><b>{esc(b["budget_number"])}</b><small>created {created} · delivered {esc(b["delivery_date"][:10])} · {len(errs)}/{cov_total} lines evaluated</small></span>
<span class="c2"><small>Predicted</small>{rupees(pred_total)}</span>
<span class="c2"><small>Actually paid</small><b>{rupees(act_total)}</b></span>
<span class="c2"><small>Gap</small>{rupees(gap)} <em>{gpct:+.1f}%</em></span>
<span class="c2"><small>Avg line error</small>{mape:.1f}%</span></summary>
<table><thead><tr><th>Item</th><th>Description</th><th class="num">Qty</th><th class="num">Predicted rate</th><th class="num">Actual paid</th><th class="num">Our error</th><th class="num">Team budget error</th></tr></thead>
<tbody>{body}{more}</tbody></table></details>''')

    # ---- overall summary ----
    if all_errs:
        med = statistics.median(abs(e) for e in all_errs)
        w5 = sum(1 for e in all_errs if abs(e) <= 5) / len(all_errs) * 100
        w10 = sum(1 for e in all_errs if abs(e) <= 10) / len(all_errs) * 100
        bias = statistics.mean(all_errs)
    else:
        med = w5 = w10 = bias = 0.0
    team_med = (statistics.median(abs(e) for e in all_team_errs) if all_team_errs else None)
    beat_pct = (beat / len(all_team_errs) * 100) if all_team_errs else None

    summary = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "completed_budgets": len(done), "budgets_evaluated": len(results),
               "lines_total": n_lines_total, "lines_evaluated": n_eval,
               "lines_uncovered_no_prior_po": n_uncovered,
               "lines_not_purchased_in_window": n_nopurchase,
               "median_abs_error_pct": round(med, 1),
               "within_5pct": round(w5, 1), "within_10pct": round(w10, 1),
               "bias_pct": round(bias, 1),
               "team_median_abs_error_pct": (round(team_med, 1) if team_med is not None else None),
               "benchmark_beats_team_pct": (round(beat_pct, 1) if beat_pct is not None else None),
               "budgets": results}
    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w") as f:
        json.dump(summary, f, indent=1)

    gen = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    beat_chip = (f'<span class="chip"><b>{beat_pct:.0f}%</b><small>lines where benchmark beat the manual budget</small></span>'
                 if beat_pct is not None else "")
    team_chip = (f'<span class="chip"><b>{team_med:.1f}%</b><small>team budget median error (same lines)</small></span>'
                 if team_med is not None else "")
    page = f'''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Benchmark accuracy back-test — Dynalektric</title><style>
:root{{--bg:#f6f5f2;--card:#fff;--ink:#1c1b19;--mut:#7a766e;--line:#e7e4de;--good:#0a7a33;--mid:#b07100;--bad:#b3261e}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:28px 4vw 60px}}
h1{{font-size:26px;margin:0 0 4px}}.sub{{color:var(--mut);max-width:70em}}
.chips{{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0 26px}}
.chip{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 18px;display:flex;flex-direction:column}}
.chip b{{font-size:22px}}.chip small{{color:var(--mut)}}
details.bud{{background:var(--card);border:1px solid var(--line);border-radius:12px;margin:0 0 12px;overflow:hidden}}
summary{{display:flex;gap:22px;align-items:center;padding:14px 18px;cursor:pointer;flex-wrap:wrap}}
summary::-webkit-details-marker{{display:none}}
.c1{{display:flex;flex-direction:column;min-width:240px}}.c1 small{{color:var(--mut)}}
.c2{{display:flex;flex-direction:column;min-width:120px}}.c2 small{{color:var(--mut);font-size:12px}}.c2 em{{font-style:normal;color:var(--mut);font-size:12px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px}}
th,td{{padding:8px 10px;border-top:1px solid var(--line);text-align:left;vertical-align:top}}
th{{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}}
.num{{text-align:right}}.mono{{font-family:Consolas,monospace;font-size:12.5px;white-space:nowrap}}
.iname{{max-width:26em}}.sub2{{color:var(--mut);font-size:11.5px}}
.good{{color:var(--good);font-weight:600}}.mid{{color:var(--mid);font-weight:600}}.bad{{color:var(--bad);font-weight:600}}
.moreln{{color:var(--mut);text-align:center}}
footer{{color:var(--mut);font-size:12.5px;margin-top:30px}}
a{{color:#0a5da0}}
.nav{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}
.nav a{{font-size:12.5px;font-weight:600;color:var(--mut);text-decoration:none;padding:7px 13px;border:1px solid var(--line);border-radius:8px;background:var(--card)}}
.nav a.active{{background:#2a78d6;color:#fff;border-color:#2a78d6}}</style></head><body>
<nav class="nav"><a href="./index.html">Summary</a><a href="./items.html">Items &amp; prices</a><a href="./consumption.html">Consumption &amp; spend</a><a href="./demand.html">Forward demand</a><a href="./budget.html">Max Purchase Limit</a><a href="./backtest.html" class="active">Accuracy</a></nav>
<h1>Benchmark accuracy back-test</h1>
<p class="sub">Out-of-sample validation on <b>completed projects</b> (delivery date passed): the predicted rate uses only POs
that existed <b>before each budget was created</b>; the actual rate is what was really paid <b>after</b>. No peeking.
Uncovered lines (no prior purchase history at the time) are excluded from the score and counted honestly below.</p>
<div class="chips">
<span class="chip"><b>{med:.1f}%</b><small>median rate error (benchmark)</small></span>
{team_chip}{beat_chip}
<span class="chip"><b>{w5:.0f}%</b><small>lines within ±5%</small></span>
<span class="chip"><b>{w10:.0f}%</b><small>lines within ±10%</small></span>
<span class="chip"><b>{bias:+.1f}%</b><small>bias (+ = we over-predict)</small></span>
<span class="chip"><b>{n_eval}</b><small>lines scored · {n_uncovered} uncovered · {n_nopurchase} not purchased in window</small></span>
</div>
{"".join(cards) if cards else '<p class="sub"><b>No completed budgets with scorable lines yet.</b> As projects finish, this page fills up automatically.</p>'}
<footer>Generated {gen} UTC · method: PO-anchor ladder frozen at budget creation date vs qty-weighted actual PO prices until delivery+{ACTUAL_GRACE_DAYS}d ·
<a href="./budget.html">Max Purchase Limit page</a> · <a href="./index.html">Summary</a></footer>
</body></html>'''
    with open(OUT_HTML, "w") as f:
        f.write(page)

    print(f"\nOverall: {n_eval} lines scored across {len(results)} completed budgets")
    print(f"  Median abs error: {med:.1f}%  |  within ±5%: {w5:.0f}%  |  within ±10%: {w10:.0f}%  |  bias {bias:+.1f}%")
    if team_med is not None:
        print(f"  Team budget median error on same lines: {team_med:.1f}%  |  benchmark closer on {beat_pct:.0f}% of lines")
    print(f"Wrote {OUT_JSON} and {OUT_HTML}")


if __name__ == "__main__":
    main()
