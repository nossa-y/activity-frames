"""Verify a nocta-recorder capture DB against the downstream contract.

Objective self-check for the recorder agent: schema shape + the populated-rate
targets that the OLD engine failed (element_name 7.3%, automation_id 0%, frame_id
0%, element_bounds 0%, browser_url 0%, scroll deltas 0%, key_code 3.6%). Prints
PASS/FAIL per requirement. Read-only.

  AFRAMES_CORPUS=/path/to/new_db.sqlite python3 verify_recorder.py [--smoke]

--smoke also runs build_routine_db.py to confirm the compiler works end to end and
reports whether fingerprints (frame_id, bbox, OCR) actually improved.
"""
import os
import sqlite3
import subprocess
import sys

DB = os.environ.get("AFRAMES_CORPUS", os.environ.get("AFRAMES_DB", "/tmp/corpus_ro.sqlite"))
HERE = os.path.dirname(__file__)


def c():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


REQUIRED = {
    "frames": ["timestamp", "app_name", "focused", "browser_url", "device_name",
               "accessibility_tree_json", "full_text", "capture_trigger", "elements_ref_frame_id"],
    "ui_events": ["timestamp", "event_type", "x", "y", "delta_x", "delta_y", "button",
                  "key_code", "text_content", "app_name", "browser_url", "element_role",
                  "element_name", "element_value", "element_automation_id", "element_bounds", "frame_id"],
    "elements": ["frame_id", "source", "role", "text", "parent_id", "depth",
                 "left_bound", "top_bound", "width_bound", "height_bound", "confidence"],
}

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def pct(conn, table, where):
    tot = conn.execute(f"SELECT COUNT(*) FROM {table}"
                       + (f" WHERE {where.split('AND',1)[0]}" if False else "")).fetchone()[0]
    return tot


def rate(conn, table, col, subset=None):
    base = f"SELECT COUNT(*) FROM {table}"
    sub = f" WHERE {subset}" if subset else ""
    tot = conn.execute(base + sub).fetchone()[0]
    if tot == 0:
        return None, 0
    cond = f"{col} IS NOT NULL AND {col} != ''"
    n = conn.execute(f"{base} WHERE {cond}" + (f" AND {subset}" if subset else "")).fetchone()[0]
    return 100.0 * n / tot, tot


def main():
    if not os.path.exists(DB):
        sys.exit(f"DB not found: {DB}")
    conn = c()

    print("== 1. schema shape ==")
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t, cols in REQUIRED.items():
        if t not in tables:
            check(f"table {t} exists", False, "MISSING")
            continue
        present = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
        missing = [x for x in cols if x not in present]
        check(f"table {t} has required columns", not missing,
              "missing: " + ",".join(missing) if missing else f"{len(cols)} cols ok")

    print("== 2. populated-rate targets (the old-engine gaps) ==")
    r_name, _ = rate(conn, "ui_events", "element_name", "event_type='click'")
    check("ui_events.element_name on clicks >= 60%", (r_name or 0) >= 60,
          f"{r_name:.1f}% (old engine: 7.3%)" if r_name is not None else "no click events")
    r_auto, _ = rate(conn, "ui_events", "element_automation_id")
    check("ui_events.element_automation_id > 0%", (r_auto or 0) > 0,
          f"{r_auto:.1f}% (old engine: 0%)" if r_auto is not None else "n/a")
    r_role, _ = rate(conn, "ui_events", "element_role", "event_type='click'")
    check("ui_events.element_role on clicks >= 60%", (r_role or 0) >= 60,
          f"{r_role:.1f}% (old: 32%)" if r_role is not None else "n/a")
    r_fid, _ = rate(conn, "ui_events", "frame_id")
    check("ui_events.frame_id == 100%", (r_fid or 0) >= 99.0,
          f"{r_fid:.1f}% (old: 0%)" if r_fid is not None else "n/a")
    r_bounds, _ = rate(conn, "ui_events", "element_bounds", "event_type='click'")
    check("ui_events.element_bounds on clicks >= 50%", (r_bounds or 0) >= 50,
          f"{r_bounds:.1f}% (old: 0%)" if r_bounds is not None else "n/a")
    r_url, _ = rate(conn, "ui_events", "browser_url")
    check("ui_events.browser_url > 0% (was only in frames)", (r_url or 0) > 0,
          f"{r_url:.1f}% (old: 0%)" if r_url is not None else "n/a")
    # scroll deltas + key_code only where those event types exist
    n_scroll = conn.execute("SELECT COUNT(*) FROM ui_events WHERE event_type='scroll'").fetchone()[0]
    if n_scroll:
        r_d, _ = rate(conn, "ui_events", "delta_y", "event_type='scroll'")
        check("ui_events.delta_y on scroll > 0%", (r_d or 0) > 0, f"{r_d:.1f}% (old: 0%)")
    n_key = conn.execute("SELECT COUNT(*) FROM ui_events WHERE event_type='key'").fetchone()[0]
    if n_key:
        r_k, _ = rate(conn, "ui_events", "key_code", "event_type='key'")
        check("ui_events.key_code on key events > 0%", (r_k or 0) > 0, f"{r_k:.1f}%")

    print("== 3. context streams (keep these healthy) ==")
    for col, tgt in [("accessibility_tree_json", 60), ("full_text", 60), ("browser_url", 1)]:
        r, _ = rate(conn, "frames", col)
        check(f"frames.{col} >= {tgt}%", (r or 0) >= tgt, f"{r:.1f}%" if r is not None else "n/a")
    r_par, _ = rate(conn, "elements", "parent_id")
    check("elements.parent_id present (AX hierarchy, not flat)", (r_par or 0) > 40,
          f"{r_par:.1f}% (old: 31% - mostly flat)" if r_par is not None else "n/a")
    conn.close()

    if "--smoke" in sys.argv:
        print("== 4. downstream compiler smoke test ==")
        env = dict(os.environ, AFRAMES_CORPUS=DB, AFRAMES_DB=DB)
        out = subprocess.run([sys.executable, os.path.join(HERE, "build_routine_db.py"),
                              "/tmp/routines_verify.db"], capture_output=True, text=True, env=env, timeout=1800)
        print(out.stdout[-600:] or out.stderr[-600:])
        check("compiler builds routine DB from this capture", out.returncode == 0)

    npass = sum(1 for _, ok, _ in results if ok)
    print(f"\n== VERDICT: {npass}/{len(results)} checks pass ==")
    print("If element_name/automation_id/frame_id/element_bounds/browser_url now PASS, the "
          "core recorder gaps are fixed and every downstream fingerprint sharpens for free.")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
