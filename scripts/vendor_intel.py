#!/usr/bin/env python3
"""
Vendor Intelligence Engine — vendor scoring + quotation analysis.

MODE 1 (default): score every vendor per material category from real PO history.

  Score (0-100) = weighted blend from data/planning_params.json:
    price_competitiveness (40)  vendor's average price vs the category median
                                across all vendors (cheaper -> higher score)
    recency (20)                days since the vendor's last PO in the category
    experience (20)             number of PO lines (log-scaled)
    consistency (20)            price volatility across the vendor's own POs
                                (lower stddev -> higher score)

  Delivery performance, rejection rate and payment terms are NOT exposed by the
  current external API — the design doc lists them as inputs to add when the
  ERP exposes GRN/quality endpoints. The score is therefore explicitly labelled
  "price-history based".

MODE 2 (--quotes quotes.csv): compare incoming vendor quotations against the
  expected cost (bom_analysis.json anchors), last PO price, and live benchmark.
  CSV columns: item_code, vendor_name, rate [, lead_time_days, payment_terms]
  Output: per item — best price, best lead time, best overall + recommendation.

Outputs
  data/vendor_scores.json / data/quote_analysis.json — PRIVATE (git-ignored).
  stdout — aggregate counts only (public log safe).
"""
import argparse
import csv
import json
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402

LEAD_PENALTY_PCT_PER_DAY = 0.2  # 0.2%/day delay cost in the quote tie-break

SCORES_OUT = os.path.join(erp.ROOT, "data", "vendor_scores.json")
QUOTES_OUT = os.path.join(erp.ROOT, "data", "quote_analysis.json")
ANALYSIS = os.path.join(erp.ROOT, "data", "bom_analysis.json")


def scale(value, worst, best):
    """Linear 0..1 with clamping; handles inverted ranges."""
    if value is None:
        return 0.5
    if worst == best:
        return 1.0
    t = (value - worst) / (best - worst)
    return max(0.0, min(1.0, t))


def score_vendors(items, po_history, intel, params, today):
    w = params["vendor_score_weights"]
    # gather (category, vendor) -> price points
    cat_vendor = defaultdict(lambda: defaultdict(list))
    vendor_names = {}
    for iid, lines in po_history.items():
        item = items.get(iid)
        if not item:
            continue
        cat = intel.categorize(item)
        for l in lines:
            vid = l.get("vendor_id") or l.get("vendor_name")
            if vid is None:
                continue
            vendor_names[vid] = l.get("vendor_name") or str(vid)
            cat_vendor[cat][vid].append(l)

    out = {}
    for cat, vendors in cat_vendor.items():
        all_prices = [l["price"] for vs in vendors.values() for l in vs]
        med = statistics.median(all_prices) if all_prices else None
        max_n = max(len(vs) for vs in vendors.values())
        rows = []
        for vid, vs in vendors.items():
            prices = [l["price"] for l in vs]
            avg = statistics.mean(prices)
            last_date = max((l["date"] for l in vs if l.get("date")), default=None)
            days_since = (today - erp.parse_date(last_date)).days if last_date else None
            vol = (statistics.pstdev(prices) / avg) if (len(prices) > 1 and avg) else 0.0

            s_price = scale(med / avg if (med and avg) else None, 0.7, 1.15)   # avg 15% under median -> full marks
            s_recency = scale(-(days_since or 730), -730, -30)                  # PO within 30d -> full marks
            s_exp = scale(math.log1p(len(vs)), 0, math.log1p(max_n))
            s_cons = scale(-vol, -0.35, -0.02)                                  # <2% own volatility -> full marks

            score = (w["price_competitiveness"] * s_price + w["recency"] * s_recency +
                     w["experience"] * s_exp + w["consistency"] * s_cons) / \
                    (w["price_competitiveness"] + w["recency"] + w["experience"] + w["consistency"]) * 100
            rows.append({
                "vendor_id": vid if isinstance(vid, int) else None,
                "vendor_name": vendor_names.get(vid),
                "po_lines": len(vs),
                "avg_price": round(avg, 2),
                "vs_category_median_pct": round((avg / med - 1) * 100, 1) if med else None,
                "last_po_date": last_date,
                "days_since_last_po": days_since,
                "own_price_volatility_pct": round(vol * 100, 1),
                "score": round(score, 1),
            })
        rows.sort(key=lambda r: -r["score"])
        out[cat] = {"category_median_price": round(med, 2) if med else None,
                    "vendors": rows}
    return out


def analyse_quotes(quotes_path, analysis, live_prices):
    """Compare vendor quotes per item vs expected cost / last PO / benchmark."""
    expected = {}
    for doc in analysis.get("documents", []):
        for l in doc.get("lines", []):
            expected.setdefault(l["item_code"], {
                "expected_rate": l.get("expected_rate"),
                "last_po_price": (l.get("anchors") or {}).get("last_po_price"),
                "benchmark": (l.get("anchors") or {}).get("benchmark_landed_cost"),
                "category": l.get("category"),
            })

    by_item = defaultdict(list)
    with open(quotes_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            try:
                rate = float(row["rate"])
            except (KeyError, ValueError):
                continue
            by_item[row["item_code"].strip()].append({
                "vendor_name": (row.get("vendor_name") or "").strip(),
                "rate": rate,
                "lead_time_days": int(row["lead_time_days"]) if (row.get("lead_time_days") or "").strip().isdigit() else None,
                "payment_terms": (row.get("payment_terms") or "").strip() or None,
            })

    results = []
    for code, quotes in by_item.items():
        ref = expected.get(code, {})
        exp = ref.get("expected_rate")
        for q in quotes:
            q["vs_expected_pct"] = round((q["rate"] / exp - 1) * 100, 1) if exp else None
            q["vs_last_po_pct"] = (round((q["rate"] / ref["last_po_price"] - 1) * 100, 1)
                                   if ref.get("last_po_price") else None)
            q["vs_benchmark_pct"] = (round((q["rate"] / ref["benchmark"] - 1) * 100, 1)
                                     if ref.get("benchmark") else None)
        best_price = min(quotes, key=lambda q: q["rate"])
        with_lead = [q for q in quotes if q["lead_time_days"] is not None]
        best_lead = min(with_lead, key=lambda q: q["lead_time_days"]) if with_lead else None

        # Economic tie-break: each day of extra lead time (vs the fastest
        # quote) costs `lead_penalty_pct_per_day` % of the rate — so a small
        # price premium can be worth faster delivery, but a large one never is.
        penalty = LEAD_PENALTY_PCT_PER_DAY / 100.0

        def effective_cost(q):
            extra_days = ((q["lead_time_days"] - best_lead["lead_time_days"])
                          if (best_lead and q["lead_time_days"] is not None) else 10)
            return q["rate"] * (1 + penalty * max(0, extra_days))
        best_overall = min(quotes, key=effective_cost)
        for q in quotes:
            q["effective_cost"] = round(effective_cost(q), 2)

        flags = []
        if exp and best_price["rate"] > exp * 1.05:
            flags.append(f"best quote is {((best_price['rate']/exp)-1)*100:.1f}% above expected cost — negotiate or re-tender")
        if exp and best_price["rate"] < exp * 0.85:
            flags.append("best quote is >15% below expected cost — verify spec/quality before awarding")
        results.append({
            "item_code": code, "category": ref.get("category"),
            "expected_rate": exp,
            "quotes": sorted(quotes, key=lambda q: q["rate"]),
            "best_price_vendor": best_price["vendor_name"],
            "best_lead_vendor": best_lead["vendor_name"] if best_lead else None,
            "recommended_vendor": best_overall["vendor_name"],
            "recommendation_basis": f"lowest effective cost = rate x (1 + {LEAD_PENALTY_PCT_PER_DAY}%/day of extra lead time vs fastest quote)",
            "flags": flags,
        })
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quotes", help="CSV of incoming quotations to analyse (mode 2)")
    args = ap.parse_args()

    params = erp.load_params()
    today = datetime.now(timezone.utc).date()

    if args.quotes:
        with open(ANALYSIS) as f:
            analysis = json.load(f)
        results = analyse_quotes(args.quotes, analysis, analysis.get("live_prices", {}))
        out = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "items": results}
        with open(QUOTES_OUT, "w") as f:
            json.dump(out, f, indent=2)
        flagged = sum(1 for r in results if r["flags"])
        print(f"Quote analysis: {len(results)} items, {flagged} flagged. "
              f"Wrote {QUOTES_OUT} (PRIVATE)")
        return

    intel = erp.MaterialIntel(params)
    session = erp.make_session()
    print("Fetching item master...")
    items = erp.fetch_items(session)
    print("Fetching purchase-order history (24 mo)...")
    po_history = erp.fetch_po_history(session)

    scores = score_vendors(items, po_history, intel, params, today)
    out = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "method": "Price-history-based score. Delivery/quality/payment-term factors pending ERP endpoints.",
        "weights": params["vendor_score_weights"],
        "by_category": scores,
    }
    with open(SCORES_OUT, "w") as f:
        json.dump(out, f, indent=2)

    for cat, block in sorted(scores.items()):
        print(f"  {cat:<14} {len(block['vendors'])} vendors scored")
    print(f"Wrote {SCORES_OUT} (PRIVATE — git-ignored, private artifact only)")


if __name__ == "__main__":
    main()
