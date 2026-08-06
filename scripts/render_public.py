#!/usr/bin/env python3
"""
Renders docs/index.html (the public, non-sensitive GitHub Pages site) from
data/public_summary.json + scripts/public_template.html.

This script deliberately never touches the private item file or any rupee/
item-code detail — it only ever reads the small aggregate JSON. The private,
full-detail dashboard (with real item codes and budgeted rates) is generated
separately (see refresh_commodity_dashboard.py) and lives only in the
Dynalektric team's private Cowork artifact, never in this public repo.

Usage: python3 render_public.py
(reads data/public_summary.json, writes docs/index.html, run from repo root)
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    with open(os.path.join(ROOT, 'data', 'public_summary.json')) as f:
        d = json.load(f)
    with open(os.path.join(ROOT, 'scripts', 'public_template.html')) as f:
        html = f.read()

    al, cu = d['aluminium'], d['copper']
    pilot, broad = d['pilot_signals'], d['broad_signals']
    conf = d['category_confidence']
    # Indicative feeds may be absent on older summaries — render dashes, never crash.
    crgo = d.get('crgo_steel') or {}
    ms = d.get('mild_steel') or {}
    ss = d.get('stainless_steel') or {}

    def price(block):
        v = block.get('price_per_kg')
        return f"{v:,.2f}" if v else "—"

    replacements = {
        '{{CRGO_PRICE}}': price(crgo),
        '{{CRGO_DATE}}': crgo.get('effective_date', '—'),
        '{{MS_PRICE}}': price(ms),
        '{{MS_DATE}}': ms.get('effective_date', '—'),
        '{{SS_PRICE}}': price(ss),
        '{{SS_DATE}}': ss.get('effective_date', '—'),
        '{{AL_PRICE}}': f"{al['price_per_kg']:,.2f}",
        '{{AL_DATE}}': al['effective_date'],
        '{{AL_SOURCE}}': al['source'],
        # Conductor reference (company doc: Hindalco EC Grade / A0 / P0610) —
        # shown once the daily fetch has captured a ready-reckoner value.
        '{{HINDALCO_LINE}}': (
            f"<br>Conductor basis: Hindalco P0610 ₹{d['aluminium_hindalco']['price_per_kg']:,.2f}/kg "
            f"(EC Grade · w.e.f. {d['aluminium_hindalco'].get('effective_date', '—')})"
            if d.get('aluminium_hindalco', {}).get('price_per_kg') else ""),
        '{{CU_PRICE}}': f"{cu['price_per_kg']:,.2f}",
        '{{CU_DATE}}': cu['settlement_date'],
        '{{CU_SOURCE}}': cu['source'],
        '{{PILOT_TOTAL}}': str(pilot['total_items']),
        '{{PILOT_CRIT}}': str(pilot['critical']),
        '{{PILOT_REVIEW}}': str(pilot['review']),
        '{{PILOT_OK}}': str(pilot['on_track']),
        '{{BROAD_TOTAL}}': str(broad['total_items']),
        '{{BROAD_CRIT}}': str(broad['critical_ge_50pct']),
        '{{BROAD_REVIEW}}': str(broad['review_15_to_50pct']),
        '{{BROAD_OK}}': str(broad['on_track_lt_15pct']),
        '{{DATA_FLAGS}}': str(broad['data_quality_flags']),
        # .get() with a fallback: older runs wrote AL1_foil/CU1_foil-style
        # keys under a since-retired 4-way split; new runs write
        # aluminium/copper directly. Falls back to 'N/A' if neither is set yet.
        '{{CONF_AL}}': conf.get('aluminium', conf.get('AL1_foil', 'N/A')),
        '{{CONF_CU}}': conf.get('copper', conf.get('CU1_foil', 'N/A')),
        '{{GENERATED_AT}}': d['generated_at_utc'],
    }
    for k, v in replacements.items():
        html = html.replace(k, v)

    out_path = os.path.join(ROOT, 'docs', 'index.html')
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        f.write(html)
    print(f"Wrote {out_path}")


if __name__ == '__main__':
    main()
