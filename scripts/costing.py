#!/usr/bin/env python3
"""
Landed-cost engine — Dynalektric's own costing formula, driven by live prices.

Turns a live metal benchmark into a finished item cost exactly as the company's
costing sheets do:

  Raw Material = (LME + Premium) x FX / 1000      (per kg; for aluminium the
                 domestic NALCO basic price is used directly as RM)
  + Conversion Rate (INR/kg)
  + Coating Rate   (INR/kg)
  + Packing & Fwd  (either % of RM, or a flat INR/kg  e.g. HDPE + pallet)
  + Freight Rate   (INR/kg)
  + Other Charges  (INR)
  - Cash Discount  (% of subtotal)      [foil variant]
  = Total ex-GST   (the real, credit-recoverable cost to compare against)
  + IGST %         -> Total incl-GST

Parameters live in data/costing_params.json (per metal-form profile) so the
formula is fixed and auditable while the rates stay editable. GST is normally
recoverable as input credit, so cost comparisons should use TOTAL EX-GST.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS = os.path.join(ROOT, "data", "costing_params.json")
SUMMARY = os.path.join(ROOT, "data", "public_summary.json")


def raw_material_inr_kg(profile, summary):
    """RM cost per kg from the live benchmark for this profile's base metal."""
    bench = profile["benchmark"]
    if bench == "Copper":
        usd = summary["copper"]["usd_per_tonne"]
        fx = summary["copper"]["usdinr"]
        premium = profile.get("premium_usd_mt", 0) or 0
        return (usd + premium) * fx / 1000.0
    if bench == "Aluminium":
        # NALCO basic price is already the domestic INR/kg metal cost.
        rm = summary["aluminium"]["price_per_kg"]
        # optional premium expressed in INR/kg
        return rm + (profile.get("premium_inr_kg", 0) or 0)
    if bench == "CRGO steel":
        return summary.get("crgo_steel", {}).get("price_per_kg", 0) or 0
    raise ValueError(f"Unknown benchmark {bench}")


def finished_cost(profile, summary, gst_pct=18.0):
    rm = raw_material_inr_kg(profile, summary)
    conv = profile.get("conversion_rate", 0) or 0
    coat = profile.get("coating_rate", 0) or 0
    basic = rm + conv + coat
    packing = (profile.get("packing_fwd_pct", 0) or 0) * rm / 100.0 + (profile.get("packing_flat", 0) or 0)
    subtotal = basic + packing + (profile.get("freight_rate", 0) or 0) + (profile.get("other_charges", 0) or 0)
    cd = subtotal * (profile.get("cash_discount_pct", 0) or 0) / 100.0
    ex_gst = subtotal - cd
    g = profile.get("gst_pct", gst_pct)
    gst = ex_gst * g / 100.0
    return {
        "profile": profile.get("name"),
        "benchmark": profile["benchmark"],
        "raw_material": round(rm, 2),
        "basic": round(basic, 2),
        "total_ex_gst": round(ex_gst, 2),
        "gst": round(gst, 2),
        "total_incl_gst": round(ex_gst + gst, 2),
        "confirmed": not profile.get("_confirm", False),
    }


def all_costs():
    with open(PARAMS) as f:
        cfg = json.load(f)
    with open(SUMMARY) as f:
        summary = json.load(f)
    out = []
    for name, prof in cfg.get("profiles", {}).items():
        prof = dict(prof, name=name)
        prof.setdefault("gst_pct", cfg.get("gst_pct", 18))
        out.append(finished_cost(prof, summary))
    return out


if __name__ == "__main__":
    for c in all_costs():
        flag = "" if c["confirmed"] else "  (PARAMS TO CONFIRM)"
        print(f"{c['profile']:22} RM ₹{c['raw_material']:>8.1f} -> ex-GST ₹{c['total_ex_gst']:>8.1f} "
              f"| incl-GST ₹{c['total_incl_gst']:>8.1f}{flag}")
