"""L01 - select replay-SAFE routines and emit a live-run manifest.

This is step 1 of the reserved live money-demo. It compiles your recurring
routines, then filters to ones that are SAFE to actually execute (no
irreversible side effects), and writes live/run_manifest.json for L02.

SAFETY: any routine whose steps touch a destructive verb (send, post, publish,
delete, remove, pay, buy, submit, confirm, transfer, archive) is marked UNSAFE
and excluded by default. You still review the manifest before running L02.
Nothing here executes anything - it only selects and writes a plan.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from _lib import run_script  # noqa: E402

DESTRUCTIVE = ("send", "post", "publish", "delete", "remove", "pay", "buy",
               "submit", "confirm", "transfer", "archive", "trash", "discard",
               "checkout", "purchase", "reply", "tweet", "share")
MANIFEST = os.path.join(os.path.dirname(__file__), "run_manifest.json")


def is_safe(plan):
    for a in plan:
        blob = (str(a.get("target", "")) + " " + str(a.get("op", ""))).lower()
        if any(d in blob for d in DESTRUCTIVE):
            return False, f"touches destructive verb in: {blob.strip()[:40]}"
    return True, "navigation/compose-only; no irreversible verb detected"


def main():
    d = run_script("compile_replay.py")
    safe, unsafe = [], []
    for i, p in enumerate(d["plans"]):
        ok, why = is_safe(p["plan"])
        rec = {"routine_id": f"R{i:02d}", "signature": p["signature"],
               "steps": p["steps"], "occurrences": p["occurrences"],
               "guard_coverage": p["guard_coverage"], "plan": p["plan"],
               "safety": why}
        (safe if ok else unsafe).append(rec)
    safe.sort(key=lambda r: -r["occurrences"])
    manifest = {
        "generated_from": "compile_replay.py",
        "pricing_note": "score with L03 using current Anthropic pricing",
        "n_safe": len(safe), "n_excluded_unsafe": len(unsafe),
        "REVIEW_BEFORE_RUNNING": "Confirm each selected routine is reversible / uses a sandbox "
                                 "or draft target. Never run a routine that sends, posts, or pays.",
        "selected_routines": safe[:10],
        "excluded_unsafe": [{"signature": u["signature"], "reason": u["safety"]} for u in unsafe],
    }
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    print(f"[L01] {len(safe)} safe routines selected, {len(unsafe)} excluded as unsafe.")
    print(f"[L01] wrote {MANIFEST}")
    print("[L01] REVIEW the manifest, trim to 6-10 routines you want to bench, then run L02.")


if __name__ == "__main__":
    main()
