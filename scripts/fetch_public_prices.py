#!/usr/bin/env python3
"""
Best-effort daily fetch of PUBLIC aluminium/copper benchmark prices, run by
the GitHub Actions workflow (which has normal internet access, unlike some
sandboxed dev environments). Updates only the aluminium/copper price fields
in data/public_summary.json — never touches item-level counts, which are
pushed separately by the private Dynalektric-side process that has access
to the actual item file.

Sources (both free/public, no subscription):
  - NALCO ingot price circular: https://nalcoindia.com/domestic/current-price/
  - LME copper cash settlement:  https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash
  - USD/INR: falls back to a fixed recent rate if no free live source parses
    cleanly (this is the least critical input — a small error here just
    shifts the copper INR/kg conversion slightly).

This is intentionally "best effort": if a source's page layout changes and
parsing fails, the script logs a warning and leaves that field at its
previous value rather than crashing the whole workflow. Page-layout drift
is exactly the kind of thing that should eventually be handed to an
LLM-based parser (see project README) instead of brittle regex — this
script is the free, no-AI-API-cost fallback tier.
"""
import json
import os
import re
import sys

try:
    import requests
except ImportError:
    print("Run: pip install requests pypdf", file=sys.stderr)
    raise

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUMMARY_PATH = os.path.join(ROOT, 'data', 'public_summary.json')
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; DynalektricPriceBot/1.0)"}


def fetch_nalco():
    """Returns (price_per_kg, band_text, effective_date) or None on failure."""
    try:
        r = requests.get("https://nalcoindia.com/domestic/current-price/", headers=HEADERS, timeout=20)
        r.raise_for_status()
        # find the most recent dated Ingot PDF link, e.g. .../Ingot-18-07-2026.pdf
        links = re.findall(r'href="([^"]*Ingot-\d{2}-\d{2}-\d{4}\.pdf)"', r.text, re.I)
        if not links:
            print("NALCO: no dated Ingot PDF link found", file=sys.stderr)
            return None
        # pick the lexicographically-latest by parsing the date
        def parse_date(url):
            m = re.search(r'Ingot-(\d{2})-(\d{2})-(\d{4})\.pdf', url)
            return (m.group(3), m.group(2), m.group(1)) if m else ('0000', '00', '00')
        pdf_url = max(links, key=parse_date)
        if not pdf_url.startswith('http'):
            pdf_url = "https://nalcoindia.com" + pdf_url if pdf_url.startswith('/') else \
                      "https://nalcoindia.com/wp-content/uploads/2019/01/" + pdf_url

        pr = requests.get(pdf_url, headers=HEADERS, timeout=20)
        pr.raise_for_status()
        with open('/tmp/_ingot.pdf', 'wb') as f:
            f.write(pr.content)

        from pypdf import PdfReader
        text = "\n".join(page.extract_text() or '' for page in PdfReader('/tmp/_ingot.pdf').pages)

        m_date = re.search(r'w\.e\.f[.\s]+(\d{2}\.\d{2}\.\d{4})', text)
        effective_date = m_date.group(1) if m_date else "unknown"

        prices = [float(p.replace(',', '')) for p in re.findall(r'([\d,]{6,7})', text)]
        prices = [p for p in prices if 200000 <= p <= 600000]  # sane Rs/MT band for ingot
        if not prices:
            print("NALCO: PDF parsed but no plausible Rs/MT prices found", file=sys.stderr)
            return None
        lo, hi = min(prices), max(prices)
        mid_per_kg = (lo + hi) / 2 / 1000.0
        band = f"{lo:,.0f}–{hi:,.0f}/MT"
        return round(mid_per_kg, 2), band, effective_date
    except Exception as e:
        print(f"NALCO fetch failed: {e}", file=sys.stderr)
        return None


def fetch_usdinr():
    """Returns (rate_float, source_host) or None on failure.

    Tries two free, no-API-key FX endpoints, then gives up (caller keeps the
    previously stored rate). USD/INR is the least critical input here — a small
    error only nudges the copper INR/kg conversion slightly — so this stays
    best-effort like the rest of the script rather than ever crashing the run.
    """
    endpoints = [
        ("https://api.frankfurter.app/latest?from=USD&to=INR",
         lambda j: j["rates"]["INR"]),
        ("https://open.er-api.com/v6/latest/USD",
         lambda j: j["rates"]["INR"]),
    ]
    for url, extract in endpoints:
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            rate = float(extract(r.json()))
            if 50 < rate < 200:  # sane guard against a garbage parse
                return round(rate, 3), url.split("//")[1].split("/")[0]
        except Exception as e:
            print(f"USD/INR fetch via {url} failed: {e}", file=sys.stderr)
    return None


def fetch_lme_copper():
    """Returns (usd_per_tonne, date_str) or None on failure."""
    try:
        r = requests.get("https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash",
                          headers=HEADERS, timeout=20)
        r.raise_for_status()
        rows = re.findall(r'(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td[^>]*>\s*([\d.]+)', r.text)
        if not rows:
            print("LME: no rows parsed from westmetall table", file=sys.stderr)
            return None
        date_str, price_str = rows[-1]
        return float(price_str), date_str
    except Exception as e:
        print(f"LME fetch failed: {e}", file=sys.stderr)
        return None


def main():
    with open(SUMMARY_PATH) as f:
        data = json.load(f)

    nalco = fetch_nalco()
    if nalco:
        price, band, eff_date = nalco
        data['aluminium']['price_per_kg'] = price
        data['aluminium']['price_band'] = band
        data['aluminium']['effective_date'] = eff_date
        print(f"Updated aluminium: Rs {price}/kg ({band}, w.e.f. {eff_date})")
    else:
        print("Keeping previous aluminium price (fetch failed)")

    # USD/INR: fetch live (best-effort) instead of the old hardcoded 95.5.
    fx = fetch_usdinr()
    if fx:
        usdinr, fx_src = fx
        data['copper']['usdinr'] = usdinr
        data['copper']['usdinr_source'] = fx_src
        print(f"Updated USD/INR: {usdinr} (via {fx_src})")
    else:
        usdinr = data['copper'].get('usdinr', 95.5)
        print(f"Keeping previous USD/INR: {usdinr} (live fetch failed)")

    lme = fetch_lme_copper()
    if lme:
        usd_per_tonne, date_str = lme
        data['copper']['usd_per_tonne'] = usd_per_tonne
        data['copper']['settlement_date'] = date_str
        data['copper']['price_per_kg'] = round(usd_per_tonne * usdinr / 1000.0, 2)
        print(f"Updated copper: ${usd_per_tonne}/t ({date_str}) -> Rs {data['copper']['price_per_kg']}/kg at {usdinr}/USD")
    else:
        print("Keeping previous copper price (fetch failed)")

    from datetime import datetime, timezone
    data['generated_at_utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    with open(SUMMARY_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == '__main__':
    main()
