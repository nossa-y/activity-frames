"""Turn frame snapshots into bounded activity segments.

The recorder stores instants: one row per screen change. This module
compiles those instants into what an agent actually needs: contiguous
segments of "the user was in app X (on site Y) from T1 to T2".

All math is deterministic and documented:

- dwell: a frame contributes min(gap_to_next_frame, DWELL_CAP) seconds
  of active time. Capture is event-driven (median gap ~9s); a long gap
  means the screen was static or the user was away, so dwell is capped.
- segment boundary: the (app, site) context key changes, or a gap
  larger than SESSION_GAP occurs.
- flicker merge: an interruption shorter than merge_flicker seconds
  that returns to the same context key is folded into the surrounding
  segment and recorded in `interruptions` (nothing is hidden).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from ._time import parse_epoch
from .db import Database

DWELL_CAP = 90.0        # seconds; max credit for one frame
SESSION_GAP = 300.0     # seconds; larger gap = user away / new session
MERGE_FLICKER = 20.0    # seconds; brief context switches fold into host segment


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host:
        return None
    return host[4:] if host.startswith("www.") else host


# Invisible direction/format marks that pollute captured app names
# (e.g. WhatsApp ships with a leading U+200E).
_FORMAT_CHARS = dict.fromkeys(map(ord, "‎‏​⁠﻿"))


def clean_name(s: str) -> str:
    return s.translate(_FORMAT_CHARS).strip()


@dataclass
class RawFrame:
    id: int
    epoch: float
    app: str
    window: str | None
    url: str | None
    domain: str | None
    device: str = ""


@dataclass
class Interruption:
    app: str
    domain: str | None
    seconds: float


@dataclass
class Segment:
    app: str
    domain: str | None            # None for non-browser apps
    start_epoch: float
    end_epoch: float
    active_seconds: float = 0.0
    frames: list[RawFrame] = field(default_factory=list)
    interruptions: list[Interruption] = field(default_factory=list)
    break_reason: str = ""        # why this segment started (debug)

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.app, self.domain)

    @property
    def frame_ids(self) -> list[int]:
        return [f.id for f in self.frames]

    def wall_seconds(self) -> float:
        return max(0.0, self.end_epoch - self.start_epoch)


def _has_column(db: Database, table: str, column: str) -> bool:
    try:
        return any(r[1] == column for r in db.rows(f"PRAGMA table_info({table})"))
    except Exception:
        return False


def load_frames(db: Database, start_utc: str, end_utc: str) -> list[RawFrame]:
    # device_name is optional: older or non-default capture schemas lack it.
    dev_col = "device_name" if _has_column(db, "frames", "device_name") else "''"
    rows = db.rows(
        f"""
        SELECT id, timestamp, app_name, window_name, browser_url, {dev_col}
        FROM frames
        WHERE timestamp >= ? AND timestamp < ?
          AND app_name IS NOT NULL AND app_name != ''
        ORDER BY timestamp ASC
        """,
        (start_utc, end_utc),
    )
    out = []
    for fid, ts, app, window, url, device in rows:
        epoch = parse_epoch(ts or "")
        if epoch <= 0:
            continue
        out.append(
            RawFrame(
                id=int(fid), epoch=epoch, app=clean_name(app or ""),
                window=clean_name(window) if window else window,
                url=url, domain=_domain(url),
                device=device or "",
            )
        )
    return out


def segments(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    dwell_cap: float = DWELL_CAP,
    session_gap: float = SESSION_GAP,
    merge_flicker: float = MERGE_FLICKER,
) -> list[Segment]:
    """Chronological (app, site) segments for a UTC window.

    Frames are partitioned by capture device (each monitor records its
    own stream); segmentation runs per device so two simultaneous
    monitors do not shred each other's sessions. The merged result is
    sorted by start time.
    """
    all_frames = load_frames(db, start_utc, end_utc)
    if not all_frames:
        return []

    by_device: dict[str, list[RawFrame]] = {}
    for f in all_frames:
        by_device.setdefault(f.device, []).append(f)

    merged: list[Segment] = []
    for stream in by_device.values():
        merged.extend(
            _segment_stream(stream, dwell_cap=dwell_cap,
                            session_gap=session_gap,
                            merge_flicker=merge_flicker)
        )
    merged.sort(key=lambda s: s.start_epoch)
    return merged


def _segment_stream(
    frames: list[RawFrame],
    *,
    dwell_cap: float,
    session_gap: float,
    merge_flicker: float,
) -> list[Segment]:
    if not frames:
        return []

    # Pass 1: raw segmentation on context-key change or session gap.
    raw: list[Segment] = []
    cur: Segment | None = None
    prev_key: tuple[str, str | None] | None = None
    for i, f in enumerate(frames):
        gap_to_next = (
            frames[i + 1].epoch - f.epoch if i + 1 < len(frames) else None
        )
        dwell = min(gap_to_next, dwell_cap) if gap_to_next is not None else 0.0

        key = (f.app, f.domain)
        if cur is None or key != cur.key:
            # Determine why this segment starts.
            if cur is None and prev_key is None:
                reason = "start"
            elif cur is None and prev_key is not None:
                reason = "session_gap"
            else:
                reason = "context_switch"
            cur = Segment(
                app=f.app, domain=f.domain,
                start_epoch=f.epoch, end_epoch=f.epoch,
                break_reason=reason,
            )
            raw.append(cur)
        cur.frames.append(f)
        cur.end_epoch = f.epoch
        if gap_to_next is not None and gap_to_next <= session_gap:
            cur.active_seconds += dwell
        if gap_to_next is not None and gap_to_next > session_gap:
            prev_key = key
            cur = None  # session break: next frame starts a new segment

    # Pass 2: flicker merge. A -> B -> A where B is brief becomes one A
    # segment with B recorded as an interruption.
    if merge_flicker <= 0:
        return raw
    merged: list[Segment] = []
    i = 0
    while i < len(raw):
        seg = raw[i]
        while (
            i + 2 < len(raw)
            and raw[i + 1].wall_seconds() <= merge_flicker
            and raw[i + 2].key == seg.key
            # never merge across a session break, on either side of B
            and raw[i + 1].start_epoch - seg.end_epoch <= session_gap
            and raw[i + 2].start_epoch - raw[i + 1].end_epoch <= session_gap
        ):
            flicker, cont = raw[i + 1], raw[i + 2]
            # The flicker's time is recorded on the interruption, NOT
            # added to the host segment's active time: active_seconds
            # stays honest about time spent in THIS context.
            seg.interruptions.append(
                Interruption(
                    app=flicker.app, domain=flicker.domain,
                    seconds=round(flicker.active_seconds or flicker.wall_seconds() or 1.0, 1),
                )
            )
            seg.frames.extend(cont.frames)
            seg.active_seconds += cont.active_seconds
            seg.end_epoch = cont.end_epoch
            seg.interruptions.extend(cont.interruptions)
            i += 2
        merged.append(seg)
        i += 1
    return merged


# ---- Coverage (port of ActivitySkeletonBuilder's one-pass measures) ----

@dataclass
class Gap:
    start_epoch: float
    end_epoch: float

    @property
    def minutes(self) -> int:
        return int((self.end_epoch - self.start_epoch) / 60)


@dataclass
class Coverage:
    first_epoch: float
    last_epoch: float
    active_minutes: int
    span_minutes: int
    coverage_pct: int
    frame_count: int
    distinct_apps: int
    gaps: list[Gap]
    hour_histogram: dict[int, int]   # local hour -> active minutes


def coverage(db: Database, start_utc: str, end_utc: str,
             *, session_gap: float = SESSION_GAP) -> Coverage:
    frames = load_frames(db, start_utc, end_utc)
    if not frames:
        return Coverage(0, 0, 0, 0, 0, 0, 0, [], {})

    from datetime import datetime

    active_minutes: set[int] = set()
    hour_minutes: dict[int, set[int]] = {}
    gaps: list[Gap] = []
    apps: set[str] = set()
    prev: float | None = None

    for f in frames:
        apps.add(f.app)
        local = datetime.fromtimestamp(f.epoch).astimezone()
        minute_id = int(f.epoch / 60)
        active_minutes.add(minute_id)
        hour_minutes.setdefault(local.hour, set()).add(minute_id)
        if prev is not None and f.epoch - prev > session_gap:
            gaps.append(Gap(prev, f.epoch))
        prev = f.epoch

    first, last = frames[0].epoch, frames[-1].epoch
    span_min = int((last - first) / 60)
    active_min = len(active_minutes)
    pct = min(100, int(active_min / span_min * 100)) if span_min > 0 else 0
    return Coverage(
        first_epoch=first,
        last_epoch=last,
        active_minutes=active_min,
        span_minutes=span_min,
        coverage_pct=pct,
        frame_count=len(frames),
        distinct_apps=len(apps),
        gaps=[g for g in gaps if g.minutes >= 5],
        hour_histogram={h: len(m) for h, m in sorted(hour_minutes.items())},
    )


# ---- App ledger (per-app aggregates over a window) ---------------------

@dataclass
class AppUsage:
    app: str
    minutes: float
    sessions: int
    longest_session_min: int
    top_windows: list[str]


def app_ledger(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    dwell_cap: float = DWELL_CAP,
    session_gap: float = SESSION_GAP,
    merge_flicker: float = MERGE_FLICKER,
) -> list[AppUsage]:
    all_frames = load_frames(db, start_utc, end_utc)
    dwell: dict[str, float] = {}
    windows: dict[str, dict[str, float]] = {}
    sessions: dict[str, int] = {}
    longest: dict[str, float] = {}

    # Per-device streams: dwell is the gap to the next frame on the SAME
    # monitor, so simultaneous monitors do not corrupt each other's math.
    by_device: dict[str, list[RawFrame]] = {}
    for f in all_frames:
        by_device.setdefault(f.device, []).append(f)

    for frames in by_device.values():
        # Pass 1: raw segmentation by app context key or session_gap.
        raw_segs: list[Segment] = []
        cur: Segment | None = None
        for i, f in enumerate(frames):
            gap_to_next = (
                frames[i + 1].epoch - f.epoch if i + 1 < len(frames) else None
            )
            d = min(gap_to_next, dwell_cap) if gap_to_next is not None else 0.0

            if gap_to_next is not None and gap_to_next <= session_gap and d > 0:
                dwell[f.app] = dwell.get(f.app, 0.0) + d
                if f.window:
                    windows.setdefault(f.app, {})
                    windows[f.app][f.window] = windows[f.app].get(f.window, 0.0) + d

            if cur is None or f.app != cur.app:
                cur = Segment(
                    app=f.app, domain=None, start_epoch=f.epoch, end_epoch=f.epoch
                )
                raw_segs.append(cur)

            cur.frames.append(f)
            cur.end_epoch = f.epoch
            if gap_to_next is not None and gap_to_next <= session_gap:
                cur.active_seconds += d
            if gap_to_next is not None and gap_to_next > session_gap:
                cur = None

        # Pass 2: flicker fold (A -> B -> A where B wall_seconds <= merge_flicker).
        if merge_flicker > 0:
            merged_segs: list[Segment] = []
            i = 0
            while i < len(raw_segs):
                seg = raw_segs[i]
                while (
                    i + 2 < len(raw_segs)
                    and raw_segs[i + 1].wall_seconds() <= merge_flicker
                    and raw_segs[i + 2].app == seg.app
                    and raw_segs[i + 1].start_epoch - seg.end_epoch <= session_gap
                    and raw_segs[i + 2].start_epoch - raw_segs[i + 1].end_epoch <= session_gap
                ):
                    cont = raw_segs[i + 2]
                    seg.frames.extend(cont.frames)
                    seg.active_seconds += cont.active_seconds
                    seg.end_epoch = cont.end_epoch
                    i += 2
                merged_segs.append(seg)
                i += 1
        else:
            merged_segs = raw_segs

        for seg in merged_segs:
            if seg.active_seconds > 0 or seg.wall_seconds() > 0:
                sessions[seg.app] = sessions.get(seg.app, 0) + 1
                longest[seg.app] = max(longest.get(seg.app, 0.0), seg.active_seconds)

    out = []
    for app, secs in sorted(dwell.items(), key=lambda kv: -kv[1]):
        if secs < 20:
            continue
        tops = sorted(windows.get(app, {}).items(), key=lambda kv: -kv[1])[:4]
        out.append(
            AppUsage(
                app=app,
                minutes=round(secs / 60, 1),
                sessions=sessions.get(app, 1),
                longest_session_min=int(longest.get(app, 0.0) / 60),
                top_windows=[w for w, _ in tops],
            )
        )
    return out
