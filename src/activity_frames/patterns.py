"""Repetitive-workflow detection (port of Nocta's PatternDetector.swift).

Seven deterministic detectors over the recorder DB. Each returns
WorkPattern rows: a machine-readable kind, a human-readable label, and
the observed count. No scoring, no inference; a pattern is reported
only when it actually repeated.

Detectors:
  1. repeated_clicks    - identical UI elements clicked MIN_FREQUENCY+ times
  2. url_patterns       - URL subtree visited MIN_FREQUENCY+ times
  3. action_sequences   - repeated click bigrams and trigrams
  4. app_switching      - repeated app-transition pairs
  5. repeated_text      - identical typed strings (off by default)
  6. daily_habits       - UI actions repeated across multiple calendar days
  7. temporal_rhythms   - clock-hour clustering of recurring activities
                          across MIN_DAYS_FOR_RHYTHM+ distinct days.
                          Reports phase (hour window) and regularity (0-1).
                          Measured only: no interpretation, no labelling.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .db import Database

MIN_FREQUENCY = 3
MIN_DAYS_FOR_RHYTHM = 3       # days a phase must appear on before we emit it
RHYTHM_BIN_MINUTES = 30      # bucket width for hour-of-day clustering
RHYTHM_REGULARITY_THRESHOLD = 0.6  # fraction of observed days that must hit
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


def temporal_rhythms(db: Database, start: str, end: str) -> list[WorkPattern]:
    """Detect hour-of-day clustering of recurring app sessions across days.

    Algorithm (all deterministic, zero interpretation):

    1. Pull every focused frame in the window; extract (date, app, hour_bin)
       where hour_bin = floor(local_minute_of_day / RHYTHM_BIN_MINUTES).
    2. For each (app, hour_bin) pair count how many distinct calendar days it
       appears on and how many days are in the window.
    3. Emit a WorkPattern when:
         - distinct days  >= MIN_DAYS_FOR_RHYTHM
         - regularity     >= RHYTHM_REGULARITY_THRESHOLD
       where regularity = distinct_days_hit / total_days_in_window (capped 1).

    Output label format:
        "<App> active at HH:MM-HH:MM on <n>/<total> days (regularity 0.87)"

    The label carries the observed times and a regularity number.
    Meaning ("morning ritual", "focus block") is the consuming agent's job
    per SPEC §7 Tier-2 rules — we emit timing, not interpretation.
    """
    from datetime import datetime, timezone

    rows = db.rows(
        """
        SELECT timestamp, app_name FROM frames
        WHERE timestamp BETWEEN ? AND ?
          AND focused = 1
          AND app_name IS NOT NULL AND app_name != ''
        ORDER BY timestamp ASC
        """,
        (start, end),
    )
    if not rows:
        return []

    # ---- Build (app, bin) -> set[date] map ---------------------------------
    # Parse timestamps into local (date, minute-of-day).
    # We parse to epoch via strptime (same pattern as parse_epoch in _time.py)
    # then convert to local wall time to get hour-of-day.
    bin_days: dict[tuple[str, int], set[str]] = {}
    seen_dates: set[str] = set()

    for (ts_raw, app_name) in rows:
        if not ts_raw or len(ts_raw) < 19:
            continue
        try:
            dt_utc = datetime.strptime(ts_raw[:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        dt_local = dt_utc.astimezone()
        local_date = dt_local.strftime("%Y-%m-%d")
        seen_dates.add(local_date)
        local_minute_of_day = dt_local.hour * 60 + dt_local.minute
        hour_bin = local_minute_of_day // RHYTHM_BIN_MINUTES
        key = (app_name, hour_bin)
        bin_days.setdefault(key, set()).add(local_date)

    if not seen_dates:
        return []

    total_days = len(seen_dates)

    # ---- Score and filter --------------------------------------------------
    candidates: list[tuple[str, int, int, float]] = []  # (app, bin, days_hit, regularity)
    for (app_name, hour_bin), days_hit_set in bin_days.items():
        days_hit = len(days_hit_set)
        if days_hit < MIN_DAYS_FOR_RHYTHM:
            continue
        regularity = days_hit / total_days
        if regularity < RHYTHM_REGULARITY_THRESHOLD:
            continue
        candidates.append((app_name, hour_bin, days_hit, regularity))

    # Sort by regularity desc, then days_hit desc, then app name for stability.
    candidates.sort(key=lambda c: (-c[3], -c[2], c[0]))

    out: list[WorkPattern] = []
    for app_name, hour_bin, days_hit, regularity in candidates[:12]:
        bin_start_min = hour_bin * RHYTHM_BIN_MINUTES
        bin_end_min = bin_start_min + RHYTHM_BIN_MINUTES
        start_hm = f"{bin_start_min // 60:02d}:{bin_start_min % 60:02d}"
        end_hm = f"{bin_end_min // 60:02d}:{bin_end_min % 60:02d}"
        out.append(
            WorkPattern(
                kind="temporal_rhythm",
                label=(
                    f"{app_name} active {start_hm}-{end_hm} "
                    f"on {days_hit}/{total_days} days "
                    f"(regularity {regularity:.2f})"
                ),
                count=days_hit,
            )
        )
    return out
