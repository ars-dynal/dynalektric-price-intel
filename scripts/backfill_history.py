#!/usr/bin/env python3
"""
ONE-SHOT multi-year price-history backfill (run via workflow_dispatch).

Extends data/price_history.json back to January 2024 from the same official
sources the daily refresh uses — so the trend charts show real rise-and-fall
over years, not just since the project started:

  Copper   westmetall LME cash-settlement tables (?year=2024/2025/2026),
           monthly mean USD/t x monthly mean USD-INR (frankfurter.app,
           ECB reference) / 1000 -> Rs/kg.
  Aluminium conductor (Hindalco P0610)  NEW SERIES — probes Hindalco's
           ready-reckoner PDF archive (one price per month, first circular
           found in that month). Company reference: EC Grade / A0 / P0610.
  Aluminium (NALCO) is left as-is: it already carries 14 months via the
           snalco.com rebase; no cleaner public archive exists.
  CRGO / MS / SS have no public feed — their history builds forward from
           quotes/POs only. No fake backfill.

Existing months are OVERWRITTEN for Copper (recomputed from the same source,
consistent basis); the current month is left to the daily updater.
"""
import io
import json
import os
import re
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch_public_prices as fpp  # HEADERS, get_with_retries, _parse-style regexes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "price_history.json")

START_YEAR = 2024
MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july",
               "august", "september", "october", "november", "december"]


def upsert(points, month, value):
    value = round(float(value), 2)
    for p in points:
        if p["m"] == month:
            p["v"] = value
            return
    points.append({"m": month, "v": value})
    points.sort(key=lambda p: p["m"])


def lme_monthly_usd():
    """{YYYY-MM: mean cash settlement USD/t} across all years."""
    pat = re.compile(
        r'(\d{1,2})\.?\s*(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+(\d{4})[^<]*</td>\s*<td[^>]*>\s*([\d.,]+)', re.I)
    monthly = defaultdict(list)
    this_year = date.today().year
    for year in range(START_YEAR, this_year + 1):
        url = f"https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash&year={year}"
        try:
            r = fpp.get_with_retries(url)
        except Exception as e:
            print(f"LME {year}: fetch failed {e}", file=sys.stderr)
            continue
        n = 0
        for day, mon, yr, price_str in pat.findall(r.text):
            try:
                price = float(price_str.replace(",", ""))
            except ValueError:
                continue
            if not (1000 < price < 100000) or int(yr) != year:
                continue
            m = MONTH_NAMES.index(mon.lower()) + 1
            monthly[f"{year}-{m:02d}"].append(price)
            n += 1
        print(f"LME {year}: {n} daily settlements")
        time.sleep(1)
    return {m: statistics.mean(v) for m, v in monthly.items()}


def fx_monthly():
    """{YYYY-MM: mean USD-INR} from frankfurter (ECB reference rates)."""
    out = {}
    this_year = date.today().year
    for year in range(START_YEAR, this_year + 1):
        url = f"https://api.frankfurter.app/{year}-01-01..{year}-12-31?from=USD&to=INR"
        try:
            j = fpp.get_with_retries(url, timeout=30).json()
        except Exception as e:
            print(f"FX {year}: fetch failed {e}", file=sys.stderr)
            continue
        monthly = defaultdict(list)
        for d, rates in (j.get("rates") or {}).items():
            if "INR" in rates:
                monthly[d[:7]].append(float(rates["INR"]))
        for m, v in monthly.items():
            out[m] = statistics.mean(v)
        time.sleep(1)
    print(f"FX: {len(out)} months")
    return out


def hindalco_monthly():
    """{YYYY-MM: P0610 Rs/kg} — first ready-reckoner PDF found in each month."""
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    out = {}
    today = date.today()
    months = []
    y, m = START_YEAR, 1
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    for y, m in months:
        found = None
        for d in range(1, 29):
            if (y, m) == (today.year, today.month) and d > today.day:
                break
            url = (f"https://www.hindalco.com/Upload/PDF/primary-ready-reckoner-"
                   f"{d:02d}-{MONTH_NAMES[m-1]}-{y}.pdf")
            try:
                r = fpp.requests.get(url, headers=fpp.HEADERS, timeout=15)
                if r.status_code != 200 or r.content[:4] != b"%PDF":
                    continue
                text = "\n".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(r.content)).pages)
                mm = re.search(r"P0610", text, re.I)
                if mm:
                    for num in re.findall(r"\d[\d,]{4,9}", text[mm.end():mm.end() + 250]):
                        v = float(num.replace(",", ""))
                        if 200000 <= v <= 800000:
                            found = v / 1000.0
                            break
                if found:
                    print(f"Hindalco {y}-{m:02d}: Rs {found}/kg ({d:02d}.{m:02d}.{y})")
                    break
            except Exception:
                pass
            time.sleep(0.2)
        if found:
            out[f"{y}-{m:02d}"] = found
        else:
            print(f"Hindalco {y}-{m:02d}: no circular found", file=sys.stderr)
    return out


def main():
    with open(HIST) as f:
        hist = json.load(f)
    cur_month = datetime.now(timezone.utc).strftime("%Y-%m")

    usd = lme_monthly_usd()
    fx = fx_monthly()
    cu = hist["series"].setdefault("Copper", {"indicative": False, "points": [],
                                              "source": "LME cash (westmetall.com) x USD/INR"})
    n = 0
    for m in sorted(usd):
        if m >= cur_month or m not in fx:
            continue
        upsert(cu["points"], m, usd[m] * fx[m] / 1000.0)
        n += 1
    print(f"Copper: backfilled/updated {n} months")

    hind = hindalco_monthly()
    hs = hist["series"].setdefault(
        "Aluminium conductor (Hindalco P0610)",
        {"indicative": False, "points": [],
         "source": "Hindalco primary ready reckoner, P0610 / EC Grade / alloy A0 (hindalco.com)"})
    for m, v in sorted(hind.items()):
        upsert(hs["points"], m, v)
    print(f"Hindalco: {len(hind)} months")

    hist["meta"]["backfilled_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    hist["meta"]["backfill_note"] = (
        f"Backfilled to {START_YEAR}-01: Copper from westmetall yearly tables x ECB USD-INR; "
        "Hindalco P0610 from ready-reckoner PDF archive (one circular per month). "
        "CRGO/MS/SS intentionally NOT backfilled — no public source; series build forward from quotes/POs.")
    with open(HIST, "w") as f:
        json.dump(hist, f, indent=2)
    print(f"Wrote {HIST}")


if __name__ == "__main__":
    main()
