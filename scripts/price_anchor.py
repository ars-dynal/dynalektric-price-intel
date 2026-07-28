#!/usr/bin/env python3
"""
Tier-2 statistical price anchor — shared by item_intel (live pages),
bom_engine (BOM calculator) and backtest_benchmark (accuracy measurement),
so the live system and the accuracy report always use the SAME logic.

Pipeline (every step explainable, every knob in data/planning_params.json):
  1. Window     POs strictly before `asof`, last 365 days (else all prior).
  2. Outliers   drop prices more than `outlier_ratio` x away from the median
                (unit/pack-size entry errors, e.g. Rs 3.54 vs Rs 0.16).
  3. Age weight exponentially-weighted average, half-life `po_halflife_days`
                (90d default): last week's PO counts ~4x a 6-month-old one.
  4. Trend      with >= `trend_min_pos` clean POs spanning >= 60 days, fit a
                linear trend and project to `asof`; applied only when the
                move is >= `trend_min_monthly_pct` %/month and capped at
                +/- `trend_cap_pct` % — no runaway extrapolation.
  5. Calibrate  bounded per-category correction learned by the monthly
                back-test from its own historical errors
                (data/category_calibration.json), capped +/- `calibration_cap_pct` %.

Returns (rate, note) — the note spells out exactly what was done, feeding the
WHY culture of the budget page.
"""
import json
import os
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALIB_PATH = os.path.join(ROOT, "data", "category_calibration.json")

DEFAULTS = {"po_halflife_days": 90.0, "outlier_ratio": 2.5,
            "trend_min_pos": 4, "trend_cap_pct": 10.0, "trend_min_monthly_pct": 1.0,
            "calibration_cap_pct": 5.0, "calibration_min_lines": 30}


def _p(params, key):
    return (params or {}).get(key, DEFAULTS[key])


def load_calibration():
    try:
        with open(CALIB_PATH) as f:
            return json.load(f).get("prefixes", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def anchor(pos, asof, params=None, calib=None, prefix=None):
    """pos: [{date 'YYYY-MM-DD', price, qty}, ...]; asof: 'YYYY-MM-DD'.
    Uses ONLY POs dated strictly before asof (safe for back-testing)."""
    prior = [p for p in pos if p.get("date") and p["date"] < asof and (p.get("price") or 0) > 0]
    if not prior:
        return None, None
    yr_cut = (datetime.fromisoformat(asof[:10]) - timedelta(days=365)).date().isoformat()
    window = [p for p in prior if p["date"] >= yr_cut] or prior

    # 2. outlier rejection around the median
    med = _median([p["price"] for p in window])
    ratio = _p(params, "outlier_ratio")
    clean = [p for p in window if med / ratio <= p["price"] <= med * ratio]
    dropped = len(window) - len(clean)
    if not clean:                      # pathological spread — median is safest
        return round(med, 2), f"median of {len(window)} PO(s) (wide spread)"

    # 3. exponentially-weighted average (recent POs dominate)
    asof_d = datetime.fromisoformat(asof[:10]).date()
    half = _p(params, "po_halflife_days")
    pts = []
    for p in clean:
        age = (asof_d - datetime.fromisoformat(p["date"][:10]).date()).days
        pts.append((age, p["price"], 0.5 ** (age / half)))
    wsum = sum(w for _, _, w in pts)
    rate = sum(pr * w for _, pr, w in pts) / wsum
    note = f"age-weighted avg of {len(clean)} PO(s), half-life {half:.0f}d"
    if dropped:
        note += f", {dropped} outlier(s) dropped"

    # 4. trend projection to asof
    span = max(a for a, _, _ in pts) - min(a for a, _, _ in pts)
    if len(pts) >= _p(params, "trend_min_pos") and span >= 60:
        n = len(pts)
        mx = sum(-a for a, _, _ in pts) / n            # time axis: -age (forward = larger)
        my = sum(pr for _, pr, _ in pts) / n
        sxx = sum((-a - mx) ** 2 for a, _, _ in pts)
        sxy = sum((-a - mx) * (pr - my) for a, pr, _ in pts)
        if sxx > 0:
            slope = sxy / sxx                           # Rs per day
            monthly_pct = slope * 30.4 / rate * 100
            if abs(monthly_pct) >= _p(params, "trend_min_monthly_pct"):
                center_age = sum(a * w for a, _, w in pts) / wsum
                adj_pct = slope * center_age / rate * 100
                cap = _p(params, "trend_cap_pct")
                adj_pct = max(-cap, min(cap, adj_pct))
                rate *= (1 + adj_pct / 100)
                note += f", trend {monthly_pct:+.1f}%/mo → projected {adj_pct:+.1f}%"

    # 5. bounded category calibration (learned by the back-test)
    if calib and prefix:
        c = calib.get(prefix)
        if c and c.get("n", 0) >= _p(params, "calibration_min_lines"):
            cap = _p(params, "calibration_cap_pct")
            adj = max(-cap, min(cap, -float(c.get("bias_pct", 0.0))))
            if abs(adj) >= 0.5:
                rate *= (1 + adj / 100)
                note += f", category calibration {adj:+.1f}% (from back-test of {c['n']} lines)"

    return round(rate, 2), note
