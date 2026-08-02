"""Repetitive-workflow detection (port of Nocta's PatternDetector.swift).

Seven deterministic detectors over the recorder DB. Each returns
WorkPattern rows: a machine-readable kind, a human-readable label, and
the observed count. No scoring, no inference; a pattern is reported
only when it actually repeated.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .db import Database

MIN_FREQUENCY = 3
_GENERIC_ELEMENTS = {"scroll area", "group", "cell", "text", "text field"}


@dataclass
class WorkPattern:
    kind: str    # repeated_click | url_pattern | action_sequence | app_switch | repeated_text | daily_habit | temporal_rhythm
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
    out += temporal_rhythms(db, start_utc, end_utc)
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


def daily_habits(db: Database, start: str, end: str) -> list[WorkPattern]:
    rows = db.rows(
        """
        SELECT date(timestamp) as day, element_name, COUNT(*) as cnt FROM ui_events
        WHERE timestamp BETWEEN ? AND ?
          AND event_type = 'click'
          AND element_name IS NOT NULL AND element_name != ''
          AND element_name NOT IN ('scroll area','group')
        GROUP BY day, element_name
        HAVING cnt >= 3
        ORDER BY element_name, day
        """,
        (start, end),
    )
    habits: dict[str, dict] = {}
    for _day, name, cnt in rows:
        if not name:
            continue
        h = habits.setdefault(name, {"days": 0, "total": 0})
        h["days"] += 1
        h["total"] += int(cnt)
    keep = sorted(
        ((n, h) for n, h in habits.items() if h["days"] >= 2),
        key=lambda nh: -nh[1]["total"],
    )
    return [
        WorkPattern(
            kind="daily_habit",
            label=f"'{n.strip()}' on {h['days']} days, {h['total']}x total",
            count=h["total"],
        )
        for n, h in keep[:10]
    ]


MIN_DAYS_FOR_RHYTHM = 3
MIN_REGULARITY = 0.60


def temporal_rhythms(db: Database, start: str, end: str) -> list[WorkPattern]:
    """Detect temporal rhythm patterns in user activity.

    Clusters focused app frames by local 30-minute time bins across calendar days.
    Emits WorkPattern(kind="temporal_rhythm") when a bin/span is hit on >= 3 distinct
    days at a regularity >= 0.60 (fraction of active days in the window that hit the bin).
    Adjacent qualifying bins per app are merged into a single contiguous span before the
    top-12 cut.
    """
    from datetime import datetime

    from ._time import parse_epoch
    from .sessionize import clean_name

    if not db.table_exists("frames"):
        return []

    rows = db.rows(
        """
        SELECT app_name, timestamp FROM (
            SELECT timestamp, app_name FROM frames
            WHERE timestamp BETWEEN ? AND ?
              AND focused = 1
              AND app_name IS NOT NULL AND app_name != ''
            ORDER BY timestamp DESC LIMIT 50000
        ) ORDER BY timestamp ASC
        """,
        (start, end),
    )
    if not rows:
        return []

    all_days_with_activity: set[str] = set()
    binned: dict[tuple[str, int], dict] = {}

    for app_raw, ts in rows:
        epoch = parse_epoch(ts or "")
        if epoch <= 0:
            continue
        dt = datetime.fromtimestamp(epoch).astimezone()
        day_str = dt.strftime("%Y-%m-%d")
        all_days_with_activity.add(day_str)

        hour = dt.hour
        minute = dt.minute
        bin_idx = hour * 2 + (1 if minute >= 30 else 0)
        app = clean_name(app_raw or "")
        if not app:
            continue

        entry = binned.setdefault((app, bin_idx), {"days": set(), "count": 0})
        entry["days"].add(day_str)
        entry["count"] += 1

    total_active_days = len(all_days_with_activity)
    if total_active_days == 0:
        return []

    app_bins: dict[str, list[tuple[int, set[str], int]]] = {}
    for (app, bin_idx), data in binned.items():
        days_hit = len(data["days"])
        regularity = days_hit / total_active_days
        if days_hit >= MIN_DAYS_FOR_RHYTHM and regularity >= MIN_REGULARITY:
            app_bins.setdefault(app, []).append((bin_idx, data["days"], data["count"]))

    merged_rhythms: list[tuple[float, int, int, str, int, int]] = []

    for app, bin_list in app_bins.items():
        bin_list.sort(key=lambda x: x[0])

        i = 0
        while i < len(bin_list):
            b_start, days_set, count_sum = bin_list[i]
            b_end = b_start + 1
            combined_days = set(days_set)

            j = i + 1
            while j < len(bin_list) and bin_list[j][0] == b_end:
                combined_days.update(bin_list[j][1])
                count_sum += bin_list[j][2]
                b_end = bin_list[j][0] + 1
                j += 1

            days_hit_span = len(combined_days)
            reg_span = days_hit_span / total_active_days
            merged_rhythms.append((reg_span, days_hit_span, count_sum, app, b_start, b_end))
            i = j

    merged_rhythms.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3], x[4]))

    out: list[WorkPattern] = []
    for reg, days_hit, total_cnt, app, b_start, b_end in merged_rhythms[:12]:
        start_h, start_m = divmod(b_start * 30, 60)
        end_h, end_m = divmod(b_end * 30, 60)
        time_str = f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}"
        label = (
            f"{app} active {time_str} on {days_hit}/{total_active_days} days "
            f"(regularity {reg:.2f})"
        )
        out.append(
            WorkPattern(
                kind="temporal_rhythm",
                label=label,
                count=total_cnt,
            )
        )

    return out
