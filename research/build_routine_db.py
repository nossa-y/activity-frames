"""Deterministically build the routine DB from the capture DB.

Per ROUTINE-DB-DESIGN.md: this is the ~95%-deterministic layer. It mines the
frequent recurring action SEQUENCES (routines), attaches each step's multi-modal
fingerprint, and stores routines with evidence POINTERS back to the raw capture.
It writes NOTHING to the Tier-2 (LLM) columns (name, description, confidence,
is_slot, slot_name) - a later idle-time semantic pass fills those.

ZERO model/network calls. Capture DB opened read-only. Routine DB is a separate file.

  AFRAMES_CORPUS=/tmp/corpus_ro.sqlite python3 build_routine_db.py [out.db]

Sourcing of the fingerprint (deterministic, from the capture):
  ax_role, ax_name  <- the ui_event's resolved clicked element (element_role/element_name);
                       the recorder resolved the element under the cursor at click time, so this
                       is the ground-truth AX identity of what was acted on.
  bbox              <- the ui_event's element_bounds (captured pixel bounds of that element).
  ocr_text          <- elements(source='ocr') for the step's frame (resolving the capture DB's
                       elements_ref_frame_id dedup), text-matched to ax_name. This is the
                       recorded AX<->OCR correspondence: the on-screen text of the acted element.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from compile_replay import (load_action_sessions, top_routines,  # noqa: E402
                            find_occurrence, _url_t)

DB = os.environ.get("AFRAMES_CORPUS", os.environ.get("AFRAMES_DB", "/tmp/corpus_ro.sqlite"))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "routines.db")
SCHEMA = os.path.join(os.path.dirname(__file__), "routine_db_schema.sql")


def cap_conn():
    # READ-ONLY connection to the capture DB (never written).
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _norm(s):
    return (s or "").replace("‎", "").strip().lower()


def _parse_ts(s):
    s = (s or "").replace("T", " ").replace("Z", "")[:19]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def resolve_frame(cap, ts_str, app_name):
    """This recorder does NOT populate ui_events.frame_id (it is NULL for every
    event). Events and frames are separate streams correlated by TIMESTAMP. So we
    resolve the frame shown at the moment of the action: the nearest frame at or
    just before the event (same app when possible), within a 15s window. Returns
    (frame_id, elements_frame) where elements_frame resolves elements_ref_frame_id."""
    t = _parse_ts(ts_str)
    if t is None:
        return None, None
    lo = (t - timedelta(seconds=15)).isoformat(sep=" ")
    for cond, params in (
        ("timestamp<=? AND timestamp>=? AND app_name=?", (ts_str, lo, app_name)),
        ("timestamp<=? AND timestamp>=?", (ts_str, lo)),
    ):
        r = cap.execute(
            f"SELECT id, COALESCE(elements_ref_frame_id, id) AS ef, browser_url FROM frames "
            f"WHERE {cond} ORDER BY timestamp DESC LIMIT 1", params).fetchone()
        if r:
            return r["id"], r["ef"], r["browser_url"]
    return None, None, None


def ef_of(cap, frame_id):
    """Resolve a frame_id to the frame that actually holds its elements (dedup)."""
    r = cap.execute("SELECT COALESCE(elements_ref_frame_id, id) AS ef FROM frames WHERE id=?",
                    (frame_id,)).fetchone()
    return r["ef"] if r else frame_id


def ocr_text_for(cap, ef, ax_name):
    """The OCR text on the resolved frame that corresponds to the acted element.
    Returns a real elements(source='ocr').text value or None - never fabricated."""
    if ef is None or not ax_name:
        return None
    axn = _norm(ax_name)
    if not axn:
        return None
    rows = cap.execute(
        "SELECT text FROM elements WHERE frame_id=? AND source='ocr' AND text IS NOT NULL AND text!=''",
        (ef,)).fetchall()
    for row in rows:                       # exact accessible-name match
        if _norm(row["text"]) == axn:
            return row["text"]
    for row in rows:                       # AX name appears within an OCR line
        if axn in _norm(row["text"]):
            return row["text"]
    for row in rows:                       # an OCR line (>=3 chars) is the element's visible text
        ot = _norm(row["text"])
        if len(ot) >= 3 and ot in axn:
            return row["text"]
    return None


def _col(row, name):
    """Safe column access (the source query may not select every column)."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


def resolve_click_element(cap, ef, x, y):
    """Frame-level click grounding via the recorder's proven rich AX element tree.
    When a click's ui_event carries no resolved element_name, find the element in the
    frame whose normalized rect contains the click (x, y), preferring the SMALLEST-area
    NAMED one (most specific). Returns (role, text) or (None, None).

    Coords are normalized 0..1 - the same space as elements.*_bound (screen fractions).
    A pixel-space click would have to be normalized by the frame first; we only trust
    already-normalized coords here (mismatched units silently no-op rather than mis-hit)."""
    if ef is None or x is None or y is None:
        return None, None
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return None, None
    if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
        return None, None
    rows = cap.execute(
        "SELECT role, text, left_bound l, top_bound t, width_bound w, height_bound h "
        "FROM elements WHERE frame_id=? AND left_bound IS NOT NULL "
        "AND text IS NOT NULL AND text!=''", (ef,)).fetchall()
    best, best_area = None, 2.0
    for r in rows:
        l, t, w, h = r["l"], r["t"], r["w"], r["h"]
        if None in (l, t, w, h):
            continue
        if w * h > 0.5:                 # skip full-window/background containers - not a click target
            continue
        if l <= x <= l + w and t <= y <= t + h and w * h < best_area:
            best, best_area = r, w * h
    return (best["role"], best["text"]) if best else (None, None)


def op_of(row):
    return "type" if row["event_type"] == "text" else "click"


def occurrence_spans(sessions, sig):
    """Timestamps of the first step of every occurrence of sig, for first/last_seen."""
    n = len(sig)
    times = []
    for sess in sessions:
        steps = [s for s, _ in sess]
        for i in range(len(steps) - n + 1):
            if tuple(steps[i:i + n]) == sig:
                times.append(sess[i][1]["timestamp"])
    return times


def build():
    cap = cap_conn()
    sessions = load_action_sessions(cap)
    routines = top_routines(sessions, limit=500)      # top 500 by yield (occurrences x steps)

    if os.path.exists(OUT):
        os.remove(OUT)
    out = sqlite3.connect(OUT)
    out.executescript(open(SCHEMA).read())

    n_routines = n_steps = n_evidence = n_ocr = n_click_grounded = 0
    for rid, (sig, occ) in enumerate(routines, start=1):
        occ_rows = find_occurrence(sessions, sig)     # representative concrete rows
        if occ_rows is None or len(occ_rows) != len(sig):
            continue
        times = occurrence_spans(sessions, sig)
        app = occ_rows[0]["app_name"]
        # site: ui_events.browser_url is NULL in the old capture; resolve the URL from
        # the first step's temporal frame (frames.browser_url). New recorder sets it on
        # the event directly, so prefer that when present.
        site = _url_t(occ_rows[0]["browser_url"]) if occ_rows[0]["browser_url"] else None
        if not site:
            _, _, burl0 = resolve_frame(cap, occ_rows[0]["timestamp"], occ_rows[0]["app_name"])
            site = _url_t(burl0) if burl0 else None

        out.execute(
            # NOTE: name/description/confidence intentionally omitted -> stay NULL (Tier-2)
            "INSERT INTO routine (id, app, site, step_count, occurrences, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?)",
            (rid, app, site, len(sig), occ, (min(times) if times else None),
             (max(times) if times else None)))
        n_routines += 1

        for ordinal, row in enumerate(occ_rows):
            ax_role = row["element_role"]
            ax_name = row["element_name"]
            bbox = row["element_bounds"]
            # New recorder sets ui_events.frame_id as an EXACT FK; use it. Old capture
            # leaves it NULL -> fall back to the 15s temporal resolution.
            if row["frame_id"] is not None:
                frame_id, ef = row["frame_id"], ef_of(cap, row["frame_id"])
            else:
                frame_id, ef, _ = resolve_frame(cap, row["timestamp"], row["app_name"])
            # Frame-level click grounding: if the click has no resolved element name,
            # recover it from the frame's rich AX tree by point-in-rect (the recorder's
            # proven 99.6%-named tree). Keeps routines grounded even while the click path
            # of ui_events.element_name lags.
            if not (ax_name and str(ax_name).strip()):
                fr, fn = resolve_click_element(cap, ef, _col(row, "x"), _col(row, "y"))
                if fn:
                    ax_role, ax_name = (ax_role or fr), fn
                    n_click_grounded += 1
            ocr = ocr_text_for(cap, ef, ax_name)
            if ocr:
                n_ocr += 1
            out.execute(
                # is_slot/slot_name intentionally omitted -> stay NULL (Tier-2)
                "INSERT INTO step (routine_id, ordinal, op, ax_role, ax_name, ocr_text, bbox) "
                "VALUES (?,?,?,?,?,?,?)",
                (rid, ordinal, op_of(row), ax_role, ax_name, ocr, bbox))
            n_steps += 1
            out.execute(
                "INSERT INTO evidence (routine_id, step_ordinal, frame_id, ui_event_id) "
                "VALUES (?,?,?,?)",
                (rid, ordinal, frame_id, row["id"]))       # frame_id resolved by timestamp
            n_evidence += 1

    out.commit()
    out.close()
    cap.close()
    print(json.dumps({
        "capture_db": DB, "routine_db": OUT,
        "routines": n_routines, "steps": n_steps, "evidence_rows": n_evidence,
        "steps_with_ocr_fingerprint": n_ocr,
        "steps_click_grounded_via_tree": n_click_grounded,
        "tier2_columns_written": "none (name/description/confidence/is_slot/slot_name left NULL)",
    }, indent=2))


if __name__ == "__main__":
    build()
