"""Turn frame snapshots into bounded activity segments."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit
from datetime import datetime, timezone

from .db import Database


DWELL_CAP = 90.0
SESSION_GAP = 300.0
MERGE_FLICKER = 20.0


# ============================================================
# TIMESTAMP HANDLING
# ============================================================

def _parse_timestamp(value) -> float:
    """
    Convert recorder/database timestamps into Unix epoch seconds.

    Supports:
        2026-08-03 23:25:39
        2026-08-03T23:25:39
        2026-08-03T23:25:39Z
        ISO timestamps with timezone offsets
        Unix timestamps
    """

    if value is None:
        return 0.0

    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    text = str(value).strip()

    if not text:
        return 0.0

    # Numeric timestamp stored as text
    try:
        number = float(text)
        if number > 0:
            return number
    except ValueError:
        pass

    # UTC timestamp ending in Z
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(
                text[:-1] + "+00:00"
            )
            return dt.timestamp()
    except ValueError:
        pass

    # General ISO timestamp
    try:
        dt = datetime.fromisoformat(text)

        # Recorder's timestamps without timezone are local Windows time
        if dt.tzinfo is None:
            dt = dt.astimezone()

        return dt.timestamp()

    except ValueError:
        pass

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(text, fmt)

            # Treat naive recorder timestamps as local time
            dt = dt.astimezone()

            return dt.timestamp()

        except ValueError:
            continue

    return 0.0


def _parse_window_timestamp(value) -> float:
    """Parse activity-frames query boundaries as UTC.

    The CLI/tests pass naive ISO day boundaries such as
    2026-07-04T00:00:00. Those boundaries represent UTC, unlike naive
    timestamps written by the Windows recorder, which represent local time.
    """
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        pass
    try:
        if text.endswith("Z"):
            dt = datetime.fromisoformat(text[:-1] + "+00:00")
        else:
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0


# ============================================================
# URL / DOMAIN
# ============================================================

def _domain(url: str | None) -> str | None:

    if not url:
        return None

    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None

    if not host:
        return None

    if host.startswith("www."):
        return host[4:]

    return host


# ============================================================
# CLEAN APPLICATION / WINDOW NAMES
# ============================================================

_FORMAT_CHARS = dict.fromkeys(
    map(ord, "‎‏​⁠﻿")
)


def clean_name(s: str) -> str:

    if not s:
        return ""

    return s.translate(
        _FORMAT_CHARS
    ).strip()


# ============================================================
# DATA STRUCTURES
# ============================================================

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
    domain: str | None

    start_epoch: float
    end_epoch: float

    active_seconds: float = 0.0

    frames: list[RawFrame] = field(
        default_factory=list
    )

    interruptions: list[Interruption] = field(
        default_factory=list
    )

    break_reason: str = ""

    @property
    def key(self) -> tuple[str, str | None]:

        return (
            self.app,
            self.domain,
        )

    @property
    def frame_ids(self) -> list[int]:

        return [
            frame.id
            for frame in self.frames
        ]

    def wall_seconds(self) -> float:

        return max(
            0.0,
            self.end_epoch - self.start_epoch,
        )


# ============================================================
# DATABASE HELPERS
# ============================================================

def _has_column(
    db: Database,
    table: str,
    column: str,
) -> bool:

    try:

        rows = db.rows(
            f"PRAGMA table_info({table})"
        )

        return any(
            row[1] == column
            for row in rows
        )

    except Exception:

        return False


# ============================================================
# LOAD FRAMES
# ============================================================

def load_frames(
    db: Database,
    start_utc: str,
    end_utc: str,
) -> list[RawFrame]:

    dev_col = (
        "device_name"
        if _has_column(
            db,
            "frames",
            "device_name",
        )
        else "''"
    )

    # Do NOT compare timestamps directly in SQLite.
    #
    # Windows recorder rows can contain local timestamps:
    #
    #     2026-08-03 23:25:39
    #
    # while activity-frames can request UTC timestamps:
    #
    #     2026-08-03T17:55:39Z
    #
    # Comparing these text values directly causes valid frames
    # to disappear. Therefore timestamps are normalized in Python.

    rows = db.rows(
        f"""
        SELECT
            id,
            timestamp,
            app_name,
            window_name,
            browser_url,
            {dev_col}
        FROM frames
        WHERE app_name IS NOT NULL
          AND app_name != ''
        ORDER BY timestamp ASC
        """
    )

    # Query/day boundaries are UTC.  Do not parse naive boundaries as
    # Windows local time; doing so truncates the tail of fixture days and
    # can hide post-gap frames (for example the GitHub segment in tests).
    start_epoch = _parse_window_timestamp(
        start_utc
    )

    end_epoch = _parse_window_timestamp(
        end_utc
    )

    output: list[RawFrame] = []

    for (
        fid,
        timestamp,
        app,
        window,
        url,
        device,
    ) in rows:

        epoch = _parse_timestamp(
            timestamp
        )

        if epoch <= 0:
            continue

        if start_epoch > 0 and epoch < start_epoch:
            continue

        if end_epoch > 0 and epoch >= end_epoch:
            continue

        cleaned_app = clean_name(
            app or ""
        )

        if not cleaned_app:
            continue

        cleaned_window = (
            clean_name(window)
            if window
            else window
        )

        output.append(
            RawFrame(
                id=int(fid),
                epoch=epoch,
                app=cleaned_app,
                window=cleaned_window,
                url=url,
                domain=_domain(url),
                device=device or "",
            )
        )

    output.sort(
        key=lambda frame: frame.epoch
    )

    return output


# ============================================================
# SEGMENTS
# ============================================================

def segments(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    dwell_cap: float = DWELL_CAP,
    session_gap: float = SESSION_GAP,
    merge_flicker: float = MERGE_FLICKER,
) -> list[Segment]:

    all_frames = load_frames(
        db,
        start_utc,
        end_utc,
    )

    if not all_frames:
        return []

    # Each monitor/device is sessionized independently.
    by_device: dict[str, list[RawFrame]] = {}

    for frame in all_frames:

        by_device.setdefault(
            frame.device,
            [],
        ).append(frame)

    result: list[Segment] = []

    for stream in by_device.values():

        stream.sort(
            key=lambda frame: frame.epoch
        )

        result.extend(
            _segment_stream(
                stream,
                dwell_cap=dwell_cap,
                session_gap=session_gap,
                merge_flicker=merge_flicker,
            )
        )

    result.sort(
        key=lambda segment: segment.start_epoch
    )

    return result


# ============================================================
# SEGMENT ONE DEVICE STREAM
# ============================================================

def _segment_stream(
    frames: list[RawFrame],
    *,
    dwell_cap: float,
    session_gap: float,
    merge_flicker: float,
) -> list[Segment]:

    if not frames:
        return []

    frames = sorted(
        frames,
        key=lambda frame: frame.epoch,
    )

    raw: list[Segment] = []

    current: Segment | None = None

    previous_key: tuple[str, str | None] | None = None

    for i, frame in enumerate(frames):

        if i + 1 < len(frames):

            gap_to_next = (
                frames[i + 1].epoch
                - frame.epoch
            )

            gap_to_next = max(
                0.0,
                gap_to_next,
            )

        else:

            gap_to_next = None

        dwell = (
            min(
                gap_to_next,
                dwell_cap,
            )
            if gap_to_next is not None
            else 0.0
        )

        key = (
            frame.app,
            frame.domain,
        )

        # ----------------------------------------------------
        # Start a new segment
        # ----------------------------------------------------

        if (
            current is None
            or key != current.key
        ):

            if (
                current is None
                and previous_key is None
            ):
                reason = "start"

            elif (
                current is None
                and previous_key is not None
            ):
                reason = "session_gap"

            else:
                reason = "context_switch"

            current = Segment(
                app=frame.app,
                domain=frame.domain,
                start_epoch=frame.epoch,
                end_epoch=frame.epoch,
                break_reason=reason,
            )

            raw.append(
                current
            )

        current.frames.append(
            frame
        )

        current.end_epoch = (
            frame.epoch
        )

        # ----------------------------------------------------
        # Active dwell
        # ----------------------------------------------------

        if (
            gap_to_next is not None
            and gap_to_next <= session_gap
        ):

            current.active_seconds += (
                dwell
            )

        # ----------------------------------------------------
        # Session gap
        # ----------------------------------------------------

        if (
            gap_to_next is not None
            and gap_to_next > session_gap
        ):

            previous_key = key

            current = None

    # ========================================================
    # FLICKER MERGE
    #
    # A -> B -> A
    #
    # If B is brief, merge the two A segments and record B
    # as an interruption.
    # ========================================================

    if merge_flicker <= 0:
        return raw

    merged: list[Segment] = []

    i = 0

    while i < len(raw):

        segment = raw[i]

        while (
            i + 2 < len(raw)

            and raw[i + 1].wall_seconds()
            <= merge_flicker

            and raw[i + 2].key
            == segment.key

            and (
                raw[i + 1].start_epoch
                - segment.end_epoch
            )
            <= session_gap

            and (
                raw[i + 2].start_epoch
                - raw[i + 1].end_epoch
            )
            <= session_gap
        ):

            flicker = raw[i + 1]

            continuation = raw[i + 2]

            interruption_seconds = (
                flicker.active_seconds
                or flicker.wall_seconds()
                or 1.0
            )

            segment.interruptions.append(
                Interruption(
                    app=flicker.app,
                    domain=flicker.domain,
                    seconds=round(
                        interruption_seconds,
                        1,
                    ),
                )
            )

            segment.frames.extend(
                continuation.frames
            )

            segment.active_seconds += (
                continuation.active_seconds
            )

            segment.end_epoch = (
                continuation.end_epoch
            )

            segment.interruptions.extend(
                continuation.interruptions
            )

            i += 2

        merged.append(
            segment
        )

        i += 1

    return merged


# ============================================================
# COVERAGE
# ============================================================

@dataclass
class Gap:

    start_epoch: float
    end_epoch: float

    @property
    def minutes(self) -> int:

        return int(
            (
                self.end_epoch
                - self.start_epoch
            )
            / 60
        )


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

    hour_histogram: dict[int, int]


def coverage(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    session_gap: float = SESSION_GAP,
) -> Coverage:

    frames = load_frames(
        db,
        start_utc,
        end_utc,
    )

    if not frames:

        return Coverage(
            first_epoch=0,
            last_epoch=0,
            active_minutes=0,
            span_minutes=0,
            coverage_pct=0,
            frame_count=0,
            distinct_apps=0,
            gaps=[],
            hour_histogram={},
        )

    active_minutes: set[int] = set()

    hour_minutes: dict[
        int,
        set[int]
    ] = {}

    gaps: list[Gap] = []

    apps: set[str] = set()

    previous_epoch: float | None = None

    for frame in frames:

        apps.add(
            frame.app
        )

        local = datetime.fromtimestamp(
            frame.epoch
        ).astimezone()

        minute_id = int(
            frame.epoch / 60
        )

        active_minutes.add(
            minute_id
        )

        hour_minutes.setdefault(
            local.hour,
            set(),
        ).add(
            minute_id
        )

        if (
            previous_epoch is not None
            and frame.epoch - previous_epoch
            > session_gap
        ):

            gaps.append(
                Gap(
                    previous_epoch,
                    frame.epoch,
                )
            )

        previous_epoch = (
            frame.epoch
        )

    first = frames[0].epoch

    last = frames[-1].epoch

    span_minutes = int(
        (last - first)
        / 60
    )

    active_minute_count = len(
        active_minutes
    )

    if span_minutes > 0:

        coverage_pct = min(
            100,
            int(
                active_minute_count
                / span_minutes
                * 100
            ),
        )

    else:

        coverage_pct = (
            100
            if active_minute_count > 0
            else 0
        )

    return Coverage(
        first_epoch=first,
        last_epoch=last,
        active_minutes=active_minute_count,
        span_minutes=span_minutes,
        coverage_pct=coverage_pct,
        frame_count=len(frames),
        distinct_apps=len(apps),
        gaps=[
            gap
            for gap in gaps
            if gap.minutes >= 5
        ],
        hour_histogram={
            hour: len(minutes)
            for hour, minutes
            in sorted(
                hour_minutes.items()
            )
        },
    )


# ============================================================
# APP LEDGER
# ============================================================

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
) -> list[AppUsage]:

    all_frames = load_frames(
        db,
        start_utc,
        end_utc,
    )

    if not all_frames:
        return []

    dwell: dict[str, float] = {}

    windows: dict[
        str,
        dict[str, float]
    ] = {}

    sessions_count: dict[
        str,
        int
    ] = {}

    longest: dict[
        str,
        float
    ] = {}

    by_device: dict[
        str,
        list[RawFrame]
    ] = {}

    for frame in all_frames:

        by_device.setdefault(
            frame.device,
            [],
        ).append(frame)

    # --------------------------------------------------------
    # Process each monitor/device independently
    # --------------------------------------------------------

    for device_frames in by_device.values():

        device_frames.sort(
            key=lambda frame: frame.epoch
        )

        current_app: str | None = None

        current_session_seconds = 0.0

        for i, frame in enumerate(
            device_frames[:-1]
        ):

            next_frame = (
                device_frames[i + 1]
            )

            gap = (
                next_frame.epoch
                - frame.epoch
            )

            if gap < 0:
                continue

            # Long inactivity = new session
            if gap > session_gap:

                current_app = None

                current_session_seconds = 0.0

                continue

            d = min(
                gap,
                dwell_cap,
            )

            # ------------------------------------------------
            # Total app dwell
            # ------------------------------------------------

            dwell[frame.app] = (
                dwell.get(
                    frame.app,
                    0.0,
                )
                + d
            )

            # ------------------------------------------------
            # Window dwell
            # ------------------------------------------------

            if frame.window:

                windows.setdefault(
                    frame.app,
                    {},
                )

                windows[
                    frame.app
                ][
                    frame.window
                ] = (
                    windows[
                        frame.app
                    ].get(
                        frame.window,
                        0.0,
                    )
                    + d
                )

            # ------------------------------------------------
            # Sessions
            # ------------------------------------------------

            if d > 0:

                if current_app != frame.app:

                    sessions_count[
                        frame.app
                    ] = (
                        sessions_count.get(
                            frame.app,
                            0,
                        )
                        + 1
                    )

                    current_app = (
                        frame.app
                    )

                    current_session_seconds = 0.0

                current_session_seconds += (
                    d
                )

                longest[
                    frame.app
                ] = max(
                    longest.get(
                        frame.app,
                        0.0,
                    ),
                    current_session_seconds,
                )

    # ========================================================
    # BUILD RESULT
    # ========================================================

    output: list[AppUsage] = []

    for app, seconds in sorted(
        dwell.items(),
        key=lambda item: -item[1],
    ):

        # Preserve original minimum usage threshold
        if seconds < 20:
            continue

        top_windows = sorted(
            windows.get(
                app,
                {},
            ).items(),
            key=lambda item: -item[1],
        )[:4]

        output.append(
            AppUsage(
                app=app,

                minutes=round(
                    seconds / 60,
                    1,
                ),

                sessions=sessions_count.get(
                    app,
                    1,
                ),

                longest_session_min=int(
                    longest.get(
                        app,
                        0.0,
                    )
                    / 60
                ),

                top_windows=[
                    window
                    for window, _
                    in top_windows
                ],
            )
        )

    return output