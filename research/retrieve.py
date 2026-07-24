"""Retrieval - turn a natural-language request into a runnable routine plan.

The last piece of the slice: given "send a message to John on LinkedIn", pick the
best-matching named routine from the routine DB and fill its variable slots from
the request, then emit a plan for replay_agentbrowser.py.

LOCAL model only (localhost guard) - the request + routine catalog never leave the
machine. The MATCH + SLOT-FILL is the small, legitimate LLM step (Tier-2 seam); the
routine's structure comes deterministically from the DB.

  LLAMA_URL=http://localhost:8081 python3 retrieve.py "send John a message on linkedin saying running late" \
      [--db routines.db] [--site linkedin] [--out plan.json]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request
from urllib.parse import urlparse

URL = os.environ.get("LLAMA_URL", "http://localhost:8081") + "/v1/chat/completions"
if (urlparse(URL).hostname or "").lower() not in ("localhost", "127.0.0.1", "::1", "[::1]"):
    sys.exit(f"[retrieve] refusing non-localhost LLAMA_URL ({URL}); request data stays on device.")

SYSTEM = (
    "You route a user's natural-language request to one of their known computer routines and "
    "fill its variable slots. Think briefly. Output ONLY compact JSON: "
    '{"routine_id": int or null if none fit, "confidence": 0..1, '
    '"slots": {slot_name: extracted_value, ...}}. Only fill slots listed for the chosen routine.'
)


def call_llm(prompt):
    body = json.dumps({"messages": [{"role": "system", "content": SYSTEM},
                                    {"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 700}).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        content = json.load(r)["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", content, re.S)
    return json.loads(m.group(0)) if m else None


def catalog(con, site=None):
    q = ("SELECT id, name, description, app, site FROM routine "
         "WHERE name IS NOT NULL AND confidence >= 0.5")
    params = ()
    if site:
        q += " AND site LIKE ?"
        params = (f"%{site}%",)
    q += " ORDER BY confidence DESC, occurrences DESC LIMIT 40"
    routines = con.execute(q, params).fetchall()
    out = []
    for r in routines:
        slots = [s[0] for s in con.execute(
            "SELECT slot_name FROM step WHERE routine_id=? AND is_slot=1 ORDER BY ordinal", (r[0],))]
        out.append({"id": r[0], "name": r[1], "description": r[2], "app": r[3], "site": r[4], "slots": slots})
    return out


def emit_plan(con, routine_id, slot_values):
    steps = con.execute(
        "SELECT ordinal, op, ax_role, ax_name, ocr_text, is_slot, slot_name "
        "FROM step WHERE routine_id=? ORDER BY ordinal", (routine_id,)).fetchall()
    plan = []
    for ordn, op, role, name, ocr, is_slot, slot_name in steps:
        step = {"op": op, "target": name, "role": role, "ocr_text": ocr,
                "guard": {"expect_element": name, "expect_role": role}}
        if is_slot and slot_name and slot_name in (slot_values or {}):
            step["value"] = slot_values[slot_name]
            step["slot"] = slot_name
        plan.append(step)
    return plan


def main():
    args = sys.argv[1:]
    request = next((a for a in args if not a.startswith("--")), None)
    if not request:
        sys.exit('usage: retrieve.py "your request" [--db routines.db] [--site linkedin] [--out plan.json]')
    db = _opt(args, "--db", os.path.join(os.path.dirname(__file__), "routines.db"))
    site = _opt(args, "--site", None)
    out = _opt(args, "--out", None)

    con = sqlite3.connect(db)
    cat = catalog(con, site)
    if not cat:
        sys.exit(f"[retrieve] no named routines{' for site '+site if site else ''} - run tier2_consolidate first.")
    lines = "\n".join(
        f'[{c["id"]}] {c["name"]} - {c["description"]} (app={c["app"]}, site={c["site"] or "-"}) '
        f'slots: {", ".join(c["slots"]) or "none"}' for c in cat)
    result = call_llm(f'Request: "{request}"\n\nKnown routines:\n{lines}\n\nPick the best routine_id and fill its slots.')
    if not result or result.get("routine_id") is None:
        print(json.dumps({"request": request, "matched": None,
                          "reason": "no routine matched", "llm": result}, indent=2))
        return
    rid = int(result["routine_id"])
    plan = emit_plan(con, rid, result.get("slots") or {})
    chosen = next((c for c in cat if c["id"] == rid), {})
    payload = {"request": request, "matched_routine_id": rid, "matched_name": chosen.get("name"),
               "match_confidence": result.get("confidence"), "slots": result.get("slots"),
               "plan": plan, "next": "run: python3 replay_agentbrowser.py <this plan> --dry-run"}
    print(json.dumps(payload, indent=2))
    if out:
        json.dump(plan, open(out, "w"), indent=2)
        print(f"\nplan written to {out}")


def _opt(args, flag, default):
    return args[args.index(flag) + 1] if flag in args and args.index(flag) + 1 < len(args) else default


if __name__ == "__main__":
    main()
