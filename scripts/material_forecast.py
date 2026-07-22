#!/usr/bin/env python3
"""
Material Consumption Forecast for Dynalektric's base metals.

PRIMARY signal (available today): purchase-order history. How many kg of each
raw metal were purchased per month is a direct, procurement-relevant proxy for
consumption. We build a monthly kg series per metal, forecast the next few
months (trailing average + linear trend), and multiply by the live price to get
a forward SPEND estimate.

This run also prints the FIELD-NAME SCHEMA (names only, never values) of the
boms / budgets / inventory endpoints, so the richer BOM-explosion x budgeted-
demand version can be built next without a separate discovery run.

SAFETY: this runs from a PUBLIC repo with public Action logs. It writes ONLY
aggregate monthly kg / forecast / spend per metal to data/consumption_forecast.json
— never item codes, names, vendors, or rupee line prices. Schema dumps are key
names + value TYPES only.
"""
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

BASE = "https://depl.consult-trico.com"
CATEGORY_MAP = {"AL-222-": "Aluminium", "CU-222-": "Copper"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")
OUT = os.path.join(ROOT, "data", "consumption_forecast.json")

# Candidate field names for a PO line-item quantity (auto-detected at runtime).
QTY_KEYS = ["quantity", "qty", "order_quantity", "order_qty", "po_quantity",
            "required_quantity", "item_quantity", "received_quantity"]
PER_PAGE = 100
FORECAST_MONTHS = 3


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


def schema_dump(session, name, path):
    """Print key names + value types only (never values) — for public logs."""
    print(f"\n=== SCHEMA: {name} ({path}) ===")
    try:
        r = session.get(f"{BASE}{path}", params={"per_page": 2}, timeout=30)
        print("HTTP", r.status_code)
        if r.status_code != 200:
            return
        pg = r.json().get("data", {})
        recs = pg.get("data", []) if isinstance(pg, dict) else []
        print(f"total={pg.get('total')} per_page={pg.get('per_page')} on_page={len(recs)}")
        if not recs:
            print("(no records)"); return

        def walk(v, d=0):
            ind = "  " * d
            if isinstance(v, dict):
                for k, val in v.items():
                    print(f"{ind}{k}: {type(val).__name__}")
                    if isinstance(val, (dict, list)) and d < 2:
                        walk(val, d + 1)
            elif isinstance(v, list) and v:
                print(f"{ind}[0]:"); walk(v[0], d + 1)
        walk(recs[0])
    except Exception as e:
        print(f"schema error: {type(e).__name__}: {e}")


def detect_qty(line):
    for k in QTY_KEYS:
        if k in line and line[k] not in (None, ""):
            try:
                return float(line[k])
            except (TypeError, ValueError):
                pass
    return None


def lin(ys):
    n = len(ys); xs = list(range(n))
    sx, sy = sum(xs), sum(ys)
    sxy = sum(x * y for x, y in zip(xs, ys)); sxx = sum(x * x for x in xs)
    den = (n * sxx - sx * sx) or 1
    b = (n * sxy - sx * sy) / den
    return (sy - b * sx) / n, b


def month_of(po_date):
    """Return YYYY-MM from an ERP date. Trico uses DD/MM/YYYY; also tolerate ISO."""
    s = (po_date or "").strip()
    if not s:
        return None
    if "/" in s:
        parts = s.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            d, mo, y = parts
            try:
                return f"{int(y):04d}-{int(mo):02d}"
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


def main():
    with open(SUMMARY) as f:
        summary = json.load(f)
    price = {"Aluminium": summary["aluminium"]["price_per_kg"],
             "Copper": summary["copper"]["price_per_kg"]}

    print("Authenticating...")
    token = auth()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    # --- schema discovery for the not-yet-modelled endpoints (names only) ---
    for nm, p in [("BOMs", "/api/external/boms"),
                  ("Budgets", "/api/external/budgets"),
                  ("Inventory", "/api/external/inventory")]:
        schema_dump(session, nm, p)

    # --- item_id -> metal (KGS-priced AL/CU only) ---
    print("\nFetching items...")
    item_metal = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        code = (rec.get("item_category") or {}).get("category_code")
        metal = CATEGORY_MAP.get(code)
        uom = (rec.get("uom") or {}).get("name")
        if metal and uom == "KGS":
            item_metal[rec["id"]] = metal
    print(f"KGS-priced metal items: {len(item_metal)}")

    # --- monthly kg purchased per metal, from PO lines ---
    print("Fetching purchase orders...")
    monthly = defaultdict(lambda: defaultdict(float))  # metal -> {YYYY-MM: kg}
    qty_field_seen, lines_seen, lines_qty = set(), 0, 0
    for po in paginate(session, "/api/external/purchase-orders", {"per_page": PER_PAGE}):
        pod = month_of(po.get("po_date"))  # -> YYYY-MM (Trico dates are DD/MM/YYYY)
        if not pod:
            continue
        for line in (po.get("items") or []):
            iid = line.get("item_id")
            metal = item_metal.get(iid)
            if not metal:
                continue
            lines_seen += 1
            q = detect_qty(line)
            if q is None:
                continue
            for k in QTY_KEYS:
                if k in line and line[k] not in (None, ""):
                    qty_field_seen.add(k); break
            lines_qty += 1
            monthly[metal][pod] += q

    print(f"PO metal lines: {lines_seen}, with a detectable quantity: {lines_qty}, "
          f"qty field(s) used: {sorted(qty_field_seen) or 'NONE (need to map manually)'}")

    # --- build series + forecast per metal ---
    out = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "unit": "kg_purchased_per_month",
           "note": "Monthly kg of each raw metal PURCHASED (proxy for consumption) from PO history. "
                   "Forecast = trailing linear trend. Spend = forecast kg x today's live price. "
                   "Aggregate only; no item/vendor/price-line detail.",
           "qty_field_used": sorted(qty_field_seen),
           "metals": {}}
    for metal, series in monthly.items():
        months = sorted(series)
        if len(months) < 3:
            out["metals"][metal] = {"insufficient_data": True,
                                    "monthly": [{"m": m, "kg": round(series[m], 1)} for m in months]}
            continue
        vals = [series[m] for m in months]
        a, b = lin(vals[-min(12, len(vals)):])
        base_i = len(vals) - 1
        fc = []
        for k in range(1, FORECAST_MONTHS + 1):
            fc.append({"m": add_months(months[-1], k), "kg": round(max(0.0, a + b * (base_i + k)), 1)})
        avg3 = statistics.mean(vals[-3:])
        fc_total = sum(p["kg"] for p in fc)
        spend = round(fc_total * price.get(metal, 0), 0)
        out["metals"][metal] = {
            "monthly": [{"m": m, "kg": round(series[m], 1)} for m in months],
            "forecast": fc,
            "avg3_kg": round(avg3, 1),
            "forecast_total_kg": round(fc_total, 1),
            "forecast_spend_inr": spend,
            "price_per_kg": price.get(metal),
        }

    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nWrote {OUT}")
    for m, d in out["metals"].items():
        if "forecast_total_kg" in d:
            print(f"  {m}: next {FORECAST_MONTHS}mo ~{d['forecast_total_kg']:,.0f} kg "
                  f"(~Rs {d['forecast_spend_inr']:,.0f} at today's price)")


if __name__ == "__main__":
    main()
