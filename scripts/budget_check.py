#!/usr/bin/env python3
"""
Max Purchase Limit / budget rate checker for Dynalektric.

For every budget in the DEPL/Trico ERP, looks at its metal raw-material lines
(bom_budget_items) and compares the budgeted rate against TODAY'S live metal
price:

  * system_rate  — the ERP's standard rate for that material
  * vendor_rate  — the quoted vendor rate
  * live         — today's benchmark for that metal (₹/kg)

A metal line whose rate is BELOW today's raw-metal price is under-costed — you
cannot even buy the metal at that rate, so the budget's max_purchase_limit is
likely too low. We flag those and estimate the shortfall (kg × price gap).

SAFETY: budget numbers, vendor ids and rupee rates are commercially sensitive.
This writes to data/budget_check.json which is git-ignored and only ever leaves
the runner as a PRIVATE workflow artifact — never committed to the public repo.
Nothing sensitive is printed to the (public) Action log beyond aggregate counts.
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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")
OUT = os.path.join(ROOT, "data", "budget_check.json")
PER_PAGE = 100


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


def main():
    with open(SUMMARY) as f:
        summary = json.load(f)
    live = {"Aluminium": summary["aluminium"]["price_per_kg"],
            "Copper": summary["copper"]["price_per_kg"]}

    token = auth()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    # item_id -> metal (AL/CU); used to identify metal lines in a budget
    item_metal = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        metal = CATEGORY_MAP.get((rec.get("item_category") or {}).get("category_code"))
        if metal:
            item_metal[rec["id"]] = metal
    print(f"Metal items mapped: {len(item_metal)}")

    budgets_out = []
    n_total = n_flagged = 0
    total_shortfall = 0.0
    by_metal = defaultdict(lambda: {"lines": 0, "under_system": 0})

    for b in paginate(session, "/api/external/budgets", {"per_page": PER_PAGE}):
        n_total += 1
        proj = b.get("project") or {}
        lines = []
        for it in (b.get("bom_budget_items") or []):
            metal = item_metal.get(it.get("item_id"))
            if not metal:
                continue
            lv = live.get(metal)
            sysr = fnum(it.get("system_rate"))
            venr = fnum(it.get("vendor_rate"))
            qty = fnum(it.get("quantity")) or 0.0
            by_metal[metal]["lines"] += 1
            under_sys = sysr is not None and lv and sysr < lv
            under_ven = venr is not None and lv and venr < lv
            if under_sys:
                by_metal[metal]["under_system"] += 1
            shortfall = (lv - sysr) * qty if under_sys else 0.0
            total_shortfall += max(0.0, shortfall)
            lines.append({
                "metal": metal, "qty": qty, "system_rate": sysr, "vendor_rate": venr,
                "live": lv, "under_system": under_sys, "under_vendor": under_ven,
                "shortfall_inr": round(shortfall, 0),
            })
        if not lines:
            continue
        flagged = any(l["under_system"] for l in lines)
        if flagged:
            n_flagged += 1
        budgets_out.append({
            "budget_number": b.get("budget_number"),
            "project_code": proj.get("project_code"),
            "delivery_date": proj.get("delivery_date"),
            "created_at": b.get("created_at"),
            "max_purchase_limit_amount": fnum(b.get("max_purchase_limit_amount")),
            "flagged": flagged,
            "metal_lines": lines,
        })

    budgets_out.sort(key=lambda x: -sum(l["shortfall_inr"] for l in x["metal_lines"]))
    out = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "live_prices": live,
        "rule": "A metal budget line is flagged when its system_rate is below today's raw-metal price "
                "(cannot procure the metal at that rate). Shortfall = (live - system_rate) x quantity.",
        "summary": {
            "budgets_with_metal_lines": len(budgets_out),
            "budgets_flagged_undercosted": n_flagged,
            "total_estimated_shortfall_inr": round(total_shortfall, 0),
            "by_metal": {k: v for k, v in by_metal.items()},
        },
        "budgets": budgets_out,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)

    # Aggregate-only to the (public) log:
    print(f"Budgets with metal lines: {len(budgets_out)} | flagged under-costed: {n_flagged}")
    print(f"Estimated total shortfall vs today's metal: Rs {total_shortfall:,.0f}")
    for m, v in by_metal.items():
        print(f"  {m}: {v['lines']} lines, {v['under_system']} below live metal price")
    print(f"Wrote {OUT} (private artifact; not committed)")


if __name__ == "__main__":
    main()
