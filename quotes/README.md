# Vendor quotes — quotes/quotes.csv

Paste REAL vendor quotations here, one row per quantity tier. A valid quote
is the freshest market evidence the system can have — it outranks even PO
history on every page (budget verdicts, items page, BOM calculator), and the
verdict will cite it: "vendor quote QT-2026-081: ₹820.00 (≥50 qty tier)".

## How to add a quote (30 seconds, on GitHub)
1. Open quotes/quotes.csv on GitHub → pencil icon (Edit).
2. Add one row per quantity break from the quotation.
3. Commit. The pages pick it up on their next run.

## Columns
| column            | meaning                                    | example      |
|-------------------|--------------------------------------------|--------------|
| item_code         | ERP item code, exactly as in the ERP       | WB-606-00010 |
| vendor_name       | supplier name                              | ENERGY MATTERS |
| min_qty           | tier threshold (1 = unit price)            | 50           |
| unit_price_ex_gst | quoted rate per UOM, EX-GST                | 820.00       |
| valid_until       | quote validity date YYYY-MM-DD (blank = no expiry) | 2026-12-31 |
| quote_ref         | quotation number for the audit trail       | QT-2026-081  |

## Example (do NOT paste these — they are illustrative, not real quotes)
    WB-606-00010,ENERGY MATTERS,1,900.00,2026-12-31,QT-2026-081
    WB-606-00010,ENERGY MATTERS,50,820.00,2026-12-31,QT-2026-081
    WB-606-00010,ENERGY MATTERS,200,750.00,2026-12-31,QT-2026-081

With those rows, a BOM needing 100 units resolves to the ≥50 tier (₹820),
not the unit price. Expired quotes are ignored automatically.

## Rules
- EVIDENCE ONLY: every row must come from an actual quotation document.
- Prices are EX-GST (GST is recoverable input credit).
- This repository is public: quotes entered here are publicly visible
  (the owner has accepted this for PO/vendor data already).
