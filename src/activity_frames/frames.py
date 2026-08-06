"""Assemble activity frames: the schema v1 document.

An ActivityFrame is one bounded stretch of human attention: an app (and
site, for browsers) with a start, an end, active time, the pages that
were on screen, and input volume. Everything in a frame is measured by
code from recorder data. There are no intent labels and no confidence
fields at this tier, because nothing here is guessed.

See SPEC.md for the full schema definition and determinism rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ._time import (
    fmt_local_hm,
    fmt_local_hms,
    hours_ago_window_utc,
    local_day_string,
    local_day_window_utc,
    now_utc_string,
)
from .db import Database
from .entities import parse_url
from .enrich import decode_text
from .sessionize import (
    DWELL_CAP,
    MERGE_FLICKER,
    SESSION_GAP,
    Segment,
    coverage as compute_coverage,
    segments as compute_segments,
)

SCHEMA_VERSION = 1

BLIND_SPOTS = [
    "Browser URLs are captured only for browser apps; native-app context comes from window titles.",
    "Time is attributed from event-driven frame capture, not a stopwatch; per-frame credit is capped.",
    "Click labels resolve only when the click lands on a recorded element; unresolved clicks are counted, not named.",
    "Activity on non-primary monitors may have reduced element resolution.",
    "Monitors are sessionized as separate streams; the same app visible on two monitors at once earns time on both.",
    "Audio (meetings, calls) is recorded by the engine when enabled but not yet compiled into frames.",
]


@dataclass
class PageView:
    kind: str
    entity: str | None
    count: int


@dataclass
class InputStats:
    keystrokes: int = 0
    clicks: int = 0
    text_events: int = 0
    copies: int = 0
    text_snippets: list[str] = field(default_factory=list)


@dataclass
class ActivityFrame:
    index: int
    app: str
    site: str | None
    start: str
    end: str
    duration_min: float
    wall_min: float
    windows: list[str]
    pages: list[PageView]
    input: InputStats
    interruptions: list[dict]
    evidence: dict

    def to_dict(self, include_input_text: bool = False) -> dict:
        d: dict = {
            "id": f"f-{self.index:04d}",
            "app": self.app,
        }

        if self.site:
            d["site"] = self.site

        d.update(
            start=self.start,
            end=self.end,
            duration_min=self.duration_min,
        )

        if abs(self.wall_min - self.duration_min) > 1:
            d["wall_min"] = self.wall_min

        if self.windows:
            d["windows"] = self.windows

        if self.pages:
            d["pages"] = [
                {
                    k: v
                    for k, v in dict(
                        kind=p.kind,
                        entity=p.entity,
                        count=p.count if p.count > 1 else None,
                    ).items()
                    if v is not None
                }
                for p in self.pages
            ]

        inp = {}

        if self.input.keystrokes:
            inp["keys"] = self.input.keystrokes

        if self.input.clicks:
            inp["clicks"] = self.input.clicks

        if self.input.copies:
            inp["copies"] = self.input.copies

        if include_input_text and self.input.text_snippets:
            inp["text"] = self.input.text_snippets

        if inp:
            d["input"] = inp

        if self.interruptions:
            d["interruptions"] = self.interruptions

        d["evidence"] = self.evidence

        return d


@dataclass
class ActivityDocument:
    schema_version: int
    generated_at: str
    window: dict
    coverage: dict
    frames: list[ActivityFrame]
    blind_spots: list[str]
    omitted_below_min: int = 0
    min_minutes: float = 0.0
    debug: dict | None = None

    def to_dict(self, include_input_text: bool = False) -> dict:
        d = {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "source": {"recorder": "nocta-recorder"},
            "window": self.window,
            "coverage": self.coverage,
            "frames": [
                f.to_dict(include_input_text)
                for f in self.frames
            ],
            "blind_spots": self.blind_spots,
        }

        if self.omitted_below_min:
            d["omitted"] = {
                "below_min_minutes": self.omitted_below_min,
                "min_minutes": self.min_minutes,
            }

        if self.debug:
            d["_debug"] = self.debug

        return d


def _pages_for_segment(seg: Segment) -> list[PageView]:
    """Aggregate URL views in a segment into typed page references."""

    views: list[PageView] = []
    index: dict[tuple[str, str | None], PageView] = {}

    for f in seg.frames:
        if not f.url:
            continue

        ref = parse_url(f.url)
        key = (ref.kind, ref.entity)

        existing = index.get(key)

        if existing is not None:
            existing.count += 1
        else:
            pv = PageView(
                kind=ref.kind,
                entity=ref.entity,
                count=1,
            )
            views.append(pv)
            index[key] = pv

    return views


def _top_windows(seg: Segment, limit: int = 3) -> list[str]:
    counts: dict[str, int] = {}

    for f in seg.frames:
        if f.window:
            w = f.window.strip()

            if w:
                counts[w] = counts.get(w, 0) + 1

    return [
        w
        for w, _ in sorted(
            counts.items(),
            key=lambda kv: -kv[1],
        )[:limit]
    ]


def build_frames(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    min_minutes: float = 0.0,
    include_text: bool = False,
    layout=None,
    dwell_cap: float = DWELL_CAP,
    session_gap: float = SESSION_GAP,
    merge_flicker: float = MERGE_FLICKER,
    debug: bool = False,
) -> ActivityDocument:
    """Compile a UTC window of recorder data into an ActivityDocument."""

    # ---------------------------------------------------------
    # NORMAL ACTIVITY-FRAMES PROCESSING
    # ---------------------------------------------------------

    segs = compute_segments(
        db,
        start_utc,
        end_utc,
        dwell_cap=dwell_cap,
        session_gap=session_gap,
        merge_flicker=merge_flicker,
    )

    cov = compute_coverage(
        db,
        start_utc,
        end_utc,
        session_gap=session_gap,
    )

    # ---------------------------------------------------------
    # WINDOWS RECORDER COMPATIBILITY FALLBACK
    # ---------------------------------------------------------
    #
    # Our Windows recorder writes valid rows into `frames`.
    # Some existing sessionization/coverage logic may not count
    # those rows correctly yet.
    #
    # Therefore count the raw rows directly as a fallback.
    # ---------------------------------------------------------

    try:
        direct_frame_count = db.scalar(
            """
            SELECT COUNT(*)
            FROM frames
            WHERE timestamp >= ?
              AND timestamp < ?
            """,
            (start_utc, end_utc),
            default=0,
        )
    except Exception:
        direct_frame_count = 0

    try:
        direct_distinct_apps = db.scalar(
            """
            SELECT COUNT(DISTINCT app_name)
            FROM frames
            WHERE timestamp >= ?
              AND timestamp < ?
              AND app_name IS NOT NULL
              AND app_name != ''
            """,
            (start_utc, end_utc),
            default=0,
        )
    except Exception:
        direct_distinct_apps = 0

    # ---------------------------------------------------------
    # INPUT EVENTS
    # ---------------------------------------------------------

    events_index: list[tuple[float, str, str | None]] = []

    if db.table_exists("ui_events"):
        from ._time import parse_epoch

        rows = db.rows(
            """
            SELECT timestamp, event_type, text_content
            FROM ui_events
            WHERE timestamp >= ?
              AND timestamp < ?
            ORDER BY timestamp ASC
            """,
            (start_utc, end_utc),
        )

        events_index = [
            (e, et or "", tx)
            for ts, et, tx in rows
            if (e := parse_epoch(ts or "")) > 0
        ]

    # ---------------------------------------------------------
    # ASSIGN EVENTS TO SEGMENTS
    # ---------------------------------------------------------

    import bisect as _bisect

    dev_segs: dict[str, list[Segment]] = {}

    for s in segs:
        dev = s.frames[0].device if s.frames else ""
        dev_segs.setdefault(dev, []).append(s)

    for lst in dev_segs.values():
        lst.sort(key=lambda s: s.start_epoch)

    dev_starts = {
        d: [s.start_epoch for s in lst]
        for d, lst in dev_segs.items()
    }

    seg_frames = sorted(
        (f.epoch, f.device)
        for s in segs
        for f in s.frames
    )

    sf_epochs = [
        e
        for e, _ in seg_frames
    ]

    def _nearest_device(epoch: float) -> str | None:
        if not sf_epochs:
            return None

        i = _bisect.bisect_left(
            sf_epochs,
            epoch,
        )

        if i >= len(sf_epochs):
            i = len(sf_epochs) - 1

        elif (
            i > 0
            and abs(sf_epochs[i - 1] - epoch)
            <= abs(sf_epochs[i] - epoch)
        ):
            i -= 1

        return seg_frames[i][1]

    seg_stats: dict[int, InputStats] = {}

    for epoch, etype, text in events_index:

        candidates = []

        for d, lst in dev_segs.items():

            j = (
                _bisect.bisect_right(
                    dev_starts[d],
                    epoch,
                )
                - 1
            )

            if (
                j >= 0
                and lst[j].end_epoch >= epoch
            ):
                candidates.append(lst[j])

        if not candidates:
            continue

        if len(candidates) == 1:
            target = candidates[0]

        else:
            near_dev = _nearest_device(epoch)

            target = next(
                (
                    c
                    for c in candidates
                    if (
                        c.frames[0].device
                        if c.frames
                        else ""
                    )
                    == near_dev
                ),
                candidates[0],
            )

        stats = seg_stats.setdefault(
            id(target),
            InputStats(),
        )

        if etype == "key":
            stats.keystrokes += 1

        elif etype == "click":
            stats.clicks += 1

        elif etype == "clipboard":
            stats.copies += 1

        elif etype == "text":

            stats.text_events += 1

            stats.keystrokes += (
                len(text)
                if text
                else 0
            )

            if include_text and text:

                decoded = decode_text(
                    text,
                    layout,
                ).strip()

                if len(decoded) > 2:

                    stats.text_snippets.append(
                        decoded[:120] + "..."
                        if len(decoded) > 120
                        else decoded
                    )

    # ---------------------------------------------------------
    # BUILD OUTPUT FRAMES
    # ---------------------------------------------------------

    frames_out: list[ActivityFrame] = []

    omitted_below_min = 0

    idx = 0

    debug_reasons: dict[str, str] = {}

    for seg in segs:

        duration_min = round(
            seg.active_seconds / 60,
            1,
        )

        if duration_min < min_minutes:

            omitted_below_min += 1

            continue

        idx += 1

        inp = seg_stats.get(
            id(seg),
            InputStats(),
        )

        fids = seg.frame_ids

        frame_id_str = f"f-{idx:04d}"

        if debug and seg.break_reason:
            debug_reasons[
                frame_id_str
            ] = seg.break_reason

        frames_out.append(
            ActivityFrame(
                index=idx,
                app=seg.app,
                site=seg.domain,
                start=fmt_local_hms(
                    seg.start_epoch
                ),
                end=fmt_local_hms(
                    seg.end_epoch
                ),
                duration_min=duration_min,
                wall_min=round(
                    seg.wall_seconds() / 60,
                    1,
                ),
                windows=_top_windows(seg),
                pages=_pages_for_segment(seg),
                input=inp,
                interruptions=[
                    {
                        k: v
                        for k, v in dict(
                            app=i.app,
                            site=i.domain,
                            seconds=i.seconds,
                        ).items()
                        if v is not None
                    }
                    for i in seg.interruptions
                ],
                evidence={
                    "frame_ids":
                        f"{min(fids)}..{max(fids)}"
                        if fids
                        else ""
                },
            )
        )

    # ---------------------------------------------------------
    # BUILD FINAL DOCUMENT
    # ---------------------------------------------------------

    doc = ActivityDocument(
        schema_version=SCHEMA_VERSION,

        generated_at=now_utc_string() + "Z",

        window={
            "start_utc": start_utc,
            "end_utc": end_utc,
        },

        coverage={
            "first_activity":
                fmt_local_hm(cov.first_epoch),

            "last_activity":
                fmt_local_hm(cov.last_epoch),

            "active_minutes":
                cov.active_minutes,

            "span_minutes":
                cov.span_minutes,

            "coverage_pct":
                cov.coverage_pct,

            # Windows recorder compatibility
            "frames_analyzed":
                max(
                    cov.frame_count,
                    direct_frame_count,
                ),

            "distinct_apps":
                max(
                    cov.distinct_apps,
                    direct_distinct_apps,
                ),

            "gaps": [
                {
                    "start":
                        fmt_local_hm(
                            g.start_epoch
                        ),

                    "end":
                        fmt_local_hm(
                            g.end_epoch
                        ),

                    "minutes":
                        g.minutes,
                }
                for g in cov.gaps
            ],
        },

        frames=frames_out,

        blind_spots=BLIND_SPOTS,

        omitted_below_min=omitted_below_min,

        min_minutes=min_minutes,
    )

    if debug and debug_reasons:

        doc.debug = {
            "sessionization":
                debug_reasons
        }

    return doc


def build_day(
    db: Database,
    day: str | None = None,
    **kwargs,
) -> ActivityDocument:
    """ActivityDocument for a local calendar day."""

    day = day or local_day_string()

    start, end = local_day_window_utc(day)

    doc = build_frames(
        db,
        start,
        end,
        **kwargs,
    )

    doc.window["day"] = day

    return doc


def build_recent(
    db: Database,
    hours: float = 2.0,
    **kwargs,
) -> ActivityDocument:
    """ActivityDocument for the last N hours."""

    start, end = hours_ago_window_utc(
        hours
    )

    return build_frames(
        db,
        start,
        end,
        **kwargs,
    )