#!/usr/bin/env python3
"""
BOM Processing + Budget Planning Engine.

For a chosen scope (one budget, one project, or every open budget) this engine
reads the ERP's BOM/budget lines and produces, per line and per project:

  Material intelligence  category bucket, base material, benchmark, lead time,
                         criticality, risk % (data/planning_params.json).
  Inventory planning     required vs on-hand vs already-allocated stock ->
                         net purchase quantity. Stock is allocated to budgets
                         in delivery-date order so two projects never count
                         the same kilogram twice.
  Price anchors          last PO price, average PO price (12 mo), ERP default
                         price, budget system_rate/vendor_rate, and a live
                         benchmark landed-cost estimate (costing.py profiles
                         driven by today's NALCO/LME/CRGO prices).
  Budget bands           expected rate (anchor hierarchy: fresh PO price >
                         benchmark landed cost > any PO price > default price
                         > system rate), min rate (cheapest credible anchor),
                         max rate (expected x (1 + category risk %)).

Outputs
  data/bom_analysis.json   full per-line detail — PRIVATE (git-ignored;
                           leaves the runner only as a private artifact).
  stdout                   aggregate counts and totals only (public log safe).

Usage
  python3 scripts/bom_engine.py                       # all budgets
  python3 scripts/bom_engine.py --project-id 42       # one project
  python3 scripts/bom_engine.py --budget-number B-123 # one budget
  python3 scripts/bom_engine.py --source boms         # engineering BOMs

Max-budget-limit mode (company BOM PDF as input):
  python3 scripts/bom_pdf_import.py bom.pdf                       # -> bom.json
  python3 scripts/bom_engine.py --bom-json bom.json --units 25 \
          [--delivery-date 2026-09-15] [--offline]
  --units multiplies every per-unit BOM quantity (project quantity).
  --offline skips the ERP entirely: only live-benchmark landed costs price the
  metal lines (Cu/Al/CRGO) — a fast market-based floor when creds are absent.
  The recommended MAX PURCHASE LIMIT for the project is the max-budget figure.
"""
import argparse
import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402
import costing  # noqa: E402
import price_anchor  # noqa: E402

CALIB = price_anchor.load_calibration()

OUT = os.path.join(erp.ROOT, "data", "bom_analysis.json")


def benchmark_rate(category, name, summary, cost_cfg):
    """Live landed-cost estimate per kg for benchmark-covered categories."""
    if category in ("Copper", "Aluminium"):
        fin = costing.finished_for(category, name, summary, cost_cfg)
        if fin:
            return fin["total_ex_gst"], f"costing profile '{fin['profile']}'"
    if category == "CRGO":
        crgo = (summary.get("crgo_steel") or {}).get("price_per_kg")
        if crgo:
            return float(crgo), "CRGO indicative benchmark (no conversion adder)"
    return None, None


def price_anchors(iid, item, category, po_history, summary, cost_cfg, params, line):
    today = datetime.now(timezone.utc).date()
    recent_cut = (today - timedelta(days=params["recent_po_days"])).isoformat()
    year_cut = (today - timedelta(days=params["po_history_days"])).isoformat()

    pos = sorted(po_history.get(iid, []), key=lambda p: p["date"])
    last_po = pos[-1] if pos else None
    yr = [p["price"] for p in pos if p["date"] >= year_cut]
    fresh = [p["price"] for p in pos if p["date"] >= recent_cut]
    ew = ew_note = None
    if fresh:
        ew, ew_note = price_anchor.anchor(
            pos, (today + timedelta(days=1)).isoformat(), params, CALIB,
            (item.get("code") or "")[:2])
    avg_po = statistics.median(yr) if yr else (statistics.median([p["price"] for p in pos]) if pos else None)
    bench, bench_src = benchmark_rate(category, item["name"], summary, cost_cfg)

    anchors = {
        "last_po_price": last_po["price"] if last_po else None,
        "last_po_date": last_po["date"] if last_po else None,
        "avg_po_price_12mo": round(avg_po, 2) if avg_po else None,
        "default_price": item["default_price"],
        "system_rate": line.get("system_rate"),
        "vendor_rate": line.get("vendor_rate"),
        "benchmark_landed_cost": round(bench, 2) if bench else None,
        "benchmark_source": bench_src,
    }

    # Expected-rate hierarchy: a fresh real purchase beats a model estimate;
    # a live benchmark estimate beats stale ERP master data.
    # EXCEPTION: a PO is only trusted while it still represents today's
    # market. If the live benchmark has drifted more than
    # po_drift_threshold_pct away from the recent-PO average (metal rally or
    # crash since that purchase), suppliers will quote today's metal — so the
    # benchmark becomes the expected rate and the basis records why.
    drift_pct = params.get("po_drift_threshold_pct", 5.0)
    po_drift_note = None
    if fresh and bench:
        po_avg = ew or statistics.median(fresh)
        drift = (bench - po_avg) / po_avg * 100
        if abs(drift) > drift_pct:
            fresh = None  # PO stale — fall through to the benchmark branch
            po_drift_note = (f"recent PO avg Rs {po_avg:,.2f} set aside — "
                             f"market moved {drift:+.1f}% since that purchase")
            anchors["po_drift_note"] = po_drift_note
    if fresh:
        expected = ew or statistics.median(fresh)
        basis = ew_note or "recent PO average"
    elif bench:
        expected, basis = bench, ("live benchmark landed cost"
                                  if not po_drift_note
                                  else f"live benchmark landed cost ({po_drift_note})")
    elif avg_po:
        expected, basis = avg_po, "PO history average"
    elif item["default_price"]:
        expected, basis = item["default_price"], "ERP default price"
    elif line.get("system_rate"):
        expected, basis = line["system_rate"], "budget system rate"
    else:
        expected, basis = None, "no anchor available"

    candidates = [v for v in (anchors["last_po_price"], anchors["avg_po_price_12mo"],
                              anchors["default_price"], anchors["benchmark_landed_cost"])
                  if v and v > 0]
    min_rate = min(candidates) if candidates else expected
    return anchors, expected, basis, min_rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["budgets", "boms"], default="budgets",
                    help="budgets = bom_budget_items with rates (default); boms = engineering BOMs")
    ap.add_argument("--project-id", type=int)
    ap.add_argument("--budget-number")
    ap.add_argument("--bom-json", help="parsed company BOM PDF (bom_pdf_import.py output) — max-budget-limit mode")
    ap.add_argument("--units", type=float, default=1.0, help="project quantity: multiplies per-unit BOM qtys")
    ap.add_argument("--delivery-date", help="YYYY-MM-DD, used for lead-time planning downstream")
    ap.add_argument("--offline", action="store_true",
                    help="no ERP calls: benchmark-only pricing (requires --bom-json)")
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    if args.offline and not args.bom_json:
        ap.error("--offline requires --bom-json")

    params = erp.load_params()
    summary = erp.load_summary()
    cost_cfg, _ = costing.load_cfg_summary()
    intel = erp.MaterialIntel(params)

    if args.offline:
        items, stock_pool, po_history = {}, {}, {}
        print("OFFLINE mode: no ERP calls — benchmark-only pricing.")
    else:
        session = erp.make_session()
        print("Fetching item master...")
        items = erp.fetch_items(session)
        print(f"  {len(items)} items.")
        print("Fetching inventory...")
        stock_pool = erp.fetch_stock(session)
        print(f"  stock records for {len(stock_pool)} items.")
        print("Fetching purchase-order history (24 mo)...")
        po_history = erp.fetch_po_history(session)
        print(f"  PO price history for {len(po_history)} items.")

    n_unmatched = 0
    if args.bom_json:
        # ---- max-budget-limit mode: company BOM PDF (parsed) as the source ----
        with open(args.bom_json) as f:
            bom = json.load(f)
        by_code = {it["code"]: it for it in items.values() if it.get("code")}
        lines = []
        for l in bom["lines"]:
            match = by_code.get(l["item_code"])
            if match is None:
                n_unmatched += 1
                iid = f"pdf:{l['item_code']}"
                items[iid] = {"id": iid, "code": l["item_code"], "name": l["description"],
                              "uom": l["uom"], "default_price": None,
                              "category_code": None, "material_type": "Raw Material",
                              "product_service": "product"}
            else:
                iid = match["id"]
            lines.append({"item_id": iid,
                          "quantity": l["qty_per_unit"] * args.units,
                          "system_rate": None, "vendor_rate": None})
        docs = [{"budget_number": bom.get("bom_number"),
                 "status": "max-budget-calc",
                 "project_code": bom.get("project_code"),
                 "delivery_date": args.delivery_date,
                 "max_purchase_limit_amount": None,
                 "lines": lines}]
        key = "budget_number"
        print(f"BOM {bom.get('bom_number')}: {len(lines)} lines x {args.units:g} units "
              f"({len(lines) - n_unmatched} matched to ERP item master, {n_unmatched} unmatched).")
    elif args.source == "budgets":
        docs = erp.fetch_budgets(session, project_id=args.project_id)
        if args.budget_number:
            docs = [d for d in docs if d.get("budget_number") == args.budget_number]
        key = "budget_number"
    else:
        docs = erp.fetch_boms(session, project_id=args.project_id)
        key = "bom_number"
    docs = [d for d in docs if d.get("lines")]
    if not args.bom_json:
        print(f"{len(docs)} {args.source} with lines in scope.")

    # allocate stock earliest-delivery-first so it is never double counted
    docs.sort(key=lambda d: (erp.parse_date(d.get("delivery_date")) or datetime.max.date()))
    remaining = dict(stock_pool)

    analysed, n_lines, n_short = [], 0, 0
    grand = defaultdict(float)
    cat_totals = defaultdict(lambda: defaultdict(float))

    for doc in docs:
        lines_out = []
        doc_tot = defaultdict(float)
        for line in doc["lines"]:
            iid = line["item_id"]
            item = items.get(iid)
            if not item or line["quantity"] <= 0:
                continue
            n_lines += 1
            mi = intel.enrich(item)
            cat = mi["category"]

            design_qty = line["quantity"]
            # Wastage allowance: purchases must cover cutting/scrap loss, not
            # just the net design quantity (e.g. CRGO lamination offcuts).
            required = design_qty * (1 + (mi.get("wastage_pct") or 0) / 100.0)
            available = max(0.0, remaining.get(iid, 0.0))
            allocated = min(required, available)
            remaining[iid] = available - allocated
            net_buy = required - allocated
            if net_buy > 0:
                n_short += 1

            anchors, expected, basis, min_rate = price_anchors(
                iid, item, cat, po_history, summary, cost_cfg, params, line)
            risk = mi["risk_pct"]
            max_rate = expected * (1 + risk / 100.0) if expected else None

            exp_cost = (expected or 0) * net_buy
            min_cost = (min_rate or 0) * net_buy
            max_cost = (max_rate or 0) * net_buy
            full_material_cost = (expected or 0) * required
            full_min = (min_rate or 0) * required
            full_max = (max_rate or 0) * required

            doc_tot["expected"] += exp_cost
            doc_tot["min"] += min_cost
            doc_tot["max"] += max_cost
            doc_tot["material_cost"] += full_material_cost
            doc_tot["full_min"] += full_min
            doc_tot["full_max"] += full_max
            grand["expected"] += exp_cost
            grand["min"] += min_cost
            grand["max"] += max_cost
            grand["full_expected"] += full_material_cost
            grand["full_min"] += full_min
            grand["full_max"] += full_max
            cat_totals[cat]["required_qty"] += required
            cat_totals[cat]["net_buy_qty"] += net_buy
            cat_totals[cat]["expected_cost"] += exp_cost
            cat_totals[cat]["full_material_cost"] += full_material_cost

            lines_out.append({
                "item_id": iid, "item_code": item["code"], "item_name": item["name"],
                "uom": item["uom"], **mi,
                "design_qty": round(design_qty, 2),
                "required_qty": round(required, 2),
                "stock_allocated": round(allocated, 2),
                "net_buy_qty": round(net_buy, 2),
                "anchors": anchors,
                "expected_rate": round(expected, 2) if expected else None,
                "expected_rate_basis": basis,
                "min_rate": round(min_rate, 2) if min_rate else None,
                "max_rate": round(max_rate, 2) if max_rate else None,
                "expected_cost": round(exp_cost, 0),
                "min_cost": round(min_cost, 0),
                "max_cost": round(max_cost, 0),
                "full_material_cost": round(full_material_cost, 0),
            })

        variance_vs_limit = None
        if doc.get("max_purchase_limit_amount"):
            variance_vs_limit = round(doc_tot["expected"] - doc["max_purchase_limit_amount"], 0)
        analysed.append({
            key: doc.get(key),
            "project_code": doc.get("project_code"),
            "delivery_date": doc.get("delivery_date"),
            "status": doc.get("status"),
            "max_purchase_limit_amount": doc.get("max_purchase_limit_amount"),
            "budget_min": round(doc_tot["min"], 0),
            "budget_expected": round(doc_tot["expected"], 0),
            "budget_max": round(doc_tot["max"], 0),
            "full_material_cost": round(doc_tot["material_cost"], 0),
            "full_budget_min": round(doc_tot["full_min"], 0),
            "full_budget_max": round(doc_tot["full_max"], 0),
            "expected_vs_limit": variance_vs_limit,
            "lines": lines_out,
        })

    out = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "bom-pdf" if args.bom_json else args.source,
        "units": args.units if args.bom_json else None,
        "unmatched_lines": n_unmatched if args.bom_json else None,
        "live_prices": {k: summary[k].get("price_per_kg") for k in
                        ("aluminium", "copper", "crgo_steel") if k in summary},
        "category_summary": {c: {k: round(v, 1) for k, v in t.items()}
                             for c, t in sorted(cat_totals.items())},
        "grand_total": {k: round(v, 0) for k, v in grand.items()},
        "documents": analysed,
    }
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)

    # ---- aggregate-only public log ----
    print(f"\nAnalysed {len(analysed)} {args.source}, {n_lines} material lines "
          f"({n_short} lines need purchasing after stock netting).")
    for c, t in sorted(cat_totals.items()):
        print(f"  {c:<14} required {t['required_qty']:>12,.0f}  net-to-buy {t['net_buy_qty']:>12,.0f}")
    print(f"Portfolio budget band: min Rs {grand['min']:,.0f} | expected Rs {grand['expected']:,.0f} "
          f"| max Rs {grand['max']:,.0f}")
    if args.bom_json:
        print(f"\nFull material budget ({args.units:g} unit(s)): "
              f"min Rs {grand['full_min']:,.0f} | expected Rs {grand['full_expected']:,.0f} "
              f"| max Rs {grand['full_max']:,.0f}")
        print(f"Already covered by stock: Rs {grand['full_expected'] - grand['expected']:,.0f} "
              f"| still to purchase: Rs {grand['expected']:,.0f}")
        print(f"RECOMMENDED MAX PURCHASE LIMIT for {docs[0].get('project_code') or docs[0].get(key)}: "
              f"Rs {grand['full_max']:,.0f}")
        if n_unmatched:
            print(f"NOTE: {n_unmatched} line(s) had no ERP item-master match — priced from "
                  f"benchmark/none; review them in the output JSON before fixing the limit.")
        if args.offline:
            print("NOTE: offline run — only benchmark-covered categories (Cu/Al/CRGO) are priced; "
                  "treat the figure as the metal-cost floor, not the full budget.")
    print(f"Wrote {args.out} (PRIVATE — git-ignored, upload as private artifact only)")


if __name__ == "__main__":
    main()
