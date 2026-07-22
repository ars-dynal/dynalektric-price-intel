#!/usr/bin/env python3
"""
Procurement alert engine.

Reads the buy signals and price history and emits an alert when something
actionable happens:

  * BUY_FLIP   — a metal newly entered a BUY signal (a buying opportunity).
  * HOLD_FLIP  — a metal newly entered HOLD (near its high — stop opportunistic buying).
  * BIG_MOVE   — a metal's latest monthly price moved >= MOVE_PCT vs the prior month.

State (last signal per metal, and which monthly moves were already alerted) is
kept inside data/alerts.json so the same condition isn't alerted twice. New
alerts this run are written to data/new_alerts.md; the workflow opens a GitHub
issue from that file only when it is non-empty.

All inputs derive from public benchmark prices, so alerts.json is safe to commit.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUY = os.path.join(ROOT, "data", "buy_signals.json")
HIST = os.path.join(ROOT, "data", "price_history.json")
FEED = os.path.join(ROOT, "data", "alerts.json")
NEW = os.path.join(ROOT, "data", "new_alerts.md")

MOVE_PCT = 5.0
KEEP = 60


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def main():
    signals = load(BUY, {"signals": {}}).get("signals", {})
    hist = load(HIST, {"series": {}}).get("series", {})
    feed = load(FEED, {"state": {"signals": {}, "moved": []}, "alerts": []})
    state = feed.get("state", {"signals": {}, "moved": []})
    prev_sig = state.get("signals", {})
    moved = set(state.get("moved", []))

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new = []

    for metal, sg in signals.items():
        cur = sg.get("signal")
        prev = prev_sig.get(metal)
        if cur == "BUY" and prev != "BUY":
            new.append(("BUY_FLIP", "opportunity", metal,
                        f"**{metal} → BUY.** {sg.get('headline','')} {sg.get('action','')}"))
        elif cur == "HOLD" and prev not in (None, "HOLD"):
            new.append(("HOLD_FLIP", "watch", metal,
                        f"**{metal} → HOLD.** {sg.get('headline','')} {sg.get('action','')}"))

    for metal, sd in hist.items():
        pts = sd.get("points", [])
        if len(pts) < 2:
            continue
        cur_p, prev_p = pts[-1]["v"], pts[-2]["v"]
        if not prev_p:
            continue
        chg = (cur_p - prev_p) / prev_p * 100
        key = f"{metal}:{pts[-1]['m']}"
        if abs(chg) >= MOVE_PCT and key not in moved:
            moved.add(key)
            arrow = "▲" if chg >= 0 else "▼"
            new.append(("BIG_MOVE", "watch", metal,
                        f"**{metal} {arrow} {abs(chg):.1f}%** to ₹{cur_p:,.0f}/kg ({pts[-1]['m']} vs {pts[-2]['m']})."))

    # persist
    feed["state"] = {"signals": {m: s.get("signal") for m, s in signals.items()},
                     "moved": sorted(moved)[-200:]}
    for typ, sev, metal, msg in new:
        feed.setdefault("alerts", []).insert(0, {"date": today, "type": typ, "severity": sev,
                                                 "metal": metal, "message": msg})
    feed["alerts"] = feed.get("alerts", [])[:KEEP]
    feed["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(FEED, "w") as f:
        json.dump(feed, f, indent=2)

    # new-alerts file drives the GitHub-issue step (empty => no issue)
    with open(NEW, "w") as f:
        if new:
            f.write(f"## Dynalektric price alerts — {today}\n\n")
            for _, sev, _, msg in new:
                f.write(f"- {msg}\n")
            f.write("\nSee the live dashboard for detail. _Auto-generated from public benchmark prices._\n")

    print(f"{len(new)} new alert(s)." + (" -> " + "; ".join(m for _, _, _, m in new) if new else ""))


if __name__ == "__main__":
    main()
