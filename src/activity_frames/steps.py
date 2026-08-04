"""One-shot step drill-down: expand an activity frame into the ordered
click-by-click script it was compiled from.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from ._time import fmt_local_hms, parse_epoch
from .db import Database


_SCREEN_CANDIDATES = (
    (1728.0, 1117.0),
    (2560.0, 1440.0),
    (1920.0, 1080.0),
    (1512.0, 982.0),
)

_STEP_EVENTS = ("click", "text", "clipboard", "app_switch")


def _columns(db: Database, table: str) -> set[str]:
    """Return column names for a SQLite table."""
    if not db.table_exists(table):
        return set()

    return {r[1] for r in db.rows(f"PRAGMA table_info({table})")}


def _utc_str(epoch: float) -> str:
    """Convert Unix epoch seconds to UTC ISO timestamp."""
    return datetime.fromtimestamp(
        epoch,
        tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%S")


def _timestamp_to_epoch(value: Any) -> float:
    """
    Convert a database timestamp to Unix epoch seconds.

    Supports:
        Unix epoch numbers
        Numeric strings
        YYYY-MM-DD HH:MM:SS
        YYYY-MM-DD HH:MM:SS.ffffff
        YYYY-MM-DDTHH:MM:SS
        ISO timestamps
        ISO timestamps ending in Z
    """

    if value is None:
        raise ValueError("Timestamp cannot be None")

    # Already numeric
    if isinstance(value, (int, float)):
        return float(value)

    value = str(value).strip()

    if not value:
        raise ValueError("Timestamp is empty")

    # Numeric timestamp stored as text
    try:
        return float(value)
    except ValueError:
        pass

    # ISO Z means UTC
    iso_value = value

    if iso_value.endswith("Z"):
        iso_value = iso_value[:-1] + "+00:00"

    # Python ISO parser
    try:
        dt = datetime.fromisoformat(iso_value)

        # Windows recorder timestamps are local time when they
        # don't contain timezone information.
        if dt.tzinfo is None:
            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.replace(tzinfo=local_tz)

        return dt.timestamp()

    except ValueError:
        pass

    # Additional explicit formats
    formats = (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    )

    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)

            local_tz = datetime.now().astimezone().tzinfo
            dt = dt.replace(tzinfo=local_tz)

            return dt.timestamp()

        except ValueError:
            continue

    # Preserve compatibility with original activity-frames
    return parse_epoch(value)


def _window_from_evidence(
    db: Database,
    evidence: dict,
    pad_s: float = 2.0,
):
    """
    Convert evidence frame IDs into a UTC time window.

    Example:

        frame_ids = "1..13"

    The Windows recorder stores timestamps as local SQLite datetime
    strings, so they are converted safely to epoch time here.
    """

    rng = str(
        (evidence or {}).get("frame_ids") or ""
    ).strip()

    if not rng:
        return None

    if ".." in rng:
        lo, _, hi = rng.partition("..")
    else:
        lo = rng
        hi = rng

    try:
        lo_i = int(lo.strip())
        hi_i = int(hi.strip())
    except (TypeError, ValueError):
        return None

    # Handle reversed evidence ranges
    if lo_i > hi_i:
        lo_i, hi_i = hi_i, lo_i

    row = db.rows(
        """
        SELECT
            MIN(timestamp),
            MAX(timestamp)
        FROM frames
        WHERE id BETWEEN ? AND ?
        """,
        (lo_i, hi_i),
    )

    if not row:
        return None

    if not row[0]:
        return None

    start_value = row[0][0]
    end_value = row[0][1]

    if start_value is None or end_value is None:
        return None

    try:
        start_epoch = _timestamp_to_epoch(start_value)
        end_epoch = _timestamp_to_epoch(end_value)
    except (ValueError, TypeError, OverflowError):
        return None

    return (
        _utc_str(start_epoch - pad_s),
        _utc_str(end_epoch + pad_s),
    )


class _Resolver:
    """Hit-test unlabeled clicks against the linked frame element tree."""

    def __init__(self, db: Database):
        self.db = db

        frame_cols = _columns(db, "frames")

        self.has_ref = (
            "elements_ref_frame_id" in frame_cols
        )

        self.has_elements = db.table_exists("elements")

    def resolve(
        self,
        frame_id: Any,
        x: Any,
        y: Any,
    ):
        if (
            not self.has_elements
            or not frame_id
            or x is None
            or y is None
            or y < 0
        ):
            return None, None

        if self.has_ref:

            ref = self.db.scalar(
                """
                SELECT COALESCE(elements_ref_frame_id, id)
                FROM frames
                WHERE id=?
                """,
                (frame_id,),
                default=frame_id,
            )

        else:
            ref = frame_id

        for sw, sh in _SCREEN_CANDIDATES:

            xn = x / sw
            yn = y / sh

            if not (
                0 <= xn <= 1
                and 0 <= yn <= 1
            ):
                continue

            rows = self.db.rows(
                """
                SELECT
                    role,
                    text
                FROM elements
                WHERE frame_id=?
                  AND text IS NOT NULL
                  AND text != ''
                  AND length(text) > 1
                  AND left_bound - 0.01 <= ?
                  AND (left_bound + width_bound) + 0.01 >= ?
                  AND top_bound - 0.01 <= ?
                  AND (top_bound + height_bound) + 0.01 >= ?
                ORDER BY
                    (width_bound * height_bound) ASC
                LIMIT 1
                """,
                (
                    ref,
                    xn,
                    xn,
                    yn,
                    yn,
                ),
            )

            if rows:
                return rows[0][0], rows[0][1]

        return None, None


def steps_for_frame(
    db: Database,
    app: str,
    evidence: dict,
    *,
    include_text: bool = True,
    max_steps: int = 250,
    text_cap: int = 80,
) -> dict:

    """Return the ordered activity script behind one frame."""

    window = _window_from_evidence(
        db,
        evidence,
    )

    if not window:
        return {
            "error": "frame has no usable evidence window",
            "evidence": evidence,
        }

    start, end = window

    # Your Windows recorder currently records frames only.
    # Therefore ui_events may not exist.
    if not db.table_exists("ui_events"):

        return {
            "task": {
                "app": app,
                "window_utc": [start, end],
                "source": "windows-recorder frames",
            },
            "steps": [],
            "step_count": 0,
            "unresolved_clicks": 0,
            "truncated": False,
            "note": (
                "The Windows recorder currently contains frame/window "
                "activity but no ui_events table."
            ),
        }

    cols = _columns(db, "ui_events")

    required_columns = (
        "timestamp",
        "event_type",
        "app_name",
        "window_title",
        "browser_url",
        "element_name",
        "element_role",
        "text_content",
    )

    # Generate safe SELECT expressions for missing columns
    base_select = []

    for column in required_columns:
        if column in cols:
            base_select.append(column)
        else:
            base_select.append(
                f"NULL AS {column}"
            )

    optional_columns = (
        "x",
        "y",
        "element_automation_id",
        "frame_id",
    )

    opt = [
        c if c in cols
        else f"NULL AS {c}"
        for c in optional_columns
    ]

    rows = db.rows(
        "SELECT "
        + ", ".join(base_select)
        + ", "
        + ", ".join(opt)
        + " FROM ui_events "
        + "WHERE timestamp >= ? "
        + "AND timestamp <= ? "
        + f"AND event_type IN ({','.join('?' * len(_STEP_EVENTS))}) "
        + "ORDER BY id",
        (
            start,
            end,
            *_STEP_EVENTS,
        ),
    )

    resolver = _Resolver(db)

    steps: list[dict] = []

    unresolved = 0
    last_url = None
    truncated = False

    for (
        ts,
        etype,
        ev_app,
        win,
        url,
        el_name,
        el_role,
        text,
        x,
        y,
        auto_id,
        fid,
    ) in rows:

        if len(steps) >= max_steps:
            truncated = True
            break

        if (
            etype != "app_switch"
            and ev_app
            and app
            and ev_app != app
        ):
            continue

        try:
            t = fmt_local_hms(
                _timestamp_to_epoch(ts)
            )
        except Exception:
            t = str(ts)

        if etype == "click":

            if (el_role or "") == "":
                continue

            name = (el_name or "").strip()

            role = el_role or ""

            resolved = False

            if not name:

                r_role, r_text = resolver.resolve(
                    fid,
                    x,
                    y,
                )

                if r_text:

                    name = r_text.strip()

                    role = r_role or role

                    resolved = True

                else:
                    unresolved += 1

            step: dict[str, Any] = {
                "t": t,
                "op": "click",
                "target": (
                    name
                    or (win or "").strip()
                )[:100],
                "role": (
                    role.replace("AX", "")
                    if role
                    else ""
                ),
            }

            if resolved:
                step["resolved"] = True

            if auto_id:
                step["automation_id"] = str(
                    auto_id
                )[:60]

            if url and url != last_url:

                step["url"] = url[:200]

                last_url = url

            steps.append(step)

        elif etype == "text":

            step = {
                "t": t,
                "op": "type",
                "chars": len(text or ""),
            }

            if include_text and text:
                step["text"] = text[:text_cap]

            steps.append(step)

        elif etype == "clipboard":

            step = {
                "t": t,
                "op": "paste",
                "chars": len(text or ""),
            }

            if include_text and text:
                step["text"] = text[:text_cap]

            steps.append(step)

        elif etype == "app_switch":

            steps.append(
                {
                    "t": t,
                    "op": "focus",
                    "target": (
                        f"{ev_app or ''} · {win}"
                        if win
                        else ev_app or ""
                    )[:100],
                }
            )

    for n, step in enumerate(
        steps,
        1,
    ):
        step["n"] = n

    return {
        "task": {
            "app": app,
            "window_utc": [
                start,
                end,
            ],
            "source": "one-shot evidence",
        },
        "steps": steps,
        "step_count": len(steps),
        "unresolved_clicks": unresolved,
        "truncated": truncated,
    }


# ---------------------------------------------------------
# QUERY -> FRAME RESOLUTION
# ---------------------------------------------------------

_STOPWORDS = frozenset(
    """
    a an and the my our your i me we
    to of for in on at with get got
    grab fetch do redo run task again
    it this that last latest new recent
    please deterministic deterministically
    replay rerun using use activity
    frames frame
    """.split()
)


_SYNONYMS = {

    "invoice": (
        "purchase",
        "purchases",
        "billing",
        "payment",
        "payments",
        "transaction",
        "transactions",
        "receipt",
        "receipts",
        "order",
    ),

    "receipt": (
        "invoice",
        "purchase",
        "purchases",
        "billing",
        "payment",
        "payments",
        "transaction",
        "transactions",
    ),

    "bill": (
        "billing",
        "invoice",
        "payment",
        "payments",
        "purchase",
    ),

    "email": (
        "mail",
        "gmail",
        "inbox",
        "compose",
        "message",
        "thread",
    ),

    "meeting": (
        "calendar",
        "event",
        "invite",
        "schedule",
    ),
}


def find_frame(
    db: Database,
    doc,
    query: str,
    *,
    max_steps: int = 250,
) -> dict:

    """Resolve a natural-language query to a demonstrated frame."""

    tokens = [
        t
        for t in re.findall(
            r"[a-z0-9]+",
            (query or "").lower(),
        )
        if t not in _STOPWORDS
    ]

    if not tokens:

        return {
            "error": "empty query after stopwords",
            "query": query,
        }

    groups = []

    for t in tokens:

        syn = (
            _SYNONYMS.get(t)
            or _SYNONYMS.get(
                t.rstrip("s")
            )
            or ()
        )

        groups.append(
            (t, *syn)
        )

    flat = tuple(
        value
        for group in groups
        for value in group
    )

    groundable_roles = {
        "Button",
        "Link",
        "TextField",
        "TextArea",
        "RadioButton",
        "CheckBox",
        "MenuItem",
        "MenuButton",
        "PopUpButton",
        "Tab",
        "Cell",
        "StaticText",
    }

    best: dict | None = None

    for fr in doc.frames:

        out = steps_for_frame(
            db,
            fr.app,
            fr.evidence,
            include_text=False,
            max_steps=max_steps,
        )

        steps = out.get("steps") or []

        if not steps:
            continue

        hay_url = " ".join(
            s.get("url", "")
            for s in steps
        ).lower()

        hay_txt = " ".join(
            s.get("target") or ""
            for s in steps
        ).lower()

        hay_app = (
            fr.app or ""
        ).lower()

        score = 0
        covered = True

        for grp in groups:

            if any(
                value in hay_url
                for value in grp
            ):
                score += 3

            elif any(
                value in hay_txt
                for value in grp
            ):
                score += 2

            elif any(
                value in hay_app
                for value in grp
            ):
                score += 1

            else:
                covered = False
                break

        if not covered:
            continue

        from_url = None

        for step in steps:

            url = (
                step.get("url") or ""
            ).lower()

            if (
                url
                and any(
                    value in url
                    for value in flat
                )
            ):

                path = urlparse(
                    step["url"]
                ).path.strip("/")

                from_url = (
                    path
                    or step["url"]
                )

                break

        cur_url = ""
        task_clicks = 0

        for step in steps:

            if step.get("url"):
                cur_url = step[
                    "url"
                ].lower()

            target = (
                step.get("target")
                or ""
            ).strip()

            if (
                step.get("op") == "click"
                and any(
                    value in cur_url
                    for value in flat
                )
                and (
                    step.get("role")
                    in groundable_roles
                    or step.get(
                        "automation_id"
                    )
                )
                and target
                and "\n" not in target
                and len(target) <= 80
                and target.lower().strip(
                    ".…"
                ) != "loading"
            ):
                task_clicks += 1

        if task_clicks < 1:
            continue

        candidate = {
            "frame": f"f-{fr.index:04d}",
            "app": fr.app,
            "score": score,
            "task_clicks": task_clicks,
            "from_url": from_url,
            "window_utc": out.get(
                "task",
                {},
            ).get("window_utc"),
            "step_count": out.get(
                "step_count"
            ),
        }

        if (
            best is None
            or fr.index > best["_index"]
        ):

            candidate["_index"] = fr.index

            best = candidate

    if best is None:

        return {
            "error": (
                "no demonstrated frame "
                "matched the query"
            ),
            "query": query,
            "tokens": tokens,
            "hint": (
                "widen --hours, or check "
                "`aframes context` for "
                "what was captured"
            ),
        }

    best.pop(
        "_index",
        None,
    )

    best["query"] = query

    return best