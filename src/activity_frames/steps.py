"""One-shot step drill-down: expand an activity frame into the ordered
click-by-click script it was compiled from.

`get_activity`'s frames are the index (what happened, when, where); each
frame's ``evidence.frame_ids`` range anchors it back to the raw capture.
:func:`steps_for_frame` re-opens that window and returns the replay view -
ordered clicks (element name, role, AXIdentifier, URL), typed runs, pastes,
and focus changes - so an agent can repeat a demonstrated task instead of
re-deriving it from pixels.

This is the ONE-SHOT path: the script of a single demonstrated run.
Consolidating repeats into named, slotted, guarded routines is the routine
layer (see ``research/``), which builds on the same evidence.

Labels use a resolution chain: the event's own ``element_name``; else a
point-in-rect hit-test of the click against the linked frame's accessibility
elements (smallest labeled element wins); else the window title. Typed text
comes from local capture (the recorder refuses secure-input contexts) and is
excluded by default. Pass ``include_text=True`` to include capped text; the
default serves lengths only.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ._time import fmt_local_hms, parse_epoch
from .db import Database

# Common display sizes in points, used to normalize a click's global pixel
# position against the frame's 0-1 element bounds. The capture layer does not
# record display geometry yet; trying the common candidates resolves the
# overwhelming share in practice (single-display setups are one of these).
_SCREEN_CANDIDATES = (
    (1728.0, 1117.0),
    (2560.0, 1440.0),
    (1920.0, 1080.0),
    (1512.0, 982.0),
)

_STEP_EVENTS = ("click", "text", "clipboard", "app_switch")


def _columns(db: Database, table: str) -> set[str]:
    return {r[1] for r in db.rows(f"PRAGMA table_info({table})")}


def _utc_str(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _window_from_evidence(db: Database, evidence: dict, pad_s: float = 2.0):
    rng = str((evidence or {}).get("frame_ids") or "")
    if not rng:
        return None
    lo, _, hi = rng.partition("..")
    hi = hi or lo
    try:
        lo_i, hi_i = int(lo), int(hi)
    except ValueError:
        return None
    row = db.rows(
        "SELECT MIN(timestamp), MAX(timestamp) FROM frames WHERE id BETWEEN ? AND ?",
        (lo_i, hi_i),
    )
    if not row or not row[0][0]:
        return None
    return (
        _utc_str(parse_epoch(row[0][0]) - pad_s),
        _utc_str(parse_epoch(row[0][1]) + pad_s),
    )


class _Resolver:
    """Hit-test unlabeled clicks against the linked frame's element tree."""

    def __init__(self, db: Database):
        self.db = db
        self.has_ref = "elements_ref_frame_id" in _columns(db, "frames")
        self.has_elements = db.table_exists("elements")

    def resolve(self, frame_id: Any, x: Any, y: Any):
        if not self.has_elements or not frame_id or x is None or y is None or y < 0:
            return None, None
        if self.has_ref:
            ref = self.db.scalar(
                "SELECT COALESCE(elements_ref_frame_id, id) FROM frames WHERE id=?",
                (frame_id,),
                default=frame_id,
            )
        else:
            ref = frame_id
        for sw, sh in _SCREEN_CANDIDATES:
            xn, yn = x / sw, y / sh
            if not (0 <= xn <= 1 and 0 <= yn <= 1):
                continue
            rows = self.db.rows(
                "SELECT role, text FROM elements WHERE frame_id=? "
                "AND text IS NOT NULL AND text != '' AND length(text) > 1 "
                "AND left_bound - 0.01 <= ? AND (left_bound + width_bound) + 0.01 >= ? "
                "AND top_bound - 0.01 <= ? AND (top_bound + height_bound) + 0.01 >= ? "
                "ORDER BY (width_bound * height_bound) ASC LIMIT 1",
                (ref, xn, xn, yn, yn),
            )
            if rows:
                return rows[0][0], rows[0][1]
        return None, None


def steps_for_frame(
    db: Database,
    app: str,
    evidence: dict,
    *,
    include_text: bool = False,
    max_steps: int = 250,
    text_cap: int = 80,
) -> dict:
    """The ordered click-by-click script behind one activity frame."""
    window = _window_from_evidence(db, evidence)
    if not window:
        return {"error": "frame has no usable evidence window", "evidence": evidence}
    start, end = window

    cols = _columns(db, "ui_events")
    opt = [
        c if c in cols else f"NULL AS {c}"
        for c in ("x", "y", "element_automation_id", "frame_id")
    ]
    rows = db.rows(
        "SELECT timestamp, event_type, app_name, window_title, browser_url, "
        f"element_name, element_role, text_content, {', '.join(opt)} "
        "FROM ui_events WHERE timestamp >= ? AND timestamp <= ? "
        f"AND event_type IN ({','.join('?' * len(_STEP_EVENTS))}) "
        "ORDER BY timestamp ASC, id ASC",
        (start, end, *_STEP_EVENTS),
    )

    resolver = _Resolver(db)
    steps: list[dict] = []
    unresolved = 0
    last_url = None
    truncated = False
    for (ts, etype, ev_app, win, url, el_name, el_role, text, x, y, auto_id, fid) in rows:
        if len(steps) >= max_steps:
            truncated = True
            break
        if etype != "app_switch" and ev_app and app and ev_app != app:
            continue  # stray events from another app inside the padded window
        t = fmt_local_hms(parse_epoch(ts))
        if etype == "click":
            if (el_role or "") == "":
                continue  # raw mouse-hook duplicate of the labeled click row
            name = (el_name or "").strip()
            role = el_role or ""
            resolved = False
            if not name:
                r_role, r_text = resolver.resolve(fid, x, y)
                if r_text:
                    name, role, resolved = r_text.strip(), r_role or role, True
                else:
                    unresolved += 1
            step: dict[str, Any] = {
                "t": t,
                "op": "click",
                "target": (name or (win or "").strip())[:100],
                "role": role.replace("AX", "") if role else "",
            }
            if resolved:
                step["resolved"] = True
            if auto_id:
                step["automation_id"] = str(auto_id)[:60]
            if url and url != last_url:
                step["url"] = url[:200]
                last_url = url
            steps.append(step)
        elif etype == "text":
            step = {"t": t, "op": "type", "chars": len(text or "")}
            if include_text and text:
                step["text"] = text[:text_cap]
            steps.append(step)
        elif etype == "clipboard":
            step = {"t": t, "op": "paste", "chars": len(text or "")}
            if include_text and text:
                step["text"] = text[:text_cap]
            steps.append(step)
        elif etype == "app_switch":
            steps.append(
                {"t": t, "op": "focus", "target": (f"{ev_app or ''} · {win}" if win else ev_app or "")[:100]}
            )

    for n, s in enumerate(steps, 1):
        s["n"] = n

    return {
        "task": {"app": app, "window_utc": [start, end], "source": "one-shot evidence"},
        "steps": steps,
        "step_count": len(steps),
        "unresolved_clicks": unresolved,
        "truncated": truncated,
    }


# ---- query -> frame resolution (deterministic, no model) ---------------------

_STOPWORDS = frozenset(
    "a an and the my our your i me we to of for in on at with get got grab fetch "
    "do redo run task again it this that last latest new recent please "
    "deterministic deterministically replay rerun using use activity frames frame".split()
)

# Small, curated fan-out so everyday task words match the artifacts they produce
# on screen (an "invoice" task navigates billing/purchase/payment surfaces).
_SYNONYMS = {
    "invoice": ("purchase", "purchases", "billing", "payment", "payments",
                "transaction", "transactions", "receipt", "receipts", "order"),
    "receipt": ("invoice", "purchase", "purchases", "billing", "payment",
                "payments", "transaction", "transactions"),
    "bill": ("billing", "invoice", "payment", "payments", "purchase"),
    "email": ("mail", "gmail", "inbox", "compose", "message", "thread"),
    "meeting": ("calendar", "event", "invite", "schedule"),
}


def find_frame(db: Database, doc, query: str, *, max_steps: int = 250) -> dict:
    """Resolve a natural-language task query to the demonstrated frame.

    Deterministic lexical scoring, zero model calls: each query token (plus a
    small synonym fan-out) is matched per frame against step URLs (+3), step
    target texts (+2), and the app name (+1). Also suggests ``from_url`` - the
    earliest step URL matching the query - so callers can slice off pre-task
    wandering. This keeps retrieval out of agent context: the caller gets one
    frame id, not the whole compiled day.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", (query or "").lower())
              if t not in _STOPWORDS]
    if not tokens:
        return {"error": "empty query after stopwords", "query": query}
    groups = []
    for t in tokens:
        syn = _SYNONYMS.get(t) or _SYNONYMS.get(t.rstrip("s")) or ()
        groups.append((t, *syn))
    flat = tuple(v for grp in groups for v in grp)

    groundable_roles = {"Button", "Link", "TextField", "TextArea", "RadioButton",
                        "CheckBox", "MenuItem", "MenuButton", "PopUpButton",
                        "Tab", "Cell", "StaticText"}
    best: dict | None = None
    for fr in doc.frames:
        out = steps_for_frame(db, fr.app, fr.evidence,
                              include_text=False, max_steps=max_steps)
        steps = out.get("steps") or []
        if not steps:
            continue
        hay_url = " ".join(s.get("url", "") for s in steps).lower()
        hay_txt = " ".join(s.get("target") or "" for s in steps).lower()
        hay_app = (fr.app or "").lower()
        # FULL COVERAGE required: every query token group must match somewhere
        # in this frame, else a frame that merely brushes one word of the task
        # could outrank the real demonstration.
        score, covered = 0, True
        for grp in groups:
            if any(v in hay_url for v in grp):
                score += 3
            elif any(v in hay_txt for v in grp):
                score += 2
            elif any(v in hay_app for v in grp):
                score += 1
            else:
                covered = False
                break
        if not covered:
            continue
        from_url = None
        for s in steps:
            u = (s.get("url") or "").lower()
            if u and any(v in u for v in flat):
                path = urlparse(s["url"]).path.strip("/")
                from_url = path or s["url"]
                break
        # a real demonstration = replayable clicks made ON task-matching pages
        # (groundable role or automation id, page URL context matches query)
        cur_url, task_clicks = "", 0
        for s in steps:
            if s.get("url"):
                cur_url = s["url"].lower()
            t = (s.get("target") or "").strip()
            if (s.get("op") == "click"
                    and any(v in cur_url for v in flat)
                    and (s.get("role") in groundable_roles or s.get("automation_id"))
                    and t and "\n" not in t and len(t) <= 80
                    and t.lower().strip(".…") != "loading"):
                task_clicks += 1
        if task_clicks < 1:
            continue
        cand = {
            "frame": f"f-{fr.index:04d}",
            "app": fr.app,
            "score": score,
            "task_clicks": task_clicks,
            "from_url": from_url,
            "window_utc": out.get("task", {}).get("window_utc"),
            "step_count": out.get("step_count"),
        }
        # among full-coverage demonstrations, RECENCY wins: "replay my X run"
        # means the latest time the user actually did X
        if best is None or fr.index > best["_index"]:
            cand["_index"] = fr.index
            best = cand
    if best is None:
        return {
            "error": "no demonstrated frame matched the query",
            "query": query,
            "tokens": tokens,
            "hint": "widen --hours, or check `aframes context` for what was captured",
        }
    best.pop("_index", None)
    best["query"] = query
    return best
