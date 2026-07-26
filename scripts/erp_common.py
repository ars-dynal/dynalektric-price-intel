#!/usr/bin/env python3
"""
Shared ERP access + material-intelligence layer for the AI Procurement engines.

One place for: OAuth, resilient pagination, item-master fetch, inventory fetch,
purchase-order history fetch, budget/BOM fetch, and the material categorizer
(ERP category code -> LLM classification cache -> keyword rules -> Others).

SAFETY: none of the functions here print anything sensitive. Engines that
import this module are responsible for keeping their own stdout aggregate-only
(public Action logs) and writing per-item detail to git-ignored files that
leave the runner only as PRIVATE workflow artifacts.
"""
import json
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

BASE = "https://depl.consult-trico.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARAMS_PATH = os.path.join(ROOT, "data", "planning_params.json")
CLASSIFICATION_PATH = os.path.join(ROOT, "data", "item_classification.json")
SUMMARY_PATH = os.path.join(ROOT, "data", "public_summary.json")
PER_PAGE = 100
REQUEST_DELAY_S = 0.15

FINISHED_KW = re.compile(r"cable|panel|busbar|desk", re.I)


def load_params():
    with open(PARAMS_PATH) as f:
        return json.load(f)


def load_summary():
    with open(SUMMARY_PATH) as f:
        return json.load(f)


def load_classification():
    try:
        with open(CLASSIFICATION_PATH) as f:
            return json.load(f).get("items", {})
    except FileNotFoundError:
        return {}


def auth():
    r = requests.post(f"{BASE}/oauth/token", json={
        "grant_type": "client_credentials",
        "client_id": os.environ["DEPL_CLIENT_ID"],
        "client_secret": os.environ["DEPL_CLIENT_SECRET"],
    }, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def make_session():
    token = auth()
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return s


def paginate(session, path, params):
    """Yields records across all pages, re-applying params every page (the ERP's
    next_page_url does not reliably preserve per_page — confirmed in prod)."""
    url = f"{BASE}{path}"
    want = dict(params)
    while url:
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query))
        q.update(want)
        full = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
        r = session.get(full, timeout=40)
        r.raise_for_status()
        pg = r.json().get("data", {})
        for rec in pg.get("data", []):
            yield rec
        url = pg.get("next_page_url")
        time.sleep(REQUEST_DELAY_S)


def fnum(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


def first_key(d, keys):
    """Return the first present, non-None value among candidate field names.
    The external API's response schemas are undocumented, so engines probe
    defensively instead of assuming one exact name."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return None


def parse_date(s):
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19 if "T" in s else 10], fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- material AI

class MaterialIntel:
    """Maps every ERP item to: category bucket (Copper/Aluminium/CRGO/Oil/
    Hardware/Consumables/Finished Goods/Others), base material, benchmark,
    lead time, risk %, criticality."""

    def __init__(self, params=None, classification=None):
        self.params = params or load_params()
        self.classification = classification if classification is not None else load_classification()
        self.prefixes = self.params["category_code_prefixes"]
        self.kw_rules = [(re.compile(r["pattern"], re.I), r["category"])
                         for r in self.params["keyword_rules"]]
        self.metal_map = self.params["classification_metal_to_category"]

    def categorize(self, item):
        """item: {code, name, category_code, material_type, product_service}."""
        name = item.get("name") or ""
        code = item.get("code") or ""
        cat_code = (item.get("category_code") or "").upper()
        mtype = (item.get("material_type") or "").lower()

        if "finished" in mtype or (item.get("product_service") == "service"):
            return "Finished Goods"
        # 1) ERP category-code prefix (authoritative for AL/CU/CC)
        for pref, bucket in self.prefixes.items():
            if cat_code.startswith(pref) or code.upper().startswith(pref + "-"):
                if bucket in ("Copper", "Aluminium") and FINISHED_KW.search(name):
                    return "Finished Goods"
                return bucket
        # 2) keyword rules (Oil / Hardware / Consumables / CRGO synonyms)
        for rx, bucket in self.kw_rules:
            if rx.search(name):
                return bucket
        # 3) LLM classification cache (base metal by item code)
        metal = (self.classification.get(code) or {}).get("metal")
        if metal and metal in self.metal_map:
            return self.metal_map[metal]
        return "Others"

    def profile(self, category):
        return self.params["categories"].get(category, self.params["categories"]["Others"])

    def enrich(self, item):
        cat = self.categorize(item)
        prof = self.profile(cat)
        return {
            "category": cat,
            "base_material": cat if cat in ("Copper", "Aluminium", "CRGO", "Oil") else "Mixed/NA",
            "benchmark": prof.get("benchmark"),
            "lead_time_days": prof["lead_time_days"],
            "risk_pct": prof["risk_pct"],
            "wastage_pct": prof.get("wastage_pct", 0.0),
            "criticality": prof["criticality"],
        }


# ------------------------------------------------------------------ fetchers

def fetch_items(session):
    """item_id -> {code, name, uom, default_price, category_code, material_type}."""
    items = {}
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        items[rec["id"]] = {
            "id": rec["id"],
            "code": rec.get("code") or first_key(rec, ["item_code", "sku"]) or "",
            "name": rec.get("name") or "",
            "uom": (rec.get("uom") or {}).get("name"),
            "default_price": fnum(rec.get("default_price")),
            "category_code": (rec.get("item_category") or {}).get("category_code"),
            "material_type": first_key(rec, ["material_type", "item_type", "type"]) or "",
            "product_service": rec.get("product_service") or rec.get("type") or "",
        }
    return items


def fetch_stock(session):
    """item_id -> total on-hand quantity (sum across warehouses/pallets)."""
    stock = defaultdict(float)
    for inv in paginate(session, "/api/external/inventory", {"per_page": PER_PAGE}):
        qty = fnum(first_key(inv, ["total_qty", "quantity", "qty", "stock"])) or 0.0
        iid = inv.get("item_id")
        if iid is not None:
            stock[iid] += qty
    return dict(stock)


QTY_KEYS = ["quantity", "qty", "order_quantity", "order_qty", "po_quantity",
            "required_quantity", "item_quantity"]


def fetch_po_history(session, since_days=730):
    """item_id -> list of {date, price, qty, vendor_id, vendor_name} PO lines."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=since_days)).isoformat()
    history = defaultdict(list)
    for po in paginate(session, "/api/external/purchase-orders", {"per_page": PER_PAGE}):
        po_date = po.get("po_date") or po.get("created_at") or ""
        if po_date and po_date[:10] < cutoff:
            continue
        vend = po.get("vendor") or {}
        vendor_id = po.get("vendor_id") or vend.get("id")
        vendor_name = vend.get("name") or vend.get("vendor_name")
        for line in (po.get("items") or []):
            iid = line.get("item_id")
            price = fnum(line.get("price"))
            if iid is None or not price or price <= 0:
                continue
            history[iid].append({
                "date": (po_date or "")[:10],
                "price": price,
                "qty": fnum(first_key(line, QTY_KEYS)) or 0.0,
                "vendor_id": vendor_id,
                "vendor_name": vendor_name,
                "po_number": po.get("po_number"),
                "status": po.get("status"),
            })
    return dict(history)


def fetch_budgets(session, project_id=None):
    """Budget headers + their bom_budget_items lines (the ERP's BOM-with-rates)."""
    params = {"per_page": PER_PAGE}
    if project_id:
        params["project_id"] = project_id
    out = []
    for b in paginate(session, "/api/external/budgets", params):
        proj = b.get("project") or {}
        out.append({
            "budget_number": b.get("budget_number"),
            "status": b.get("status"),
            "project_code": proj.get("project_code"),
            "project_name": proj.get("name") or proj.get("project_name"),
            "delivery_date": proj.get("delivery_date"),
            "max_purchase_limit_amount": fnum(b.get("max_purchase_limit_amount")),
            "lines": [{
                "item_id": it.get("item_id"),
                "quantity": fnum(it.get("quantity")) or 0.0,
                "system_rate": fnum(it.get("system_rate")),
                "vendor_rate": fnum(it.get("vendor_rate")),
            } for it in (b.get("bom_budget_items") or [])],
        })
    return out


def fetch_boms(session, project_id=None, bom_number=None):
    """Engineering BOMs. Line-item field names are probed defensively because the
    external spec documents no response schema for /boms."""
    params = {"per_page": PER_PAGE}
    if project_id:
        params["project_id"] = project_id
    if bom_number:
        params["bom_number"] = bom_number
    out = []
    for b in paginate(session, "/api/external/boms", params):
        lines = []
        raw_lines = first_key(b, ["items", "bom_items", "lines", "bom_item"]) or []
        for it in raw_lines:
            if not isinstance(it, dict):
                continue
            iid = first_key(it, ["item_id", "material_id"])
            qty = fnum(first_key(it, QTY_KEYS + ["bom_quantity"]))
            if iid is None or not qty:
                continue
            lines.append({"item_id": iid, "quantity": qty,
                          "system_rate": fnum(it.get("system_rate")),
                          "vendor_rate": fnum(it.get("vendor_rate"))})
        proj = b.get("project") or {}
        out.append({
            "bom_number": b.get("bom_number"),
            "status": b.get("status"),
            "project_code": proj.get("project_code"),
            "delivery_date": proj.get("delivery_date"),
            "lines": lines,
        })
    return out
