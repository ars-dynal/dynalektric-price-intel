#!/usr/bin/env python3
"""
Appends today's benchmark prices into data/price_history.json so the monthly
trend series self-builds over time.

Run in the daily workflow AFTER fetch_public_prices.py. It reads the live
prices from data/public_summary.json and upserts ONE point per metal for the
current calendar month (YYYY-MM): if this month already has a point it is
overwritten with the latest value, otherwise a new month is appended. That
means each month ends up holding its most-recent reading — a clean monthly
series that keeps extending with no manual step.

Rs/kg throughout. Copper/aluminium come straight from the live summary;
CRGO is indicative and only updated if a value is present.
"""
import json
import os
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, "data", "price_history.json")
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")


def upsert(points, month, value):
    if value is None:
        return
    value = round(float(value), 2)
    for p in points:
        if p["m"] == month:
            p["v"] = value
            return
    points.append({"m": month, "v": value})
    points.sort(key=lambda p: p["m"])


def main():
    with open(HIST) as f:
        hist = json.load(f)
    with open(SUMMARY) as f:
        s = json.load(f)

    month = datetime.now(timezone.utc).strftime("%Y-%m")

    live = {
        "Copper": s.get("copper", {}).get("price_per_kg"),
        "Aluminium": s.get("aluminium", {}).get("price_per_kg"),
        "CRGO steel": s.get("crgo_steel", {}).get("price_per_kg"),
        "Mild Steel": s.get("mild_steel", {}).get("price_per_kg"),
        "Stainless Steel": s.get("stainless_steel", {}).get("price_per_kg"),
    }
    for metal, val in live.items():
        series = hist["series"].get(metal)
        if series is None:
            continue
        upsert(series["points"], month, val)

    hist["meta"]["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(HIST, "w") as f:
        json.dump(hist, f, indent=2)
    print(f"Updated price_history.json for {month}: " +
          ", ".join(f"{m}={v}" for m, v in live.items() if v is not None))


if __name__ == "__main__":
    main()
