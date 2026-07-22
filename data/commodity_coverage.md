# Item → commodity price coverage map

Which items in the Dynalektric item master can have a **market price fetched**
for them, from what source, and how reliable that source is. Derived from the
item-master export (`items_YYYYMMDD_HHMMSS.xlsx`, ~2,100 priced items) and the
live DEPL/Trico ERP category codes.

The purpose of this map is to answer, per category: *do we compare this item to
a traded commodity benchmark, or do we price it from its own purchase-order
history?* Only a minority of items are truly "commodity-priced" — the rest are
fabricated, specialty, or one-off and should be tracked via real PO history
(which `erp_analysis.py` already pulls), not against a metal benchmark.

## Tier 1 — clean, free, public daily benchmark (fetched automatically today)

| Excel category | ERP category_code | What it is | Benchmark | Source | Status |
|---|---|---|---|---|---|
| `AL1`, `AL2` | `AL-222-` | Aluminium conductor / strip / foil (winding metal) | NALCO ingot, Rs/kg | [NALCO circular](https://nalcoindia.com/domestic/current-price/) | **Live** |
| `CU1`, `CU2` | `CU-222-` | Enamelled copper strip / wire (winding metal) | LME copper cash → Rs/kg | [westmetall.com](https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash) | **Live** |

~206 KGS-priced aluminium + copper raw-form items sit behind these two
benchmarks. This is the core of the existing pipeline and needs no paid feed.

## Tier 2 — fetchable, but NO clean free daily benchmark (indicative only)

| Excel category | What it is | Benchmark needed | Source reality |
|---|---|---|---|
| `CC` (65 KGS items, ₹93–290/kg) | **CRGO core laminations**, grades M3/M4/M5 (0.23–0.30mm) — the transformer core, a top-3 cost driver | CRGO electrical steel, Rs/kg | No NALCO/LME-style free feed. SM Steels & IndiaMART are quote-on-request; SteelMint/Fastmarkets are paid. Best handled as a **manual weekly indicative quote** or real PO price, clearly labelled "indicative". |
| `CF` (tie rods, core bolts) | MS / SS structural steel, mostly fabricated to size | HRC / MS steel, Rs/MT | OfBusiness (regional, partial), SteelMint (paid). The Patil Group RM report uses Bigmint (paid subscription). |

These are real cost drivers but should carry an **"indicative — not a clean
benchmark"** confidence flag so nobody treats a scraped supplier quote as a
settlement price. For CRGO especially, the item's own recent PO price is a more
trustworthy anchor than any free web number.

## Tier 3 — not commodity-fetchable (price from PO history, not a benchmark)

Fabricated, specialty, consumable, or one-off items. There is no meaningful
"today's market price" to fetch — track drift via their own purchase-order
history instead.

| Excel category | What it is |
|---|---|
| `FST` (~487) | Fasteners — washers, nuts, bolts (steel-derived but hardware-priced) |
| `INS`, `CMP`, `ANG` | Insulation, fibreglass sheet/block, tapes |
| `EL`, `EN` | Electrical clips/adaptors, diode blocks, converters |
| `MEA`, `HNT`, `PRD`, `TMP` | Instruments/machines, hand tools, consumables, labels/markers |
| `PKG` | Packing material |
| `PVT` | Finished transformers — this is **output**, not a raw input |

## Bottom line

- **Fetch a live benchmark for:** aluminium + copper only (Tier 1) — already done.
- **Add with an indicative flag:** CRGO laminations (`CC`) and MS steel (`CF`) —
  the biggest untracked transformer cost drivers, but honestly labelled because
  no free daily source exists.
- **Everything else:** compare each item to its own recent PO price, not to a
  commodity benchmark.
