"""Tier-2 semantic consolidation - the ONLY stage allowed to fill the LLM columns.

Runs a LOCAL model (llama.cpp server on localhost) over the deterministically-mined
routines and writes ONLY the five Tier-2 columns:
    routine.name, routine.description, routine.confidence, step.is_slot, step.slot_name
It NEVER touches the deterministic base (app/site/step_count/occurrences/first_seen/
last_seen/op/ax_role/ax_name/ocr_text/bbox/evidence). The routine data goes only to
localhost - nothing leaves the machine (local-first).

It also acts as a QUALITY FILTER: routines the model judges non-delegable (noise, e.g.
a repetitive window-click loop) get name/description NULL and confidence 0.

  LLAMA_URL=http://localhost:8081  python3 tier2_consolidate.py [routines.db] [limit]
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import urllib.request

DB = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "routines.db")
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 30
URL = os.environ.get("LLAMA_URL", "http://localhost:8081") + "/v1/chat/completions"

SYSTEM = (
    "You label a user's recurring computer routine so it can later be retrieved by a "
    "natural-language request and replayed. Think briefly, then output ONLY compact JSON: "
    '{"name": short imperative task name or null if this is not a real delegable task, '
    '"description": one sentence a user might say to trigger it, '
    '"is_delegable_task": true|false (false = noise like repetitive window clicks), '
    '"confidence": 0..1, '
    '"slots": [{"ordinal": int, "slot_name": snake_case}] for the steps whose value varies '
    "per run (e.g. the recipient, the message text). No prose outside the JSON."
)


def call_llm(steps, app, occ):
    lines = [f"{s['ordinal']} {s['op']} {s['ax_name'] or '['+(s['ax_role'] or 'element')+']'}"
             + (f"  (screen text: {s['ocr_text']})" if s['ocr_text'] else "")
             for s in steps]
    user = f"App: {app}. Recurs {occ}x. Steps:\n" + "\n".join(lines)
    body = json.dumps({
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "temperature": 0, "max_tokens": 900,
    }).encode()
    req = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        content = json.load(r)["choices"][0]["message"].get("content") or ""
    m = re.search(r"\{.*\}", content, re.S)          # extract the JSON object
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    routines = con.execute(
        "SELECT id, app, occurrences FROM routine ORDER BY occurrences*step_count DESC LIMIT ?",
        (LIMIT,)).fetchall()

    named = noise = failed = slots_written = 0
    for r in routines:
        steps = con.execute(
            "SELECT ordinal, op, ax_role, ax_name, ocr_text FROM step WHERE routine_id=? ORDER BY ordinal",
            (r["id"],)).fetchall()
        out = call_llm(steps, r["app"], r["occurrences"])
        if not out:
            failed += 1
            continue
        delegable = bool(out.get("is_delegable_task", True)) and out.get("name")
        if delegable:
            # WRITE ONLY Tier-2 columns on routine
            con.execute("UPDATE routine SET name=?, description=?, confidence=? WHERE id=?",
                        (str(out.get("name"))[:120], str(out.get("description") or "")[:300],
                         float(out.get("confidence") or 0.5), r["id"]))
            named += 1
            for sl in (out.get("slots") or []):
                try:
                    ordn = int(sl.get("ordinal"))
                except (TypeError, ValueError):
                    continue
                # WRITE ONLY Tier-2 columns on step
                con.execute("UPDATE step SET is_slot=1, slot_name=? WHERE routine_id=? AND ordinal=?",
                            (str(sl.get("slot_name") or "slot")[:60], r["id"], ordn))
                slots_written += con.total_changes and 1 or 0
        else:
            con.execute("UPDATE routine SET name=NULL, description=NULL, confidence=0.0 WHERE id=?",
                        (r["id"],))
            noise += 1
        con.commit()

    con.close()
    print(json.dumps({
        "routine_db": DB, "processed": len(routines),
        "named": named, "flagged_noise": noise, "llm_parse_failures": failed,
        "note": "wrote ONLY routine.name/description/confidence and step.is_slot/slot_name; "
                "deterministic base untouched; local model, data stayed on device.",
    }, indent=2))


if __name__ == "__main__":
    main()
