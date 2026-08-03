#!/usr/bin/env python3
"""
Vendor quote store with volume-tier resolution (quotes/quotes.csv).

The team pastes real vendor quotations into quotes/quotes.csv (editable
directly on GitHub — see quotes/README.md). A VALID quote is the freshest
market evidence we can have — newer than any PO — so the pricing ladder
puts it first:

    valid vendor quote (qty tier) > PO anchor > live costing estimate > ...

Schema (one row per quantity tier):
    item_code, vendor_name, min_qty, unit_price_ex_gst, valid_until, quote_ref

Resolution for a needed quantity Q:
    keep rows for the item that are still valid (valid_until >= today,
    or blank = no expiry) with min_qty <= Q; within each vendor take the
    highest applicable tier (the deepest volume break earned); across
    vendors take the CHEAPEST offer. Expired rows are ignored entirely.

Bad rows (non-numeric price/qty) are skipped with a warning, never fatal —
this file is hand-edited by non-programmers.
"""
import csv
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUOTES_PATH = os.path.join(ROOT, "quotes", "quotes.csv")


def load_quotes(path=QUOTES_PATH):
    """item_code -> list of {vendor, min_qty, price, valid_until, ref}."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8-sig") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):
            try:
                code = (row.get("item_code") or "").strip()
                if not code:
                    continue
                rec = {
                    "vendor": (row.get("vendor_name") or "").strip(),
                    "min_qty": float(row.get("min_qty") or 1),
                    "price": float(row["unit_price_ex_gst"]),
                    "valid_until": (row.get("valid_until") or "").strip(),
                    "ref": (row.get("quote_ref") or "").strip(),
                }
                if rec["price"] <= 0:
                    raise ValueError("price <= 0")
                out.setdefault(code, []).append(rec)
            except (KeyError, ValueError, TypeError) as e:
                print(f"quotes.csv line {i}: skipped ({e})", file=sys.stderr)
    return out


def resolve(quotes, item_code, qty=1.0, today=None):
    """Best applicable quote for `qty` units, or None.
    Returns {price, vendor, ref, min_qty, valid_until, note}."""
    rows = quotes.get(item_code)
    if not rows:
        return None
    today = today or datetime.now(timezone.utc).date().isoformat()
    live = [r for r in rows
            if (not r["valid_until"] or r["valid_until"] >= today) and r["min_qty"] <= qty]
    if not live:
        return None
    # deepest earned tier per vendor, then cheapest vendor wins
    per_vendor = {}
    for r in live:
        cur = per_vendor.get(r["vendor"])
        if cur is None or r["min_qty"] > cur["min_qty"]:
            per_vendor[r["vendor"]] = r
    best = min(per_vendor.values(), key=lambda r: r["price"])
    tier = f"≥{best['min_qty']:g} qty tier" if best["min_qty"] > 1 else "unit tier"
    validity = f", valid till {best['valid_until']}" if best["valid_until"] else ""
    note = (f"vendor quote {best['ref'] or best['vendor']}: ₹{best['price']:,.2f} "
            f"({tier}{validity})")
    return {**best, "note": note}
