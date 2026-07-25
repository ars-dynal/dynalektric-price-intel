# AI Procurement Planning upgrade — drop-in for `dynalektric-price-intel`

Three new engines that turn the existing price tracker into a procurement
planning system, following every established repo convention (aggregate-only
public logs, git-ignored private JSON, private workflow artifacts, `requests`
as the only dependency).

## What's new

| File | Purpose |
|---|---|
| `scripts/erp_common.py` | Shared ERP client + **Material Intelligence** (category buckets: Copper / Aluminium / CRGO / Oil / Hardware / Consumables / Finished Goods / Others; lead time, risk %, criticality per bucket) |
| `scripts/bom_engine.py` | **BOM → Budget engine**: explodes budgets/BOMs, nets against inventory (stock allocated earliest-delivery-first, never double-counted), computes min / expected / max budget per line from five price anchors |
| `scripts/purchase_planner.py` | **Purchase Planning**: BUY_NOW / BUY_SOON / DELAY / MONITOR per line, lead-time and market-signal aware, plus alerts (delivery risk, critical shortage, budget limit exceeded) |
| `scripts/vendor_intel.py` | **Vendor Intelligence**: 0–100 vendor score per material category from real PO history; `--quotes file.csv` mode compares incoming quotations vs expected cost / last PO / benchmark |
| `data/planning_params.json` | All policy numbers (lead times, risk %, thresholds, score weights) — editable without touching code |
| `scripts/bom_pdf_import.py` | **Company BOM PDF parser** — converts the ERP "BOM Details" PDF export (e.g. DE/BOM/26-27/07-275) into normalized JSON; fails loudly on layout drift instead of dropping lines |
| `.github/workflows/max-budget.yml` | **Max Purchase Limit calculator** — paste a base64-encoded BOM PDF + project quantity into the Actions form; result comes back as a private artifact |
| `.github/workflows/procurement-plan.yml` | Weekly BOM analysis + purchase plan (private artifacts) |
| `.github/workflows/vendor-intel.yml` | Monthly vendor scoring (private artifact) |
| `tests/smoke_test.py` | Offline test with synthetic ERP fixtures — `python3 tests/smoke_test.py` (no credentials needed; run it from the repo root after copying in, since it reads the repo's `data/public_summary.json`, `buy_signals.json`, `costing_params.json` and `item_classification.json`) |

## Install

1. Copy `scripts/`, `data/planning_params.json`, `.github/workflows/*.yml`,
   and `tests/` into the repo root (nothing is overwritten — all files are new).
2. Append `GITIGNORE_ADDITIONS.txt` to `.gitignore` **before** the first run.
3. Commit + push. Run **AI Procurement Plan** from the Actions tab
   (secrets `DEPL_CLIENT_ID` / `DEPL_CLIENT_SECRET` are already configured).
4. Download the `procurement-plan-…` private artifact for the full detail.

## How the budget bands work

For every BOM line the engine collects five anchors: last PO price, 12-month
average PO price, ERP default price, budget system rate, and a live
benchmark landed cost (NALCO/LME/CRGO via `costing.py` profiles). Then:

- **expected** = fresh PO average (≤180 d) → else benchmark landed cost →
  else PO history average → else default price → else system rate
- **min** = cheapest credible anchor
- **max** = expected × (1 + category risk %) — risk % per commodity lives in
  `planning_params.json` (Copper 10 %, CRGO 12 %, Aluminium 8 %…)

## Max Purchase Limit from a BOM PDF

```
python3 scripts/bom_pdf_import.py "bom 4.pdf"            # -> bom 4.json
python3 scripts/bom_engine.py --bom-json "bom 4.json" --units 50 --delivery-date 2026-09-15
```

Every line is matched to the ERP item master by code, priced from PO history /
live benchmarks / default prices, netted against stock, and rolled up into
min / expected / max — the **max figure is the recommended max purchase limit**
for the project. `--offline` skips the ERP for a quick metal-cost floor.
Or run it from the Actions tab (**Max Purchase Limit calculator**) by pasting
the PDF as base64 — BOM PDFs must never be committed to this public repo.

## Quote analysis

```
python3 scripts/vendor_intel.py --quotes quotes.csv
# quotes.csv: item_code,vendor_name,rate,lead_time_days,payment_terms
```

Every quote is compared against expected cost, last PO and live benchmark;
the recommended vendor is the lowest *effective* cost (rate + 0.2 %/day of
extra lead time vs the fastest quote), with flags when the best quote is
> 5 % above expected cost (negotiate) or > 15 % below (verify spec).

## Design

The full system design (architecture, DB, API, workflows, dashboards,
copilot, risk engine, future AI roadmap) is in
`design/AI_Procurement_System_Design.docx` in this package.
