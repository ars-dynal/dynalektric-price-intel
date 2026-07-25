#!/usr/bin/env python3
"""
Purchase Planning Engine + procurement alerts.

Consumes the BOM/budget analysis (data/bom_analysis.json, produced by
bom_engine.py) plus the market buy-timing signals (data/buy_signals.json) and
turns every net-to-buy line into a dated, prioritised purchasing action:

  BUY_NOW    latest order date (delivery date - lead time) is inside the
             buy-now window, or already past (overdue).
  BUY_SOON   market signal is BUY for that material's benchmark (price in the
             lower third of its year) and the line must be bought eventually —
             buying early captures the favourable price.
  DELAY      market signal is HOLD (price near its yearly high) AND there is
             enough schedule slack to wait for a pullback.
  MONITOR    everything else — no urgency, price mid-range.

Alert conditions emitted (aggregate counts to stdout; detail in the private
JSON): overdue lines, inventory shortages on critical materials, budget lines
whose expected cost exceeds the budget's max purchase limit.

Outputs
  data/purchase_plan.json  full plan — PRIVATE (git-ignored).
  stdout                   aggregate counts only.
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import erp_common as erp  # noqa: E402

ANALYSIS = os.path.join(erp.ROOT, "data", "bom_analysis.json")
SIGNALS = os.path.join(erp.ROOT, "data", "buy_signals.json")
OUT = os.path.join(erp.ROOT, "data", "purchase_plan.json")

# category bucket -> buy_signals.json metal key
SIGNAL_KEY = {"Copper": "Copper", "Aluminium": "Aluminium", "CRGO": "CRGO steel"}
ACTION_ORDER = {"BUY_NOW": 0, "BUY_SOON": 1, "MONITOR": 2, "DELAY": 3}


def market_signal(signals, category):
    key = SIGNAL_KEY.get(category)
    if not key:
        return None
    return (signals.get("signals", {}).get(key) or {}).get("signal")


def plan_line(line, doc, signals, params, today):
    lead = int(line.get("lead_time_days") or 14)
    delivery = erp.parse_date(doc.get("delivery_date"))
    latest_order = delivery - timedelta(days=lead) if delivery else None
    days_to_order = (latest_order - today).days if latest_order else None
    sig = market_signal(signals, line["category"])
    buy_window = params.get("buy_now_window_days", 7)
    slack_needed = params.get("delay_slack_days", 30)

    overdue = days_to_order is not None and days_to_order < 0
    if days_to_order is not None and days_to_order <= buy_window:
        action = "BUY_NOW"
        reason = (f"latest order date {'passed ' + str(-days_to_order) + 'd ago' if overdue else 'in ' + str(days_to_order) + 'd'} "
                  f"(delivery {doc.get('delivery_date')}, lead {lead}d)")
    elif sig == "BUY":
        action = "BUY_SOON"
        reason = f"market signal BUY for {line['category']} — price in lower third of its year; buy ahead of need"
    elif sig == "HOLD" and (days_to_order is None or days_to_order > slack_needed):
        action = "DELAY"
        reason = f"market signal HOLD ({line['category']} near yearly high) and {days_to_order if days_to_order is not None else '∞'}d slack — wait for a pullback"
    else:
        action = "MONITOR"
        reason = f"no urgency: {days_to_order if days_to_order is not None else 'no'}d to latest order date, signal {sig or 'n/a'}"

    priority = 1 if (overdue or (line["criticality"] == "High" and action == "BUY_NOW")) else \
               2 if action in ("BUY_NOW", "BUY_SOON") else 3
    return {
        **{k: line[k] for k in ("item_id", "item_code", "item_name", "category",
                                 "criticality", "net_buy_qty", "expected_rate",
                                 "expected_cost", "lead_time_days")},
        "budget_number": doc.get("budget_number") or doc.get("bom_number"),
        "project_code": doc.get("project_code"),
        "delivery_date": doc.get("delivery_date"),
        "latest_order_date": latest_order.isoformat() if latest_order else None,
        "days_to_latest_order": days_to_order,
        "overdue": overdue,
        "market_signal": sig,
        "action": action,
        "priority": priority,
        "reason": reason,
    }


def main():
    params = erp.load_params()
    with open(ANALYSIS) as f:
        analysis = json.load(f)
    try:
        with open(SIGNALS) as f:
            signals = json.load(f)
    except FileNotFoundError:
        signals = {"signals": {}}

    today = datetime.now(timezone.utc).date()
    plan, alerts = [], []

    for doc in analysis["documents"]:
        for line in doc["lines"]:
            if (line.get("net_buy_qty") or 0) <= 0:
                continue
            plan.append(plan_line(line, doc, signals, params, today))

        # budget-limit alert at document level
        limit = doc.get("max_purchase_limit_amount")
        if limit and doc.get("budget_expected") and doc["budget_expected"] > limit:
            alerts.append({
                "type": "BUDGET_LIMIT_EXCEEDED",
                "budget_number": doc.get("budget_number") or doc.get("bom_number"),
                "project_code": doc.get("project_code"),
                "detail": f"expected material cost Rs {doc['budget_expected']:,.0f} exceeds "
                          f"max purchase limit Rs {limit:,.0f} "
                          f"(+{(doc['budget_expected']/limit-1)*100:.1f}%)",
            })

    for p in plan:
        if p["overdue"]:
            alerts.append({"type": "DELIVERY_RISK", "item_code": p["item_code"],
                           "project_code": p["project_code"],
                           "detail": f"latest order date {p['latest_order_date']} already passed; "
                                     f"lead time {p['lead_time_days']}d may delay production"})
        if p["criticality"] == "High" and p["action"] == "BUY_NOW":
            alerts.append({"type": "CRITICAL_SHORTAGE", "item_code": p["item_code"],
                           "project_code": p["project_code"],
                           "detail": f"critical material short {p['net_buy_qty']:,.0f} units — order now"})

    plan.sort(key=lambda p: (p["priority"], ACTION_ORDER.get(p["action"], 9),
                             p["days_to_latest_order"] if p["days_to_latest_order"] is not None else 9999))

    by_action = defaultdict(lambda: {"lines": 0, "spend": 0.0})
    for p in plan:
        by_action[p["action"]]["lines"] += 1
        by_action[p["action"]]["spend"] += p.get("expected_cost") or 0

    out = {
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary_by_action": {a: {"lines": v["lines"], "expected_spend_inr": round(v["spend"], 0)}
                              for a, v in sorted(by_action.items(), key=lambda kv: ACTION_ORDER.get(kv[0], 9))},
        "alerts": alerts,
        "plan": plan,
    }
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)

    print(f"Purchase plan: {len(plan)} action lines across "
          f"{len({p['budget_number'] for p in plan})} budgets/BOMs.")
    for a in ("BUY_NOW", "BUY_SOON", "MONITOR", "DELAY"):
        if a in by_action:
            v = by_action[a]
            print(f"  {a:<9} {v['lines']:>4} lines  ~Rs {v['spend']:,.0f}")
    counts = defaultdict(int)
    for al in alerts:
        counts[al["type"]] += 1
    print("Alerts:", dict(counts) if counts else "none")
    print(f"Wrote {OUT} (PRIVATE — git-ignored, private artifact only)")


if __name__ == "__main__":
    main()
