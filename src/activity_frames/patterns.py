"""Repetitive-workflow detection (port of Nocta's PatternDetector.swift).

Six deterministic detectors over the recorder DB. Each returns
WorkPattern rows: a machine-readable kind, a human-readable label, and
the observed count. No scoring, no inference; a pattern is reported
only when it actually repeated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit

from ._time import local_day_string, parse_epoch
from .db import Database

MIN_FREQUENCY = 3
_GENERIC_ELEMENTS = {"scroll area", "group", "cell", "text", "text field"}


@dataclass
class WorkPattern:
    kind: str    # repeated_click | url_pattern | action_sequence | app_switch | repeated_text | daily_habit
    label: str
    count: int


def detect(db: Database, start_utc: str, end_utc: str,
           *, include_text: bool = False) -> list[WorkPattern]:
    """Detect repetitive workflows.

    include_text gates the repeated-text detector: its labels quote raw
    typed content, so it is OFF by default (same contract as the rest of
    the package: input volume is standard, input content is opt-in).
    """
    out: list[WorkPattern] = []
    if db.table_exists("ui_events"):
        out += repeated_clicks(db, start_utc, end_utc)
        out += action_sequences(db, start_utc, end_utc)
        if include_text:
            out += repeated_text(db, start_utc, end_utc)
        out += daily_habits(db, start_utc, end_utc)
    out += url_patterns(db, start_utc, end_utc)
    out += app_switching(db, start_utc, end_utc)
    return out


def repeated_clicks(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT element_name, element_role, COUNT(*) as cnt FROM ui_events
        WHERE timestamp BETWEEN ? AND ?
          AND event_type = 'click'
          AND element_name IS NOT NULL AND element_name != ''
          AND element_name NOT IN ('scroll area','group','cell','text','text field')
        GROUP BY element_name, element_role
        HAVING cnt >= ?
        ORDER BY cnt DESC LIMIT 20
        """,
        (start, end, MIN_FREQUENCY),
    )
    return [
        WorkPattern(
            kind="repeated_click",
            label=f"Clicked '{(name or '').strip()}' ({role or ''}) {cnt}x",
            count=int(cnt),
        )
        for name, role, cnt in rows
        if name
    ]


def url_patterns(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT browser_url, COUNT(*) as cnt FROM frames
        WHERE timestamp BETWEEN ? AND ?
          AND focused = 1
          AND browser_url IS NOT NULL AND browser_url != ''
        GROUP BY browser_url ORDER BY cnt DESC LIMIT 2000
        """,
        (start, end),
    )
    groups: dict[str, dict] = {}
    for url, cnt in rows:
        try:
            parts_all = urlsplit(url)
        except ValueError:
            continue
        host = parts_all.hostname
        if not host:
            continue
        parts = [p for p in parts_all.path.split("/") if p]
        if len(parts) >= 2:
            key = f"{host}/{parts[0]}/{parts[1]}/*"
        elif len(parts) == 1:
            key = f"{host}/{parts[0]}/*"
        else:
            key = host
        g = groups.setdefault(key, {"urls": set(), "visits": 0})
        g["urls"].add(url)
        g["visits"] += int(cnt)

    keep = [
        (k, g) for k, g in groups.items()
        if g["visits"] >= MIN_FREQUENCY and (len(g["urls"]) >= 3 or g["visits"] >= 5)
    ]
    keep.sort(key=lambda kg: -kg[1]["visits"])
    return [
        WorkPattern(
            kind="url_pattern",
            label=f"{k} - {g['visits']} visits, {len(g['urls'])} unique pages",
            count=g["visits"],
        )
        for k, g in keep[:15]
    ]


def action_sequences(db: Database, start: str, end: str) -> list[WorkPattern]:
    # Most recent 20k clicks, re-sorted chronologically for sequence mining
    # (a plain ORDER BY ... LIMIT would analyze the OLDEST part of the window).
    rows = db.rows(
        """
        SELECT element_name, element_role FROM (
            SELECT timestamp, element_name, element_role FROM ui_events
            WHERE timestamp BETWEEN ? AND ?
              AND event_type = 'click'
              AND element_name IS NOT NULL AND element_name != ''
            ORDER BY timestamp DESC LIMIT 20000
        ) ORDER BY timestamp ASC
        """,
        (start, end),
    )
    if len(rows) < 4:
        return []
    actions = []
    for name, role in rows:
        name = (name or "").strip()
        actions.append(f"[{role or 'element'}]" if name in _GENERIC_ELEMENTS else name)

    bigrams: dict[str, int] = {}
    trigrams: dict[str, int] = {}
    for i in range(len(actions) - 1):
        a, b = actions[i], actions[i + 1]
        if a == b and a.startswith("["):
            continue
        bigrams[f"{a} -> {b}"] = bigrams.get(f"{a} -> {b}", 0) + 1
    for i in range(len(actions) - 2):
        seq = f"{actions[i]} -> {actions[i + 1]} -> {actions[i + 2]}"
        trigrams[seq] = trigrams.get(seq, 0) + 1

    out: list[WorkPattern] = []
    seen_parts: set[str] = set()
    for seq, cnt in sorted(trigrams.items(), key=lambda kv: -kv[1])[:10]:
        if cnt < MIN_FREQUENCY:
            break
        out.append(WorkPattern(kind="action_sequence", label=f"{seq} ({cnt}x)", count=cnt))
        seen_parts.update(seq.split(" -> "))
    for seq, cnt in sorted(bigrams.items(), key=lambda kv: -kv[1])[:10]:
        if cnt < MIN_FREQUENCY:
            break
        if all(p in seen_parts for p in seq.split(" -> ")):
            continue
        out.append(WorkPattern(kind="action_sequence", label=f"{seq} ({cnt}x)", count=cnt))
    return out[:10]


def app_switching(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT app_name FROM (
            SELECT timestamp, app_name FROM frames
            WHERE timestamp BETWEEN ? AND ?
              AND focused = 1
              AND app_name IS NOT NULL AND app_name != ''
            ORDER BY timestamp DESC LIMIT 50000
        ) ORDER BY timestamp ASC
        """,
        (start, end),
    )
    transitions: dict[str, int] = {}
    prev = None
    for (app,) in rows:
        if prev and prev != app:
            key = f"{prev} -> {app}"
            transitions[key] = transitions.get(key, 0) + 1
        prev = app
    keep = sorted(
        ((k, v) for k, v in transitions.items() if v >= MIN_FREQUENCY),
        key=lambda kv: -kv[1],
    )
    return [
        WorkPattern(kind="app_switch", label=f"{k} - {v} transitions", count=v)
        for k, v in keep[:10]
    ]


def repeated_text(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT text_content, COUNT(*) as cnt FROM ui_events
        WHERE timestamp BETWEEN ? AND ?
          AND event_type = 'text'
          AND text_content IS NOT NULL AND LENGTH(text_content) > 10
        GROUP BY text_content
        HAVING cnt >= ?
        ORDER BY cnt DESC LIMIT 15
        """,
        (start, end, MIN_FREQUENCY),
    )
    out = []
    for text, cnt in rows:
        text = (text or "").strip()
        if len(text) < 10:
            continue
        preview = text[:120] + "..." if len(text) > 120 else text
        out.append(
            WorkPattern(kind="repeated_text", label=f'Typed {cnt}x: "{preview}"', count=int(cnt))
        )
    return out[:10]


MIN_CLICKS_PER_DAY = 3   # a day only counts toward a habit above this floor


def daily_habits(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT timestamp, element_name FROM ui_events
        WHERE timestamp BETWEEN ? AND ?
          AND event_type = 'click'
          AND element_name IS NOT NULL AND element_name != ''
          AND element_name NOT IN ('scroll area','group')
        ORDER BY element_name, timestamp
        """,
        (start, end),
    )

    # Bucket by LOCAL calendar day. SQLite's date() buckets by UTC, so for a
    # user far enough from UTC one local day straddles a UTC boundary and
    # counted as two -- and since a habit is "days >= 2" below, that reported
    # a habit out of activity that only ever happened once.
    per_day: dict[tuple[str, str], int] = {}
    for ts, name in rows:
        if not name:
            continue
        epoch = parse_epoch(ts or "")
        if epoch <= 0:
            continue
        day = local_day_string(datetime.fromtimestamp(epoch, tz=timezone.utc))
        per_day[(name, day)] = per_day.get((name, day), 0) + 1

    habits: dict[str, dict] = {}
    for (name, _day), cnt in per_day.items():
        if cnt < MIN_CLICKS_PER_DAY:
            continue
        h = habits.setdefault(name, {"days": 0, "total": 0})
        h["days"] += 1
        h["total"] += cnt
    keep = sorted(
        ((n, h) for n, h in habits.items() if h["days"] >= 2),
        # name breaks ties so output stays deterministic regardless of the
        # order rows arrive in (the SQL ORDER BY no longer does it for us).
        key=lambda nh: (-nh[1]["total"], nh[0]),
    )
    return [
        WorkPattern(
            kind="daily_habit",
            label=f"'{n.strip()}' on {h['days']} days, {h['total']}x total",
            count=h["total"],
        )
        for n, h in keep[:10]
    ]
