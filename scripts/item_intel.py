#!/usr/bin/env python3
"""
Per-item purchase intelligence for the public items page.

Pulls purchase-order history (24 mo) + the item master from the ERP and
writes data/item_intel.json keyed by item code:

  { "CODE": { "lpo": last PO price, "lpod": its date,
              "avg180": mean price of POs in the last 180 days,
              "avg12":  mean price of POs in the last 365 days,
              "npo": PO-line count,
              "vend": [ {"n": vendor, "p": last price, "d": date}, ... top 3 ] } }

The items page merges this in as "Our rate" (recent real buying price, falling
back to the costing-formula estimate) and a per-item vendor list with each
vendor's most recent price.

NOTE: the repo owner has accepted that vendor names and purchase rates are
publicly visible in this repository.
"""
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402
import price_anchor  # noqa: E402

OUT = os.path.join(erp.ROOT, "data", "item_intel.json")


def main():
    session = erp.make_session()
    print("Fetching item master...")
    items = erp.fetch_items(session)
    print(f"  {len(items)} items.")
    print("Fetching purchase-order history (24 mo)...")
    po = erp.fetch_po_history(session)
    print(f"  history for {len(po)} items.")

    params = erp.load_params()
    calib = price_anchor.load_calibration()
    asof = (datetime.now(timezone.utc).date() + timedelta(days=1)).isoformat()
    today = datetime.now(timezone.utc).date()
    cut180 = (today - timedelta(days=180)).isoformat()
    cut365 = (today - timedelta(days=365)).isoformat()

    out = {}
    for iid, lines in po.items():
        item = items.get(iid)
        if not item or not item.get("code"):
            continue
        lines = sorted(lines, key=lambda l: l["date"])
        last = lines[-1]
        p180 = [l["price"] for l in lines if l["date"] >= cut180]
        p365 = [l["price"] for l in lines if l["date"] >= cut365]
        by_vendor = {}
        for l in lines:  # ascending: keeps each vendor's LATEST line
            name = l.get("vendor_name")
            if name:
                by_vendor[name] = l
        vend = sorted(by_vendor.values(), key=lambda l: l["date"], reverse=True)[:3]
        ew, ewb = price_anchor.anchor(lines, asof, params, calib, (item["code"] or "")[:2])
        out[item["code"]] = {
            "ew": ew, "ewb": ewb,
            "lpo": round(last["price"], 2), "lpod": last["date"],
            # medians (field names kept for compatibility): a single PO with a
            # unit/pack-size entry error can no longer drag the rate
            "avg180": round(statistics.median(p180), 2) if p180 else None,
            "avg12": round(statistics.median(p365), 2) if p365 else None,
            "npo": len(lines),
            "vend": [{"n": l.get("vendor_name"), "p": round(l["price"], 2), "d": l["date"]}
                     for l in vend],
        }

    doc = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "items": out}
    with open(OUT, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"Wrote {OUT}: purchase intel for {len(out)} items.")


if __name__ == "__main__":
    main()
