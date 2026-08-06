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
HEADERS = {
    # A plain browser UA: some market-data sites (westmetall included) return
    # 403 or an empty table to anything that self-identifies as a bot.
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_with_retries(url, tries=3, timeout=20):
    """GET with small backoff — one-off network blips shouldn't lose a day's price."""
    import time
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:
            last = e
            if i < tries - 1:
                time.sleep(5 * (i + 1))
    raise last


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


_MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June",
     "July", "August", "September", "October", "November", "December"])}


def _parse_lme_rows(html):
    """Westmetall's English table writes dates as '24. July 2026' (NOT
    24.07.2026) and prices with thousands-commas ('13,617.00'). Parse every
    (date, cash-settlement) pair — cash settlement is the first price cell
    after the date — and return the newest one. Never trust row order."""
    from datetime import datetime as _dt
    pat = re.compile(
        r'(\d{1,2})\.?\s*(January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+(\d{4})[^<]*</td>\s*<td[^>]*>\s*([\d.,]+)',
        re.I)
    best = None
    for day, mon, year, price_str in pat.findall(html):
        try:
            d = _dt(int(year), _MONTHS[mon.capitalize()], int(day))
            price = float(price_str.replace(",", ""))
        except (ValueError, KeyError):
            continue
        if not (1000 < price < 100000):  # sane guard against a garbage parse
            continue
        if best is None or d > best[0]:
            best = (d, price)
    if best is None:
        return None
    return best[1], best[0].strftime("%d %b %Y")


def fetch_hindalco():
    """Hindalco 'Primary metal price Ready Reckoner' PDF — the company's
    official reference for ALUMINIUM CONDUCTOR (EC Grade, alloy A0, P0610).
    PDFs live at a predictable URL per issue date; probe the last 20 days for
    the newest one. Returns (p0610_per_kg, ec_wire_rod_per_kg, wef_date) or None."""
    from datetime import date, timedelta as _td
    import io
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader  # older runners
    months = ["january", "february", "march", "april", "may", "june", "july",
              "august", "september", "october", "november", "december"]
    today = date.today()
    for back in range(0, 20):
        d = today - _td(days=back)
        url = (f"https://www.hindalco.com/Upload/PDF/primary-ready-reckoner-"
               f"{d.day:02d}-{months[d.month-1]}-{d.year}.pdf")
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            if r.status_code != 200 or not r.content[:4] == b"%PDF":
                continue
            text = "\n".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(r.content)).pages)
            # Prices are Rs/MT ("402000" or Indian-grouped "4,02,000"), but
            # spec digits sit between the keyword and the price ("P0610
            # (99.85% min)..."), so: scan a window after the keyword and take
            # the first number in a plausible Rs/MT band for aluminium.
            def grab(pattern):
                m = re.search(pattern, text, re.I)
                if not m:
                    return None
                for num in re.findall(r"\d[\d,]{4,9}", text[m.end():m.end() + 250]):
                    v = float(num.replace(",", ""))
                    if 200000 <= v <= 800000:
                        return v / 1000.0
                return None
            p0610 = grab(r"P0610")
            ec_rod = grab(r"EC\s*Grade\s*Wire\s*Rods?")
            if p0610 or ec_rod:
                return p0610, ec_rod, d.strftime("%d.%m.%Y")
            print(f"Hindalco: PDF {d} parsed but no P0610/EC rod match", file=sys.stderr)
        except Exception as e:
            print(f"Hindalco probe {d}: {type(e).__name__}: {e}", file=sys.stderr)
    print("Hindalco: no ready-reckoner PDF found in the last 20 days", file=sys.stderr)
    return None


def fetch_lme_copper():
    """Returns (usd_per_tonne, date_str) or None on failure."""
    try:
        r = get_with_retries("https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash")
        parsed = _parse_lme_rows(r.text)
        if parsed is None:
            # Print what we actually received, so the run log shows WHY
            # (layout change vs block page) instead of a silent shrug.
            print(f"LME: no rows parsed (HTTP {r.status_code}, {len(r.text)} bytes). "
                  f"Page head: {r.text[:300]!r}", file=sys.stderr)
            return None
        return parsed
    except Exception as e:
        print(f"LME fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
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

    hind = fetch_hindalco()
    if hind:
        p0610, ec_rod, wef = hind
        h = data.setdefault('aluminium_hindalco', {})
        if p0610:
            h['price_per_kg'] = p0610
        if ec_rod:
            h['ec_wire_rod_per_kg'] = ec_rod
        h['grade'] = 'EC Grade, alloy A0, product code P0610'
        h['effective_date'] = wef
        h['source'] = 'Hindalco primary ready reckoner (hindalco.com)'
        print(f"Updated Hindalco: P0610 Rs {p0610}/kg, EC wire rod Rs {ec_rod}/kg (w.e.f. {wef})")
    else:
        print("Keeping previous Hindalco aluminium-conductor price (fetch failed)")

    lme = fetch_lme_copper()
    if lme:
        usd_per_tonne, date_str = lme
        data['copper']['usd_per_tonne'] = usd_per_tonne
        data['copper']['settlement_date'] = date_str
        data['copper']['price_per_kg'] = round(usd_per_tonne * usdinr / 1000.0, 2)
        print(f"Updated copper: ${usd_per_tonne}/t ({date_str}) -> Rs {data['copper']['price_per_kg']}/kg at {usdinr}/USD")
    else:
        # ::warning:: makes this show up as a yellow annotation on the run
        # page, so a silently-stale copper price can't hide in green runs.
        print(f"::warning::LME copper fetch failed — page still shows "
              f"{data['copper'].get('settlement_date')} settlement "
              f"(${data['copper'].get('usd_per_tonne')}/t). Check westmetall.com layout/blocking.")
        print("Keeping previous copper price (fetch failed)")

    from datetime import datetime, timezone
    data['generated_at_utc'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    with open(SUMMARY_PATH, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"Wrote {SUMMARY_PATH}")


if __name__ == '__main__':
    main()
