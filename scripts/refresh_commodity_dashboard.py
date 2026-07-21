#!/usr/bin/env python3
"""
Dynalektric commodity price intelligence — dashboard regenerator.

Usage:
    python3 refresh_commodity_dashboard.py \
        --xlsx /path/to/items_YYYYMMDD_HHMMSS.xlsx \
        --al-price-per-kg 361.90 --al-date "18 Jul 2026" --al-band "356,050-367,750/MT" \
        --cu-usd-per-tonne 13373.50 --cu-date "17 Jul 2026" --usdinr 95.5 \
        --out dashboard.html

This script does NOT fetch prices itself (no guaranteed internet access at run
time) — the caller (a Claude session with WebFetch/WebSearch) looks up:
  1. NALCO aluminium ingot price -> https://nalcoindia.com/domestic/current-price/
     (follow the dated Ingot PDF link, take the mid-point of the basic price
     band in Rs/MT, divide by 1000 for Rs/kg)
  2. LME copper cash price -> https://www.westmetall.com/en/markdaten.php?action=table&field=LME_Cu_cash
     (most recent cash-settlement USD/tonne)
  3. USD/INR rate -> https://www.x-rates.com/average/?from=USD&to=INR&amount=1&year=<year>
     (latest monthly average is fine)
and passes them in as arguments. This keeps the analysis logic (item
selection, premium learning, gap computation, HTML rendering) deterministic
and reviewable, while only the three market numbers vary day to day.

Everything below reproduces the methodology from the 21 Jul 2026 pilot:
  - "Frequently used" proxy = number of pallet/warehouse stock-movement
    records per item (column with the embedded JSON, mislabeled "Location"
    in the source file) + most recent stock_date.
  - Comparable set = aluminium/copper items (categories AL1, AL2, CU1, CU2),
    UOM = KGS, priced (Default Price present), excluding finished/insulated
    forms (cable, panel, plate, wire, busbar, desk, sleeve in the name).
  - Data-quality flags = priced items far below any realistic per-kg metal
    cost (< Rs 100/kg for aluminium, < Rs 200/kg for copper) — almost
    certainly stale/placeholder entries, not real prices.
  - Learned premium per category = median (default_price / metal_price - 1)
    across that category's "near-market" items (gap within +/-15% of metal
    price), used to produce a premium-adjusted "suggested rate" instead of
    comparing raw metal price directly.
"""
import argparse
import json
import re
import statistics
from collections import defaultdict

try:
    import openpyxl
except ImportError:
    raise SystemExit("Run: pip install openpyxl --break-system-packages")

EXCLUDE_KW = re.compile(r'cable|panel|plate|wire|busbar|desk|sleeve', re.I)
METAL_CATEGORIES = ('AL1', 'AL2', 'CU1', 'CU2')


def load_items(xlsx_path):
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active
    items = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None or row[4] is None:  # need code + Product/Service (primary row)
            continue
        cat = row[7]
        if cat not in METAL_CATEGORIES:
            continue
        loc_json = row[18]  # pallet-history JSON, mislabeled "Location" in source
        n_pallets, max_date, total_qty = 0, None, 0.0
        if loc_json:
            try:
                recs = json.loads(loc_json)
                n_pallets = len(recs)
                for r in recs:
                    d = r.get('stock_date')
                    if d and (max_date is None or d > max_date):
                        max_date = d
                    try:
                        total_qty += float(r.get('total_quantity', 0) or 0)
                    except (TypeError, ValueError):
                        pass
            except (json.JSONDecodeError, TypeError):
                pass
        items.append({
            'code': row[0], 'name': row[1], 'uom': row[3], 'category': cat,
            'default_price': row[15], 'n_pallets': n_pallets, 'max_date': max_date,
            'total_qty': total_qty,
        })
    return items


def market_price(cat, al_price, cu_price):
    return al_price if cat.startswith('AL') else cu_price


def build_dataset(items, al_price, cu_price):
    priced = [it for it in items if it['default_price']]
    kgs = [it for it in priced if it['uom'] == 'KGS']
    raw_form = [it for it in kgs if not EXCLUDE_KW.search(it['name'])]

    # data quality flags: absurdly low for the metal in question
    flag_floor = {'AL1': 100, 'AL2': 100, 'CU1': 200, 'CU2': 200}
    flagged = [it for it in raw_form if it['default_price'] < flag_floor[it['category']]]
    clean = [it for it in raw_form if it not in flagged]

    for it in clean:
        mp = market_price(it['category'], al_price, cu_price)
        it['market_price'] = mp
        it['gap_pct'] = (mp - it['default_price']) / it['default_price'] * 100

    clean.sort(key=lambda r: -r['gap_pct'])
    return clean, flagged


def learn_premiums(clean):
    near = defaultdict(list)
    for r in clean:
        if -15 <= r['gap_pct'] < 15:
            near[r['category']].append(r['default_price'] / r['market_price'] - 1)
    premiums, confidence = {}, {}
    for cat, vals in near.items():
        premiums[cat] = statistics.median(vals)
        n = len(vals)
        confidence[cat] = 'High' if n >= 15 else ('Low' if n >= 1 else 'Unverified')
    fallback = statistics.median([v for vals in near.values() for v in vals]) if near else 0.0
    for cat in METAL_CATEGORIES:
        if cat not in premiums:
            premiums[cat] = fallback
            confidence[cat] = 'Unverified'
    return premiums, confidence, {k: len(v) for k, v in near.items()}


def pick_frequent(clean, top_n=18):
    ranked = sorted(clean, key=lambda x: (-x['n_pallets'], x['max_date'] or '', -x['total_qty']))
    return ranked[:top_n]


def signal(gap, thresholds=(40, 15)):
    a = abs(gap)
    if a >= thresholds[0]:
        return 'critical', 'Critical'
    if a >= thresholds[1]:
        return 'warning', 'Review'
    return 'ontrack', 'On track'


def conf_class(level):
    return {'High': 'conf-high', 'Low': 'conf-low', 'Unverified': 'conf-unv'}[level]


def render_pilot_rows(pilot_items, premiums, confidence, n_samples):
    rows = []
    for it, mp, prem in ((it, it['market_price'], premiums[it['category']]) for it in pilot_items):
        suggested = mp * (1 + prem)
        gap = (suggested - it['default_price']) / it['default_price'] * 100
        key, label = signal(gap)
        lvl = confidence[it['category']]
        cls = conf_class(lvl)
        n = n_samples.get(it['category'], 0)
        title = (f"n={n} near-market items" if lvl != 'Unverified'
                 else "no current near-market items in this category — borrowed estimate")
        barw = min(abs(gap), 100)
        name = it['name']
        name_short = name if len(name) <= 50 else name[:47] + '…'
        rows.append(f'''
      <tr>
        <td class="mono">{it['code']}</td>
        <td class="itemname" title="{name.replace('"','&quot;')}">{name_short}</td>
        <td class="mono" style="text-align:center">{it['n_pallets']}</td>
        <td class="mono" style="text-align:center">{it['max_date']}</td>
        <td class="mono num">₹{it['default_price']:,.0f}</td>
        <td class="mono num">₹{suggested:,.0f}</td>
        <td class="gapcell">
          <div class="gapbar-track"><div class="gapbar-fill status-{key}" style="width:{barw:.0f}%"></div></div>
          <span class="gapnum status-text-{key}">{'+' if gap>=0 else '−'}{abs(gap):.0f}%</span>
        </td>
        <td><span class="badge badge-{key}"><i class="dot"></i>{label}</span></td>
        <td><span class="conf-badge {cls}" title="{title}">{lvl}</span></td>
      </tr>''')
    n_crit = sum(1 for it in pilot_items
                 if signal((it['market_price']*(1+premiums[it['category']])-it['default_price'])/it['default_price']*100)[0]=='critical')
    n_warn = sum(1 for it in pilot_items
                 if signal((it['market_price']*(1+premiums[it['category']])-it['default_price'])/it['default_price']*100)[0]=='warning')
    n_ok = len(pilot_items) - n_crit - n_warn
    return "\n".join(rows), n_crit, n_warn, n_ok


def render_gap_rows(clean, top_n=15):
    rows = []
    for r in clean[:top_n]:
        key, label = signal(r['gap_pct'], thresholds=(50, 15))
        barw = min(abs(r['gap_pct']), 130) / 130 * 100
        name = r['name']
        name_short = name if len(name) <= 58 else name[:55] + '…'
        rows.append(f'''
      <tr>
        <td class="mono">{r['code']}</td>
        <td class="itemname" title="{name.replace('"','&quot;')}">{name_short}</td>
        <td class="mono num">₹{r['default_price']:,.0f}</td>
        <td class="mono num">₹{r['market_price']:,.0f}</td>
        <td class="gapcell">
          <div class="gapbar-track"><div class="gapbar-fill status-{key}" style="width:{barw:.0f}%"></div></div>
          <span class="gapnum status-text-{key}">+{r['gap_pct']:.0f}%</span>
        </td>
        <td><span class="badge badge-{key}"><i class="dot"></i>{label}</span></td>
      </tr>''')
    return "\n".join(rows)


def render_flag_rows(flagged):
    rows = []
    for r in flagged:
        name = r['name']
        name_short = name if len(name) <= 55 else name[:52] + '…'
        unit = 'kg'
        rows.append(f'''<tr>
        <td class="mono">{r['code']}</td>
        <td class="itemname">{name_short}</td>
        <td class="mono num">₹{r['default_price']:.0f}/{unit}</td>
        <td class="flagnote">Below any realistic {'aluminium' if r['category'].startswith('AL') else 'copper'} cost — likely a legacy or placeholder entry, not a real price</td>
      </tr>''')
    return "\n".join(rows) if rows else '<tr><td colspan="4" class="flagnote">No data-quality flags today.</td></tr>'


TEMPLATE_PATH_NOTE = "See commodity_price_intelligence_prototype.html for the full HTML shell — this script fills its {{PLACEHOLDERS}}."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', required=True)
    ap.add_argument('--al-price-per-kg', type=float, required=True)
    ap.add_argument('--al-date', required=True)
    ap.add_argument('--al-band', default='')
    ap.add_argument('--cu-usd-per-tonne', type=float, required=True)
    ap.add_argument('--cu-date', required=True)
    ap.add_argument('--usdinr', type=float, required=True)
    ap.add_argument('--template', required=True, help='HTML shell with {{...}} placeholders')
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    al_price = args.al_price_per_kg
    cu_price = args.cu_usd_per_tonne * args.usdinr / 1000.0

    items = load_items(args.xlsx)
    clean, flagged = build_dataset(items, al_price, cu_price)
    premiums, confidence, n_samples = learn_premiums(clean)
    pilot = pick_frequent(clean, 18)

    pilot_rows, n_crit, n_warn, n_ok = render_pilot_rows(pilot, premiums, confidence, n_samples)
    gap_rows = render_gap_rows(clean, 15)
    flag_rows = render_flag_rows(flagged)

    with open(args.template) as f:
        html = f.read()

    replacements = {
        '{{AL_PRICE}}': f'{al_price:,.2f}',
        '{{AL_DATE}}': args.al_date,
        '{{AL_BAND}}': args.al_band,
        '{{CU_PRICE}}': f'{cu_price:,.2f}',
        '{{CU_USD}}': f'{args.cu_usd_per_tonne:,.2f}',
        '{{CU_DATE}}': args.cu_date,
        '{{USDINR}}': f'{args.usdinr:.1f}',
        '{{PILOT_ROWS}}': pilot_rows,
        '{{GAP_ROWS}}': gap_rows,
        '{{FLAG_ROWS}}': flag_rows,
        '{{N_CRIT}}': str(n_crit),
        '{{N_WARN}}': str(n_warn),
        '{{N_OK}}': str(n_ok),
        '{{N_CLEAN}}': str(len(clean)),
        '{{N_TOTAL_ITEMS}}': str(len(items)),
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    with open(args.out, 'w') as f:
        f.write(html)
    print(f"Wrote {args.out}  (AL ₹{al_price:.2f}/kg, CU ₹{cu_price:.2f}/kg, {len(clean)} clean items, "
          f"{len(flagged)} flagged, pilot: {n_crit} critical / {n_warn} review / {n_ok} on track)")


if __name__ == '__main__':
    main()
