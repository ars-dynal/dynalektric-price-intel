#!/usr/bin/env python3
"""
Live ERP-driven commodity price analysis for Dynalektric.

Pulls Items + Purchase Orders from the DEPL ERP API, filters to aluminium/
copper raw-material items (item_category.category_code 'AL-222-'/'CU-222-',
priced in KGS, not a finished/insulated form), and uses REAL purchase-order
transaction prices to:

  1. Calibrate each category's premium over today's raw-metal price from
     actual recent purchases, instead of the old "near-market items" proxy.
     This works for copper too, unlike the proxy method, since real PO
     history exists regardless of whether the item master's static
     "default_price" field was ever updated.
  2. Rank "frequently used" items by real purchase-order count, instead of
     the warehouse/pallet-touch proxy used before the ERP API was connected.

SAFETY: this script is run from a PUBLIC repo whose Action logs are public.
It must NEVER print or write raw item codes, item names, vendor names/ids,
or rupee prices to stdout or to any committed file. Only aggregate counts,
medians, and category-level confidence labels are ever emitted. All raw
per-item/per-PO data stays in memory for the duration of the run and is
discarded when the job ends.
"""
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

BASE = "https://depl.consult-trico.com"
# Confirmed against live data (2026-07-22 debug run): item_category.category_code
# is 'AL-222-' for every aluminium item and 'CU-222-' for every copper item.
# There is no live-data equivalent of the old AL1/AL2/CU1/CU2 foil-vs-strip
# split (a code-prefix check across 119 real AL/CU items found 0 matches on
# any AL1/AL2/CU1/CU2 prefix) - so this collapses to two categories.
CATEGORY_MAP = {"AL-222-": "AL", "CU-222-": "CU"}
METAL_CATEGORIES = {"AL", "CU"}
EXCLUDE_KW = re.compile(r'cable|panel|plate|wire|busbar|desk|sleeve', re.I)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_PATH = os.path.join(ROOT, 'data', 'public_summary.json')

PER_PAGE = 100
REQUEST_DELAY_S = 0.15  # be a polite API citizen


def auth():
    cid = os.environ["DEPL_CLIENT_ID"]
    secret = os.environ["DEPL_CLIENT_SECRET"]
    r = requests.post(f"{BASE}/oauth/token", json={
        "grant_type": "client_credentials", "client_id": cid, "client_secret": secret
    }, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def paginate(session, path, params):
    """Yields records across all pages. Explicitly re-applies `params` (e.g.
    per_page) on every request, not just the first - Laravel's next_page_url
    does not reliably preserve custom per_page here, which previously caused
    pagination to silently fall back to a small default page size on every
    page after the first (confirmed via debug run: 201 pages for what should
    have been ~30-90 pages at per_page=100)."""
    url = f"{BASE}{path}"
    desired_params = dict(params)
    while url:
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        query.update(desired_params)
        full_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
        r = session.get(full_url, timeout=30)
        r.raise_for_status()
        body = r.json()
        paginator = body.get("data", {})
        records = paginator.get("data", [])
        for rec in records:
            yield rec
        url = paginator.get("next_page_url")
        time.sleep(REQUEST_DELAY_S)


def fetch_metal_items(session):
    """Returns dict: item_id -> {category, uom_name, default_price, name_excluded}"""
    items = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        code = (rec.get("item_category") or {}).get("category_code")
        cat = CATEGORY_MAP.get(code)
        if not cat:
            continue
        uom_name = (rec.get("uom") or {}).get("name")
        name = rec.get("name") or ""
        items[rec["id"]] = {
            "category": cat,
            "uom": uom_name,
            "default_price": rec.get("default_price"),
            "excluded_form": bool(EXCLUDE_KW.search(name)),
        }
    return items


def fetch_po_lines_for_items(session, item_ids):
    """Returns dict: item_id -> list of (po_date_str, price_float)"""
    history = defaultdict(list)
    for po in paginate(session, "/api/external/purchase-orders", {"per_page": PER_PAGE}):
        po_date = po.get("po_date")
        for line in (po.get("items") or []):
            iid = line.get("item_id")
            if iid in item_ids:
                try:
                    price = float(line.get("price") or 0)
                except (TypeError, ValueError):
                    price = 0.0
                if price > 0:
                    history[iid].append((po_date, price))
    return history


def main():
    with open(SUMMARY_PATH) as f:
        summary = json.load(f)
    al_market = summary["aluminium"]["price_per_kg"]
    cu_market = summary["copper"]["price_per_kg"]

    def market_price(cat):
        return al_market if cat.startswith("AL") else cu_market

    print("Authenticating...")
    token = auth()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})

    print("Fetching items (categories AL-222-/CU-222- only)...")
    items = fetch_metal_items(session)
    print(f"Matched {len(items)} aluminium/copper items across all categories (counts only, no codes/names).")

    comparable_ids = {iid for iid, it in items.items()
                       if it["uom"] == "KGS" and not it["excluded_form"]}
    print(f"{len(comparable_ids)} of those are KGS-priced, non-finished raw forms (comparable set).")

    print("Fetching purchase order history for comparable items (this can take a few minutes)...")
    po_history = fetch_po_lines_for_items(session, comparable_ids)
    items_with_po_history = len(po_history)
    print(f"{items_with_po_history} comparable items have at least one purchase order on record.")

    # --- calibrate premium per category from REAL recent purchase prices ---
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")
    premiums_by_cat = defaultdict(list)
    freq_count = {}  # item_id -> number of PO lines (real usage frequency)

    for iid, history in po_history.items():
        cat = items[iid]["category"]
        recent = [(d, p) for d, p in history if (d or "0000-00-00") >= cutoff]
        use_history = recent if recent else history  # fall back to all-time if nothing recent
        if not use_history:
            continue
        avg_price = statistics.mean(p for _, p in use_history)
        mp = market_price(cat)
        premium = avg_price / mp - 1
        premiums_by_cat[cat].append(premium)
        freq_count[iid] = len(history)

    learned_premium, confidence, sample_n = {}, {}, {}
    for cat in METAL_CATEGORIES:
        vals = premiums_by_cat.get(cat, [])
        sample_n[cat] = len(vals)
        if len(vals) >= 15:
            confidence[cat] = "High"
        elif len(vals) >= 3:
            confidence[cat] = "Medium"
        elif len(vals) >= 1:
            confidence[cat] = "Low"
        else:
            confidence[cat] = "Unverified"
        learned_premium[cat] = statistics.median(vals) if vals else None

    fallback_vals = [v for vals in premiums_by_cat.values() for v in vals]
    fallback = statistics.median(fallback_vals) if fallback_vals else 0.0
    for cat in METAL_CATEGORIES:
        if learned_premium[cat] is None:
            learned_premium[cat] = fallback

    print("\nCalibrated premiums (median, real PO data, no item-level values shown):")
    for cat in sorted(METAL_CATEGORIES):
        print(f"  {cat}: n={sample_n[cat]} samples, confidence={confidence[cat]}, "
              f"premium={learned_premium[cat]*100:.1f}%")

    # --- broad signal counts: default_price vs raw metal, no premium ---
    broad_crit = broad_review = broad_ok = broad_flagged = 0
    flag_floor = {"AL": 100, "CU": 200}
    broad_total = 0
    for iid in comparable_ids:
        dp = items[iid]["default_price"]
        if not dp or dp <= 0:
            continue
        cat = items[iid]["category"]
        if dp < flag_floor[cat]:
            broad_flagged += 1
            continue
        broad_total += 1
        gap = (market_price(cat) - dp) / dp * 100
        if gap >= 50:
            broad_crit += 1
        elif gap >= 15:
            broad_review += 1
        else:
            broad_ok += 1

    # --- pilot signal counts: top 18 by REAL purchase frequency, premium-adjusted ---
    ranked = sorted(freq_count.items(), key=lambda kv: -kv[1])[:18]
    pilot_crit = pilot_review = pilot_ok = 0
    for iid, _ in ranked:
        cat = items[iid]["category"]
        history = po_history[iid]
        recent = [(d, p) for d, p in history if (d or "0000-00-00") >= cutoff] or history
        avg_price = statistics.mean(p for _, p in recent)
        suggested = market_price(cat) * (1 + learned_premium[cat])
        gap = (suggested - avg_price) / avg_price * 100
        if abs(gap) >= 40:
            pilot_crit += 1
        elif abs(gap) >= 15:
            pilot_review += 1
        else:
            pilot_ok += 1

    print(f"\nPilot ({len(ranked)} most-frequently-purchased items, real PO-based): "
          f"{pilot_crit} critical / {pilot_review} review / {pilot_ok} on track")
    print(f"Broad ({broad_total} priced comparable items): "
          f"{broad_crit} critical / {broad_review} review / {broad_ok} on track, "
          f"{broad_flagged} data-quality flags")

    # --- write back ONLY aggregate fields ---
    summary["pilot_signals"] = {
        "total_items": len(ranked),
        "critical": pilot_crit,
        "review": pilot_review,
        "on_track": pilot_ok,
        "note": ("Top items ranked by REAL purchase-order frequency (live ERP data), "
                 "premium-adjusted using calibrated premiums from actual recent purchase prices. "
                 "No item codes or rupee amounts shown publicly.")
    }
    summary["broad_signals"] = {
        "total_items": broad_total,
        "critical_ge_50pct": broad_crit,
        "review_15_to_50pct": broad_review,
        "on_track_lt_15pct": broad_ok,
        "data_quality_flags": broad_flagged,
        "note": "All priced, comparable aluminium/copper items (live ERP), vs. today's metal price, no premium adjustment."
    }
    summary["category_confidence"] = {
        "aluminium": f"{confidence['AL']} (n={sample_n['AL']} real POs, last 12mo preferred)",
        "copper": f"{confidence['CU']} (n={sample_n['CU']} real POs, last 12mo preferred)",
    }
    summary["data_source"] = "live_erp_purchase_order_history"
    summary["generated_at_utc"] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    with open(SUMMARY_PATH, 'w') as f:
        json.dump(summary, f, indent=2)
    print(f"\nWrote aggregate-only update to {SUMMARY_PATH}")


if __name__ == '__main__':
    main()
