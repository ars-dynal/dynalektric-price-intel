#!/usr/bin/env python3
"""
AI item classification — assigns each raw-material item a base metal using the
free open-source LLM (via scripts/llm.py), instead of relying only on the ERP's
AL-222-/CU-222- category codes.

This is what extends the tracker BEYOND copper/aluminium: it reads each item's
NAME (not sensitive) and classifies its primary base metal as one of
Copper / Aluminium / CRGO steel / Stainless Steel / Mild Steel / Other, plus a
coarse form. Results are cached in data/item_classification.json keyed by item
code, so each run only classifies items it hasn't seen — cheap on the free tier.

Candidates = items priced by weight/length (uom KGS or MTR) — the raw-material
universe where a base metal is meaningful. Item codes are already public on the
site; a base-metal label is not sensitive, so the cache is committed.

Falls back gracefully: if no LLM key is set, it does nothing (keeps the existing
category-based detection).
"""
import json
import os
import sys
import time
from collections import Counter
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

BASE = "https://depl.consult-trico.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, "data", "item_classification.json")
PER_PAGE = 100
BATCH = 40
CANDIDATE_UOM = {"KGS", "MTR"}
METALS = ["Copper", "Aluminium", "CRGO steel", "Stainless Steel", "Mild Steel", "Other"]

SYSTEM = (
    "You are a materials engineer classifying electrical/transformer manufacturing "
    "inventory by PRIMARY base metal. Respond with strict JSON only."
)
INSTR = (
    "For each item, output its primary base metal as exactly one of: "
    f"{', '.join(METALS)}. Also give a short 'form' (e.g. strip, wire, foil, sheet, "
    "lamination, rod, busbar, sleeve, other). CRGO/grain-oriented/electrical steel "
    "-> 'CRGO steel'. SS/304/316 -> 'Stainless Steel'. MS/mild steel/CRCA -> "
    "'Mild Steel'. Enamelled/ETP copper -> 'Copper'. If not a metal, 'Other'. "
    'Return {"results":[{"code":"..","metal":"..","form":".."}]} in the same order.'
)


def auth():
    r = requests.post(f"{BASE}/oauth/token", json={
        "grant_type": "client_credentials",
        "client_id": os.environ["DEPL_CLIENT_ID"],
        "client_secret": os.environ["DEPL_CLIENT_SECRET"],
    }, timeout=20)
    r.raise_for_status()
    return r.json()["access_token"]


def paginate(session, path, params):
    url = f"{BASE}{path}"
    want = dict(params)
    while url:
        parts = urlsplit(url)
        q = dict(parse_qsl(parts.query)); q.update(want)
        full = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
        r = session.get(full, timeout=40); r.raise_for_status()
        pg = r.json().get("data", {})
        for rec in pg.get("data", []):
            yield rec
        url = pg.get("next_page_url")
        time.sleep(0.15)


def classify_batch(items, chat_json=None):
    """items: list of {code,name}. Returns {code: {metal, form}}."""
    chat_json = chat_json or llm.chat_json
    payload = json.dumps([{"code": i["code"], "name": i["name"][:160]} for i in items], ensure_ascii=False)
    out = chat_json(SYSTEM, INSTR + "\n\nItems:\n" + payload)
    res = {}
    for r in out.get("results", []):
        metal = r.get("metal")
        if metal in METALS:
            res[str(r.get("code"))] = {"metal": metal, "form": r.get("form", "")}
    return res


def load_cache():
    try:
        with open(CACHE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"generated_at_utc": None, "model": llm.MODEL, "items": {}}


def fetch_candidates(session):
    cands = []
    for rec in paginate(session, "/api/external/items", {"per_page": PER_PAGE}):
        uom = (rec.get("uom") or {}).get("name")
        name = rec.get("name")
        if uom in CANDIDATE_UOM and name:
            cands.append({"code": rec.get("code"), "name": name, "uom": uom,
                          "cat": (rec.get("item_category") or {}).get("category_code"),
                          "rate": rec.get("default_price")})
    return cands


REAL_METALS = {"Copper", "Aluminium", "CRGO steel", "Stainless Steel", "Mild Steel"}


def write_items_json(cands, known):
    """Regenerate data/items.json to include every KGS metal item (all base metals,
    not just AL/CU), so the dashboard shows the full classified universe."""
    items = []
    for c in cands:
        if c.get("uom") != "KGS":
            continue
        metal = (known.get(c["code"]) or {}).get("metal")
        if metal not in REAL_METALS:
            continue
        items.append({"code": c["code"], "name": c["name"], "cat": c.get("cat"),
                      "metal": metal, "uom": "KGS", "rate": c.get("rate")})
    if len(items) < 200:  # safety: don't clobber the existing list on a bad/partial run
        print(f"Only {len(items)} classified KGS metal items — keeping existing items.json.")
        return
    path = os.path.join(ROOT, "data", "items.json")
    with open(path) as f:
        prev = json.load(f)
    prev["items"] = items
    prev["item_count"] = len(items)
    prev["source_export"] = "ERP live + AI classification"
    with open(path, "w") as f:
        json.dump(prev, f, ensure_ascii=False, indent=0)
    from collections import Counter
    print(f"Wrote data/items.json with {len(items)} classified KGS metal items: "
          f"{dict(Counter(i['metal'] for i in items))}")


def main():
    if not llm.available():
        print("No LLM key set (LLM_API_KEY / GROQ_API_KEY). Skipping classification.")
        return
    cache = load_cache()
    known = cache["items"]

    token = auth()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    cands = fetch_candidates(session)
    todo = [c for c in cands if c["code"] not in known]
    print(f"{len(cands)} candidate items, {len(todo)} new to classify (model {llm.MODEL})")

    done = 0
    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        try:
            res = classify_batch(batch)
            known.update(res)
            done += len(res)
        except Exception as e:
            print(f"  batch {i//BATCH} failed: {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(1.0)  # be polite to the free tier

    from datetime import datetime, timezone
    cache["generated_at_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    cache["model"] = llm.MODEL
    cache["items"] = known
    with open(CACHE, "w") as f:
        json.dump(cache, f, indent=2)

    counts = Counter(v["metal"] for v in known.values())
    print(f"Classified {done} new; cache now {len(known)} items.")
    print("By base metal:", dict(counts))

    # Expand the dashboard item list to the full classified metal universe.
    write_items_json(cands, known)


if __name__ == "__main__":
    main()
