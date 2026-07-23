"""Deterministic replay-plan compiler (LLM-free) - the mechanism that turns a
measured recurring routine into an executable skill.

For each top recurring routine it locates a concrete captured occurrence and
emits a REPLAY PLAN: an ordered list of resolved actions, each carrying a
deterministic GUARD (expected element role/name and/or URL template) taken
straight from the captured accessibility data. On replay an executor performs
the action only if the guard matches the live screen, else it hands control
back to a full agent (the deopt path). No model is involved in compilation.

This is the "buy" side of the ski-rental economics and the denominator of the
Routine Overhead Ratio: the plan's token size is what a replay SENDS instead
of the ~340x larger perceive-reason-act token stream of re-derivation.

We measure B (compile cost) and the plan payload size. We do NOT execute here:
live actuation on real authenticated apps is reserved for the operator
(see bench_three_arm.py). Read-only over a COPY of a screenpipe DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from measure_overhead import (_canon_click, is_specific, MIN_FREQUENCY,  # noqa: E402
                              MIN_STEPS, MAX_STEPS, SESSION_GAP_S, toks)

DB = os.environ.get("AFRAMES_CORPUS", "/tmp/corpus_ro.sqlite")


def _conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _ts(s):
    s = (s or "").replace("Z", "").replace("T", " ")[:19]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def load_action_sessions(conn):
    """Sessions of raw click/text events with their concrete element data."""
    rows = conn.execute(
        """SELECT id, frame_id, timestamp, event_type, element_name, element_role, element_value,
                  element_bounds, app_name, browser_url
           FROM ui_events
           WHERE event_type IN ('click','text')
           ORDER BY timestamp ASC""").fetchall()
    sessions, cur, prev = [], [], None
    for r in rows:
        t = _ts(r["timestamp"])
        if t is None:
            continue
        if prev is not None and (t - prev).total_seconds() > SESSION_GAP_S:
            if cur:
                sessions.append(cur)
            cur = []
        step = ("type:" + (r["element_name"] or "field")[:24] if r["event_type"] == "text"
                else _canon_click(r["element_name"], r["element_role"]))
        if step and (not cur or cur[-1][0] != step):
            cur.append((step, r))
        prev = t
    if cur:
        sessions.append(cur)
    return sessions


def top_routines(sessions, limit=20):
    from collections import defaultdict
    counts = defaultdict(int)
    for sess in sessions:
        steps = [s for s, _ in sess]
        L = len(steps)
        for n in range(MIN_STEPS, min(MAX_STEPS, L) + 1):
            for i in range(L - n + 1):
                counts[tuple(steps[i:i + n])] += 1
    frequent = {g: c for g, c in counts.items()
                if c >= MIN_FREQUENCY and sum(is_specific(s) for s in g) >= 2}
    # maximal
    by_len = sorted(frequent, key=lambda g: -len(g))
    kept = []
    for g in by_len:
        gs = " -> ".join(g)
        if any(gs in " -> ".join(k) and frequent[g] == frequent[k] for k in kept):
            continue
        kept.append(g)
    kept.sort(key=lambda g: -frequent[g] * len(g))
    return [(g, frequent[g]) for g in kept[:limit]]


def find_occurrence(sessions, sig):
    """Return the raw events of the first session window matching sig exactly."""
    n = len(sig)
    for sess in sessions:
        steps = [s for s, _ in sess]
        for i in range(len(steps) - n + 1):
            if tuple(steps[i:i + n]) == sig:
                return [row for _, row in sess[i:i + n]]
    return None


def compile_plan(sig, occurrence):
    """Emit a deterministic replay plan with per-step guards from captured data."""
    plan = []
    for step, row in zip(sig, occurrence):
        if step.startswith("type:"):
            action = {"op": "type", "target": (row["element_name"] or "field"),
                      "role": row["element_role"] or "",
                      "guard": {"expect_role": row["element_role"] or "",
                                "expect_app": row["app_name"] or ""}}
            if row["browser_url"]:
                action["guard"]["expect_url_template"] = _url_t(row["browser_url"])
        else:
            action = {"op": "click", "target": (row["element_name"] or "").strip()[:60],
                      "role": row["element_role"] or "",
                      "guard": {"expect_element": (row["element_name"] or "").strip()[:60],
                                "expect_role": row["element_role"] or "",
                                "expect_app": row["app_name"] or ""}}
            if row["browser_url"]:
                action["guard"]["expect_url_template"] = _url_t(row["browser_url"])
        plan.append(action)
    return plan


def _url_t(url):
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
    except ValueError:
        return ""
    parts = [p for p in u.path.split("/") if p]
    host = u.hostname or ""
    if len(parts) >= 2:
        return f"{host}/{parts[0]}/{parts[1]}/*"
    if len(parts) == 1:
        return f"{host}/{parts[0]}/*"
    return host


def main():
    conn = _conn()
    t0 = time.perf_counter()
    sessions = load_action_sessions(conn)
    routines = top_routines(sessions)
    compile_ms_total = 0.0
    plans = []
    for sig, occ_count in routines:
        b0 = time.perf_counter()
        occ = find_occurrence(sessions, sig)
        if occ is None:
            continue
        plan = compile_plan(sig, occ)
        b_ms = (time.perf_counter() - b0) * 1000
        compile_ms_total += b_ms
        payload = json.dumps(plan, ensure_ascii=False)
        # guard coverage: fraction of steps whose guard has a concrete element or url
        grounded = sum(1 for a in plan
                       if a["guard"].get("expect_element") or a["guard"].get("expect_url_template"))
        plans.append({
            "signature": " -> ".join(sig),
            "steps": len(sig),
            "occurrences": occ_count,
            "compile_ms_B": round(b_ms, 2),
            "plan_tokens": toks(payload),
            "guard_grounded_steps": grounded,
            "guard_coverage": round(grounded / max(len(sig), 1), 2),
            "plan": plan,
        })
    conn.close()
    plans.sort(key=lambda p: -p["occurrences"])
    # Per-step re-derivation cost (typical 1512x982 screenshot + ctx + reason).
    PER_STEP = 1512 * 982 // 750 + 350 + 180  # = 2509 tokens/step
    import statistics as st
    for p in plans:
        num = p["steps"] * PER_STEP
        p["rederivation_tokens"] = num
        # THREE honest denominators, all reported (never cherry-picked):
        p["R_inject_guarded_plan"] = round(num / max(p["plan_tokens"], 1), 1)  # realistic
    Rinj = [p["R_inject_guarded_plan"] for p in plans]
    out = {
        "db": DB,
        "per_step_rederivation_tokens": PER_STEP,
        "n_plans_compiled": len(plans),
        "total_compile_ms_B": round(compile_ms_total, 1),
        "median_compile_ms_B": round(sorted(p["compile_ms_B"] for p in plans)[len(plans) // 2], 2) if plans else None,
        "median_plan_tokens": sorted(p["plan_tokens"] for p in plans)[len(plans) // 2] if plans else None,
        "median_guard_coverage": round(sorted(p["guard_coverage"] for p in plans)[len(plans) // 2], 2) if plans else None,
        "R_inject_guarded_plan_median": round(st.median(Rinj), 1) if Rinj else None,
        "R_inject_guarded_plan_p25_p75": [round(sorted(Rinj)[len(Rinj)//4],1), round(sorted(Rinj)[3*len(Rinj)//4],1)] if Rinj else None,
        "denominator_ladder": {
            "R_info_min_action_script": "~343x (measure_overhead.py) - info-content CEILING, ~8 tok/step",
            "R_inject_guarded_plan": "this file - REALISTIC: inject the full guarded skill (~60 tok/step)",
            "on_hit_local_replay": "LLM out of the loop on a guard match => ~0 tokens sent, ~99% saving; "
                                   "bounded by guard coverage and drift (reserved for live bench)",
        },
        "note": "B is CPU-only and 0 tokens - no model in compilation. Report the conservative "
                "R_inject as the operational headline; R_info is the information-content ceiling.",
        "plans": plans,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
