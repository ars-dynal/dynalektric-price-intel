#!/usr/bin/env python3
"""
Offline smoke test — runs all three engines against synthetic ERP fixtures
(no network, no credentials). Verifies: categorization, stock allocation
across two budgets, budget bands, planner actions, vendor scoring, and the
quote-analysis mode. Run:  python3 tests/smoke_test.py
"""
import json
import os
import sys
from datetime import date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))

import erp_common as erp  # noqa: E402

TODAY = date.today()

ITEMS = {
    1: {"id": 1, "code": "CU-111-00001", "name": "Copper Foil, Thickness 0.1mm", "uom": "KGS",
        "default_price": 900.0, "category_code": "CU-222-", "material_type": "Raw Material", "product_service": "product"},
    2: {"id": 2, "code": "AL-111-00002", "name": "Aluminium Foil, Electrical Grade 1050", "uom": "KGS",
        "default_price": 400.0, "category_code": "AL-222-", "material_type": "Raw Material", "product_service": "product"},
    3: {"id": 3, "code": "CC-555-00471", "name": "CRGO Lamination M4 0.27mm", "uom": "KGS",
        "default_price": 210.0, "category_code": "CC-555-", "material_type": "Raw Material", "product_service": "product"},
    4: {"id": 4, "code": "CH-303-00048", "name": "Hex Bolt M12x50 SS", "uom": "NOS",
        "default_price": 12.0, "category_code": "CH-303-", "material_type": "Raw Material", "product_service": "product"},
    5: {"id": 5, "code": "OL-101-00001", "name": "Transformer Oil IEC 60296", "uom": "LTR",
        "default_price": 95.0, "category_code": "OL-101-", "material_type": "Raw Material", "product_service": "product"},
}
STOCK = {1: 650.0, 2: 0.0, 3: 500.0, 4: 10000.0, 5: 0.0}
PO_HISTORY = {
    1: [{"date": (TODAY - timedelta(days=40)).isoformat(), "price": 1310.0, "qty": 500, "vendor_id": 11, "vendor_name": "MEHTA METALS", "po_number": "PO-1", "status": 1},
        {"date": (TODAY - timedelta(days=200)).isoformat(), "price": 1150.0, "qty": 800, "vendor_id": 12, "vendor_name": "SHREYAS ENT", "po_number": "PO-2", "status": 1}],
    2: [{"date": (TODAY - timedelta(days=90)).isoformat(), "price": 395.0, "qty": 1000, "vendor_id": 13, "vendor_name": "JINDAL ALUMINIUM", "po_number": "PO-3", "status": 1},
        {"date": (TODAY - timedelta(days=60)).isoformat(), "price": 405.0, "qty": 700, "vendor_id": 13, "vendor_name": "JINDAL ALUMINIUM", "po_number": "PO-4", "status": 1},
        {"date": (TODAY - timedelta(days=30)).isoformat(), "price": 452.0, "qty": 300, "vendor_id": 12, "vendor_name": "SHREYAS ENT", "po_number": "PO-5", "status": 1}],
    3: [{"date": (TODAY - timedelta(days=400)).isoformat(), "price": 198.0, "qty": 2000, "vendor_id": 14, "vendor_name": "SM STEELS", "po_number": "PO-6", "status": 1}],
}
BUDGETS = [
    {"budget_number": "BUD-001", "status": "pending", "project_code": "PRJ-A",
     "project_name": "5 MVA Transformer", "delivery_date": (TODAY + timedelta(days=20)).isoformat(),
     "max_purchase_limit_amount": 1_500_000.0,
     "lines": [{"item_id": 1, "quantity": 1000.0, "system_rate": 1100.0, "vendor_rate": None},
               {"item_id": 3, "quantity": 800.0, "system_rate": 0.0, "vendor_rate": None},
               {"item_id": 5, "quantity": 300.0, "system_rate": 90.0, "vendor_rate": None}]},
    {"budget_number": "BUD-002", "status": "pending", "project_code": "PRJ-B",
     "project_name": "10 MVA Transformer", "delivery_date": (TODAY + timedelta(days=120)).isoformat(),
     "max_purchase_limit_amount": 900_000.0,
     "lines": [{"item_id": 1, "quantity": 500.0, "system_rate": 1200.0, "vendor_rate": None},
               {"item_id": 2, "quantity": 1200.0, "system_rate": 380.0, "vendor_rate": None},
               {"item_id": 4, "quantity": 400.0, "system_rate": 12.0, "vendor_rate": None}]},
]


def patch(monkey=erp):
    monkey.make_session = lambda: None
    monkey.fetch_items = lambda s: ITEMS
    monkey.fetch_stock = lambda s: dict(STOCK)
    monkey.fetch_po_history = lambda s, since_days=730: {k: list(v) for k, v in PO_HISTORY.items()}
    monkey.fetch_budgets = lambda s, project_id=None: [json.loads(json.dumps(b)) for b in BUDGETS]


def check(cond, msg):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {msg}")
    if not cond:
        sys.exit(1)


def main():
    patch()

    # ---- material intelligence ----
    intel = erp.MaterialIntel()
    print("MaterialIntel:")
    check(intel.categorize(ITEMS[1]) == "Copper", "copper foil -> Copper")
    check(intel.categorize(ITEMS[2]) == "Aluminium", "aluminium foil -> Aluminium")
    check(intel.categorize(ITEMS[3]) == "CRGO", "CC lamination -> CRGO")
    check(intel.categorize(ITEMS[4]) == "Hardware", "hex bolt -> Hardware")
    check(intel.categorize(ITEMS[5]) == "Oil", "transformer oil -> Oil")
    e = intel.enrich(ITEMS[1])
    check(e["lead_time_days"] == 30 and e["criticality"] == "High", "copper lead time 30d / High")

    # ---- BOM engine ----
    import bom_engine
    sys.argv = ["bom_engine.py"]
    print("bom_engine:")
    bom_engine.main()
    with open(bom_engine.OUT) as f:
        analysis = json.load(f)
    b1 = next(d for d in analysis["documents"] if d["budget_number"] == "BUD-001")
    b2 = next(d for d in analysis["documents"] if d["budget_number"] == "BUD-002")
    l_cu1 = next(l for l in b1["lines"] if l["item_id"] == 1)
    l_cu2 = next(l for l in b2["lines"] if l["item_id"] == 1)
    check(l_cu1["stock_allocated"] == 650.0 and l_cu1["net_buy_qty"] == 375.0,
          "copper stock 650 allocated to earliest budget; net buy 375 (incl. 2.5% wastage on 1000)")
    check(l_cu2["stock_allocated"] == 0.0 and l_cu2["net_buy_qty"] == 512.5,
          "no double-counting: second budget gets no copper stock (500 x 1.025)")
    # Stale-PO guard: a fresh PO outranks the benchmark ONLY while the live
    # benchmark stays within po_drift_threshold_pct of it. The fixture PO is
    # Rs 1310 while the benchmark tracks the real live copper price, so which
    # branch fires depends on today's market — assert the RULE, not one side.
    bench_cu = l_cu1["anchors"]["benchmark_landed_cost"]
    drift_cu = abs(bench_cu - 1310.0) / 1310.0 * 100
    if drift_cu > 5.0:
        check(l_cu1["expected_rate_basis"].startswith("live benchmark landed cost (recent PO avg"),
              f"market drifted {drift_cu:.1f}%% from fixture PO -> PO set aside for live benchmark".replace('%%','%'))
        check(l_cu1["expected_rate"] == bench_cu, "drifted line priced at the live benchmark")
    else:
        check(l_cu1["expected_rate_basis"] == "recent PO average",
              "fresh PO price outranks benchmark (market within drift threshold)")
    check(l_cu1["max_rate"] > l_cu1["expected_rate"] > 0, "max band above expected")
    l_crgo = next(l for l in b1["lines"] if l["item_id"] == 3)
    check(l_crgo["net_buy_qty"] == 340.0, "CRGO 800x1.05 wastage - 500 stock = 340 net")
    check(l_crgo["expected_rate_basis"] == "live benchmark landed cost",
          "stale PO (400d) falls back to CRGO benchmark")
    l_oil = next(l for l in b1["lines"] if l["item_id"] == 5)
    check(l_oil["expected_rate"] == 95.0 and l_oil["expected_rate_basis"] == "ERP default price",
          "no-PO, no-benchmark item uses default price")

    # ---- max-budget-limit mode (company BOM PDF -> parsed JSON) ----
    print("bom_engine --bom-json (max budget limit):")
    bom_fixture = {
        "bom_number": "DE/BOM/26-27/07-TEST", "project_code": "RC TEST",
        "fg_code": "PVT00000001", "line_count": 3,
        "lines": [
            {"serial": 1, "item_code": "CU-111-00001", "description": "Copper Foil, Thickness 0.1mm",
             "pdf_category": "CU2", "uom": "KGS", "qty_per_unit": 0.9},
            {"serial": 2, "item_code": "CC-999-99999", "description": "CRGO Lamination M4 (not in ERP)",
             "pdf_category": "CC", "uom": "KGS", "qty_per_unit": 3.2},
            {"serial": 3, "item_code": "FST-999-00504", "description": "MS HEX BOLT, SIZE: M5X60",
             "pdf_category": "FST", "uom": "NOS", "qty_per_unit": 4.0},
        ],
    }
    bom_path = os.path.join(HERE, "bom_fixture.json")
    with open(bom_path, "w") as f:
        json.dump(bom_fixture, f)
    patch()  # re-patch: bom_engine.main may have been imported already
    sys.argv = ["bom_engine.py", "--bom-json", bom_path, "--units", "10", "--delivery-date", "2027-01-15"]
    bom_engine.main()
    with open(bom_engine.OUT) as f:
        mb = json.load(f)
    check(mb["source"] == "bom-pdf" and mb["units"] == 10, "bom-pdf source + units recorded")
    doc = mb["documents"][0]
    l_cu = next(l for l in doc["lines"] if l["item_code"] == "CU-111-00001")
    check(l_cu["design_qty"] == 9.0 and l_cu["required_qty"] == 9.22,
          "per-unit qty x units (0.9 x 10) + 2.5% wastage")
    # Same stale-PO guard applies here: PO anchors are kept only while the
    # live benchmark is within the drift threshold of the fixture PO price.
    check(l_cu["category"] == "Copper" and
          (l_cu["expected_rate_basis"] == "recent PO average" or
           l_cu["expected_rate_basis"].startswith("live benchmark landed cost (recent PO avg")),
          "matched ERP item keeps PO anchors (or documents why the PO was set aside)")
    check(l_cu["stock_allocated"] > 0, "matched item nets against ERP stock")
    l_crgo = next(l for l in doc["lines"] if l["item_code"] == "CC-999-99999")
    check(l_crgo["category"] == "CRGO", "unmatched item still categorized from its code")
    check(l_crgo["expected_rate_basis"] == "live benchmark landed cost",
          "unmatched CRGO priced from live benchmark")
    check(mb["unmatched_lines"] == 2, "unmatched count reported")
    check(doc["budget_max"] > doc["budget_expected"] > 0, "max budget band above expected")

    # restore the full-portfolio analysis for the planner test below
    patch()
    sys.argv = ["bom_engine.py"]
    bom_engine.main()

    # ---- purchase planner ----
    import purchase_planner
    sys.argv = ["purchase_planner.py"]
    print("purchase_planner:")
    purchase_planner.main()
    with open(purchase_planner.OUT) as f:
        plan = json.load(f)
    by_key = {(p["budget_number"], p["item_id"]): p for p in plan["plan"]}
    p_cu1 = by_key[("BUD-001", 1)]
    check(p_cu1["action"] == "BUY_NOW" and p_cu1["overdue"],
          "copper for 20d-out delivery with 30d lead -> BUY_NOW (overdue)")
    check(p_cu1["priority"] == 1, "overdue critical line is priority 1")
    p_cu2 = by_key[("BUD-002", 1)]
    check(p_cu2["action"] in ("DELAY", "MONITOR", "BUY_SOON"),
          f"copper with 120d slack not BUY_NOW (got {p_cu2['action']}, signal {p_cu2['market_signal']})")
    types = {a["type"] for a in plan["alerts"]}
    check("DELIVERY_RISK" in types and "CRITICAL_SHORTAGE" in types, "delivery + shortage alerts raised")

    # ---- vendor intelligence ----
    import vendor_intel
    sys.argv = ["vendor_intel.py"]
    print("vendor_intel:")
    vendor_intel.main()
    with open(vendor_intel.SCORES_OUT) as f:
        scores = json.load(f)
    cu = scores["by_category"]["Copper"]
    check(len(cu["vendors"]) == 2, "two copper vendors scored")
    al = scores["by_category"]["Aluminium"]
    check(al["vendors"][0]["vendor_name"] == "JINDAL ALUMINIUM",
          "cheaper, more experienced aluminium vendor ranks first")

    # ---- quote analysis ----
    qcsv = os.path.join(HERE, "quotes_fixture.csv")
    with open(qcsv, "w") as f:
        f.write("item_code,vendor_name,rate,lead_time_days,payment_terms\n")
        f.write("CU-111-00001,MEHTA METALS,1350,25,30 days\n")
        f.write("CU-111-00001,SHREYAS ENT,1298,40,45 days\n")
        f.write("CU-111-00001,NEW VENDOR,1490,15,advance\n")
    sys.argv = ["vendor_intel.py", "--quotes", qcsv]
    vendor_intel.main()
    with open(vendor_intel.QUOTES_OUT) as f:
        qa = json.load(f)
    item = qa["items"][0]
    check(item["best_price_vendor"] == "SHREYAS ENT", "best price vendor identified")
    check(item["best_lead_vendor"] == "NEW VENDOR", "best lead-time vendor identified")
    check(item["recommended_vendor"] in ("MEHTA METALS", "SHREYAS ENT"),
          f"overall pick balances price+lead (got {item['recommended_vendor']})")

    print("\nAll smoke tests passed.")


if __name__ == "__main__":
    main()
