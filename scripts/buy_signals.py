#!/usr/bin/env python3
"""
Buy-timing monitor for Dynalektric's base metals.

Reads data/price_history.json and, for each metal, decides whether today is a
good time to BUY (price historically low), NEUTRAL, or HOLD (price near its
recent high). The decision is transparent and rule-based:

  * range_pos  = where today's price sits in its trailing-12-month [low, high]
                 band (0 = cheapest in a year, 1 = most expensive).
  * mom3       = 3-month momentum (direction).
  * vs_avg6    = today vs the trailing 6-month average.

  BUY      : range_pos <= 0.33  (price in the lower third of the year's range)
  HOLD     : range_pos >= 0.72  (price near the top of the range)
  NEUTRAL  : in between

A short, dated market-context note per metal is layered on top so the reason
reflects known supply/demand drivers, not just the arithmetic. Output is
written to data/buy_signals.json for the dashboard and any future alerting.

This is procurement decision-support, not a guarantee — commodity prices can
move against any signal. It never places orders; it flags timing.
"""
import json
import os
import statistics
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Dated market-context notes (refresh when the macro view changes).
CONTEXT = {
    "Copper": "Near record 2026 highs on a structural refined-copper deficit; "
              "Goldman Sachs sees only a modest decline later in the year. Downside is limited.",
    "Aluminium": "Multi-year high; NALCO guides that aluminium may ease into FY27 as alumina softens. "
                 "A pullback is more likely than a further leg up.",
    "CRGO steel": "Indicative price only — a buy signal needs a real price feed or history to build.",
    "Mild Steel": "Indicative price only — a buy signal needs a real price feed or history to build.",
    "Stainless Steel": "Indicative price only — a buy signal needs a real price feed or history to build.",
}


def analyse(name, pts):
    vals = [p["v"] for p in pts]
    cur = vals[-1]
    lo, hi = min(vals), max(vals)
    rng = hi - lo
    pos = (cur - lo) / rng if rng > 0 else 0.5
    avg6 = statistics.mean(vals[-min(6, len(vals)):])
    base = vals[-4] if len(vals) >= 4 else vals[0]
    mom3 = (cur - base) / base * 100 if base else 0.0
    vs_avg6 = (cur - avg6) / avg6 * 100 if avg6 else 0.0

    if pos <= 0.33:
        sig, cls, head = "BUY", "buy", "Favourable — price in the lower third of its year"
    elif pos >= 0.72:
        sig, cls, head = "HOLD", "hold", "Elevated — near the top of its year"
    else:
        sig, cls, head = "NEUTRAL", "neutral", "Mid-range — buy to plan"

    # momentum nuance
    if sig == "HOLD" and mom3 <= -3:
        head = "Elevated but pulling back — watch for an entry"
    elif sig == "BUY" and mom3 >= 3:
        head = "Cheap but rising — buy soon, window may close"

    reason = (f"At ₹{cur:,.0f}/kg it sits {pos*100:.0f}% up its trailing-12-month range "
              f"(₹{lo:,.0f}–₹{hi:,.0f}), {vs_avg6:+.0f}% vs its 6-month average, "
              f"3-month momentum {mom3:+.0f}%. {CONTEXT.get(name, '')}")

    action = {
        "BUY": "Good stock-up window — bring forward non-urgent volumes.",
        "NEUTRAL": "Buy to requirement; no strong timing edge either way.",
        "HOLD": "Cover essential needs only; wait for a dip before opportunistic buying.",
    }[sig]

    return dict(signal=sig, cls=cls, headline=head, reason=reason, action=action,
                cur=round(cur, 2), lo=round(lo, 2), hi=round(hi, 2),
                pos=round(pos, 3), mom3=round(mom3, 1), vs_avg6=round(vs_avg6, 1))


def main():
    with open(os.path.join(ROOT, "data", "price_history.json")) as f:
        hist = json.load(f)

    out = {"generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "disclaimer": "Procurement decision-support based on public benchmark prices. "
                         "Not investment advice; prices can move against any signal.",
           "signals": {}}
    for name, sd in hist.get("series", {}).items():
        pts = sd.get("points", [])
        if sd.get("indicative") or len(pts) < 3:
            out["signals"][name] = dict(signal="NO SIGNAL", cls="none",
                                        headline="Insufficient / indicative data",
                                        reason=CONTEXT.get(name, "Not enough history yet."),
                                        action="Configure a price source or wait for history to build.",
                                        cur=(pts[-1]["v"] if pts else None))
            continue
        out["signals"][name] = analyse(name, pts)

    with open(os.path.join(ROOT, "data", "buy_signals.json"), "w") as f:
        json.dump(out, f, indent=2)
    print("Buy signals: " + " | ".join(f"{k}={v['signal']}" for k, v in out["signals"].items()))


if __name__ == "__main__":
    main()
