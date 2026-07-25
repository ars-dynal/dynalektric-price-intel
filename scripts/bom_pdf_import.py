#!/usr/bin/env python3
"""
Company-format BOM PDF importer.

Parses Dynalektric engineering BOM PDFs (the "BOM Details" report exported by
TRICO ERP, e.g. DE/BOM/26-27/07-275) into normalized JSON that bom_engine.py
accepts via --bom-json. Layout expected per line item:

    <serial><CODE-PREFIX->            e.g.  1CU-
    <mid>-                                  222-
    <serial5>                               00429
    <description ...>
    Category: <cat><UOM><qty>               Category:  CU2KGS0.90000
    <vendor sources / comment ...>

Parsing is anchored on three independent patterns (item code, Category+UOM+qty,
serial numbers); the parser cross-checks their counts and fails loudly on
mismatch instead of silently dropping lines — a format drift must never turn
into a silently short budget.

Usage
  python3 scripts/bom_pdf_import.py bom.pdf                 # -> bom.json
  python3 scripts/bom_pdf_import.py bom.pdf --out my.json --csv my.csv

SAFETY: BOM PDFs are business-sensitive. Never commit them (or their parsed
JSON/CSV) to the public repo — outputs are covered by .gitignore (data/bom_*.json,
inbox/). The max-budget workflow passes the PDF as a base64 dispatch input and
returns results only as a PRIVATE artifact.
"""
import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    raise SystemExit("Run: pip install pypdf")

UOMS = r"(?:KGS|NOS|MTR|ROL|LTR|SET|PRS|PKT|BOX|PCS|SQM|RMT)"
# NOTE: no lookahead after the 5-digit code tail — descriptions can start with
# a digit ("2-Conductor Terminal Block"), and ERP item codes are always
# exactly XX-NNN-NNNNN. The serial-sequence cross-check below catches drift.
# `\s*\n?` after the serial: pypdf 3.x emits "1CU-" on one line, pypdf 6.x
# emits "1\nCU-" — both layouts must parse (verified against both versions).
ITEM_START = re.compile(r"(?m)^(\d{1,3})\s*\n?([A-Z]{1,5}-)\s*\n(\d{3}-)\s*\n(\d{5})")
CAT_UOM_QTY = re.compile(rf"Category:\s*([A-Z0-9]+?)\s*({UOMS})\s*(\d+\.\d+)")
HEADER_ROW = re.compile(r"#\s*Code\s+Description\s+Drawing\s+UOM\s+QTY\s+Sources\s+Comment")


def extract_text(pdf_path):
    reader = PdfReader(pdf_path)
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def parse_header(text):
    hdr = {}
    m = re.search(r"BOM Number\s*(DE/BOM/[\w\-/]+)", text)
    hdr["bom_number"] = m.group(1) if m else None
    m = re.search(r"PROJECT CODE\s*:\s*([A-Z0-9 \-]+)", text)
    hdr["project_code"] = m.group(1).strip() if m else None
    m = re.search(r"(?m)^([A-Z]{2,4}\d{5,})\s", text)  # FG code e.g. PVT00000404
    hdr["fg_code"] = m.group(1) if m else None
    m = re.search(r"Description(.*?)\nVersion\b", text, re.S)
    hdr["description"] = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
    return hdr


def parse_lines(text):
    starts = list(ITEM_START.finditer(text))
    if not starts:
        raise SystemExit("PARSE ERROR: no item lines found — has the BOM PDF layout changed?")
    lines = []
    for i, m in enumerate(starts):
        serial = int(m.group(1))
        code = f"{m.group(2)}{m.group(3)}{m.group(4)}"
        block = text[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
        block = HEADER_ROW.sub("", block)
        cm = CAT_UOM_QTY.search(block)
        if not cm:
            raise SystemExit(f"PARSE ERROR: line {serial} ({code}) has no 'Category/UOM/qty' anchor — layout drift?")
        desc = re.sub(r"\s+", " ", block[:cm.start()]).strip()
        tail = re.sub(r"\s+", " ", block[cm.end():]).strip()
        lines.append({
            "serial": serial,
            "item_code": code,
            "description": desc,
            "pdf_category": cm.group(1),
            "uom": cm.group(2),
            "qty_per_unit": float(cm.group(3)),
            "sources_raw": tail or None,
        })
    # cross-checks: serials must be 1..N with no gaps
    serials = [l["serial"] for l in lines]
    expect = list(range(1, len(lines) + 1))
    if serials != expect:
        raise SystemExit(f"PARSE ERROR: serial sequence broken (got {serials[:8]}…, "
                         f"expected 1..{len(lines)}) — some lines were missed; refusing partial output.")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", help="output JSON path (default: <pdf>.json)")
    ap.add_argument("--csv", help="also write a CSV for spreadsheet review")
    args = ap.parse_args()

    text = extract_text(args.pdf)
    header = parse_header(text)
    lines = parse_lines(text)
    out = {
        "parsed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_pdf": args.pdf.split("/")[-1],
        **header,
        "line_count": len(lines),
        "lines": lines,
    }
    out_path = args.out or re.sub(r"\.pdf$", "", args.pdf, flags=re.I) + ".json"
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2)
    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["serial", "item_code", "description", "pdf_category", "uom", "qty_per_unit"])
            for l in lines:
                w.writerow([l["serial"], l["item_code"], l["description"],
                            l["pdf_category"], l["uom"], l["qty_per_unit"]])
    # stdout stays aggregate-only (safe for public Action logs)
    cats = {}
    for l in lines:
        cats[l["pdf_category"]] = cats.get(l["pdf_category"], 0) + 1
    print(f"Parsed {out['bom_number'] or 'BOM'}: {len(lines)} lines, "
          f"{len(cats)} PDF categories ({', '.join(f'{k}:{v}' for k, v in sorted(cats.items()))}).")
    print(f"Wrote {out_path}" + (f" and {args.csv}" if args.csv else "") +
          " (both PRIVATE — never commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
