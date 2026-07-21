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

    replacements = {
        '{{AL_PRICE}}': f"{al['price_per_kg']:,.2f}",
        '{{AL_DATE}}': al['effective_date'],
        '{{AL_SOURCE}}': al['source'],
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
        '{{CONF_AL1}}': conf['AL1_foil'],
        '{{CONF_AL2}}': conf['AL2_conductor_strip'],
        '{{CONF_CU1}}': conf['CU1_foil'],
        '{{CONF_CU2}}': conf['CU2_strip'],
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
