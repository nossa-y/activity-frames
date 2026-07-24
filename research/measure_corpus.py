"""Instrument card + temporal-holdout h (the one predictive claim).

INSTRUMENT CARD - capture is NOT free; quantify it honestly:
  DB bytes, frames/active-day, % frames carrying an accessibility tree
  (the replay-grounding ceiling), % carrying OCR text (OCR duty), capture
  triggers, ui_event mix.

TEMPORAL HOLDOUT - de-circularize h: mine routine signatures from the FIRST
40 active days only, then measure on the LATER days the fraction of action
steps that fall inside a routine whose signature was already seen in the
training window. That out-of-sample predicted-hit rate - not in-sample
recurrence - is what the economics is allowed to use.

Read-only. Point AFRAMES_CORPUS at a COPY of a capture DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from datetime import datetime

import sys
sys.path.insert(0, os.path.dirname(__file__))
from measure_overhead import _canon_click, is_specific, MIN_FREQUENCY, MIN_STEPS, MAX_STEPS, SESSION_GAP_S  # noqa: E402

DB = os.environ.get("AFRAMES_CORPUS", "/tmp/corpus_ro.sqlite")


def _conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def instrument_card(conn):
    row = conn.execute("SELECT COUNT(*) n, MIN(date(timestamp)) a, MAX(date(timestamp)) b FROM frames").fetchone()
    frames, first, last = row["n"], row["a"], row["b"]
    active_days = conn.execute(
        "SELECT COUNT(*) FROM (SELECT date(timestamp) d FROM frames "
        "WHERE app_name IS NOT NULL GROUP BY d HAVING COUNT(*)>=200)").fetchone()[0]
    ax = conn.execute(
        "SELECT SUM(CASE WHEN accessibility_tree_json IS NOT NULL AND accessibility_tree_json!='' "
        "THEN 1 ELSE 0 END) a, COUNT(*) n FROM frames").fetchone()
    ocr = conn.execute(
        "SELECT SUM(CASE WHEN full_text IS NOT NULL AND full_text!='' THEN 1 ELSE 0 END) a, "
        "COUNT(*) n FROM frames").fetchone()
    triggers = {r["capture_trigger"] or "null": r["n"] for r in conn.execute(
        "SELECT capture_trigger, COUNT(*) n FROM frames GROUP BY capture_trigger")}
    evmix = {r["event_type"]: r["n"] for r in conn.execute(
        "SELECT event_type, COUNT(*) n FROM ui_events GROUP BY event_type ORDER BY n DESC")}
    db_bytes = os.path.getsize(DB)
    return {
        "db_bytes": db_bytes,
        "db_gb": round(db_bytes / 1e9, 2),
        "gb_per_active_day": round(db_bytes / 1e9 / max(active_days, 1), 3),
        "frames": frames, "first_day": first, "last_day": last,
        "active_days": active_days,
        "frames_per_active_day": round(frames / max(active_days, 1)),
        "pct_frames_with_accessibility_tree": round(100 * ax["a"] / max(ax["n"], 1), 1),
        "pct_frames_with_ocr_text": round(100 * ocr["a"] / max(ocr["n"], 1), 1),
        "capture_triggers": triggers,
        "ui_event_mix": evmix,
        "note": "capture runs continuously: on-device OCR duty ~= pct_frames_with_ocr_text; "
                "replay grounding is bounded by pct_frames_with_accessibility_tree.",
    }


def _sessions_in(conn, day_lo, day_hi):
    """Action-step sessions whose date is in [day_lo, day_hi]."""
    rows = conn.execute(
        """SELECT timestamp, event_type, element_name, element_role FROM ui_events
           WHERE event_type IN ('click','text') AND date(timestamp) BETWEEN ? AND ?
           ORDER BY timestamp ASC""", (day_lo, day_hi)).fetchall()
    def ts(s):
        s = (s or "").replace("Z", "").replace("T", " ")[:19]
        try: return datetime.fromisoformat(s)
        except Exception: return None
    def step_of(r):
        return ("type:" + (r["element_name"] or "field")[:24] if r["event_type"] == "text"
                else _canon_click(r["element_name"], r["element_role"]))
    sessions, cur, prev = [], [], None
    for r in rows:
        t = ts(r["timestamp"])
        if t is None: continue
        if prev is not None and (t - prev).total_seconds() > SESSION_GAP_S:
            if cur: sessions.append(cur)
            cur = []
        s = step_of(r)
        if s and (not cur or cur[-1] != s):
            cur.append(s)
        prev = t
    if cur: sessions.append(cur)
    return sessions


def temporal_holdout(conn, train_days=40):
    active = [r["d"] for r in conn.execute(
        "SELECT date(timestamp) d FROM frames WHERE app_name IS NOT NULL "
        "GROUP BY d HAVING COUNT(*)>=200 ORDER BY d")]
    if len(active) < train_days + 5:
        train_days = int(len(active) * 0.66)
    train = active[:train_days]
    test = active[train_days:]

    # mine specific routine signatures from TRAIN
    train_sessions = _sessions_in(conn, train[0], train[-1])
    counts = defaultdict(int)
    for sess in train_sessions:
        L = len(sess)
        for n in range(MIN_STEPS, min(MAX_STEPS, L) + 1):
            for i in range(L - n + 1):
                counts[tuple(sess[i:i + n])] += 1
    train_sigs = {g for g, c in counts.items()
                  if c >= MIN_FREQUENCY and sum(is_specific(s) for s in g) >= 2}

    # in-sample h on train
    def covered_fraction(sessions, sigset):
        tot = cov = 0
        for sess in sessions:
            L = len(sess); tot += L; mark = [False] * L
            for i in range(L):
                for n in range(MIN_STEPS, min(MAX_STEPS, L - i) + 1):
                    if tuple(sess[i:i + n]) in sigset:
                        for j in range(i, i + n): mark[j] = True
            cov += sum(mark)
        return cov, tot
    train_cov, train_tot = covered_fraction(train_sessions, train_sigs)

    # out-of-sample predicted-hit on TEST using TRAIN signatures
    test_sessions = _sessions_in(conn, test[0], test[-1]) if test else []
    test_cov, test_tot = covered_fraction(test_sessions, train_sigs)

    return {
        "train_days": len(train), "test_days": len(test),
        "n_train_routine_signatures": len(train_sigs),
        "h_in_sample_train": round(train_cov / max(train_tot, 1), 4),
        "h_out_of_sample_predicted_on_test": round(test_cov / max(test_tot, 1), 4),
        "interpretation": "out-of-sample predicted-hit rate is the non-circular h the "
                          "economics may use; in-sample is an upper bound.",
    }


if __name__ == "__main__":
    conn = _conn()
    out = {"db": DB,
           "instrument_card": instrument_card(conn),
           "temporal_holdout_h": temporal_holdout(conn)}
    conn.close()
    print(json.dumps(out, indent=2))
