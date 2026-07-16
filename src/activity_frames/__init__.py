"""activity-frames: episodic memory for AI agents.

Compiles raw screen-capture data (a local capture database) into
structured, deterministic activity frames that any agent can consume.
No LLM in the loop: same input, same output, every time.

Quickstart:

    from activity_frames import ActivityLog

    log = ActivityLog()                    # finds the local capture DB
    doc = log.day()                        # today's activity frames
    print(log.context(hours=2))            # paste-ready agent context
"""
from __future__ import annotations

from .communications import COMM_KINDS, CommSurface, TitleItem
from .communications import surfaces as comm_surfaces
from .db import Database, RecorderDBNotFound, find_default_db
from .emit import context_block, to_json, to_markdown, to_yaml
from .entities import PageRef, parse_url
from .frames import (
    SCHEMA_VERSION,
    ActivityDocument,
    ActivityFrame,
    build_day,
    build_frames,
    build_recent,
)
from .patterns import WorkPattern, detect as detect_patterns
from .sessionize import Coverage, Segment, app_ledger, coverage, segments

__version__ = "0.1.0"
__all__ = [
    "ActivityLog",
    "ActivityDocument",
    "ActivityFrame",
    "COMM_KINDS",
    "CommSurface",
    "Coverage",
    "Database",
    "PageRef",
    "RecorderDBNotFound",
    "SCHEMA_VERSION",
    "Segment",
    "TitleItem",
    "WorkPattern",
    "app_ledger",
    "build_day",
    "build_frames",
    "build_recent",
    "comm_surfaces",
    "context_block",
    "coverage",
    "detect_patterns",
    "find_default_db",
    "parse_url",
    "segments",
    "to_json",
    "to_markdown",
    "to_yaml",
]


class ActivityLog:
    """High-level facade over a local capture database."""

    def __init__(
        self,
        db_path: str | None = None,
        *,
        layout: str | dict | None = None,
        min_minutes: float = 0.5,
    ):
        self.db = Database(db_path)
        self.layout = layout
        self.min_minutes = min_minutes

    # ---- documents ----

    def day(self, day: str | None = None, **kwargs) -> ActivityDocument:
        """Activity frames for a local calendar day ("YYYY-MM-DD", default today)."""
        kwargs.setdefault("min_minutes", self.min_minutes)
        kwargs.setdefault("layout", self.layout)
        return build_day(self.db, day, **kwargs)

    def recent(self, hours: float = 2.0, **kwargs) -> ActivityDocument:
        """Activity frames for the last N hours."""
        kwargs.setdefault("min_minutes", self.min_minutes)
        kwargs.setdefault("layout", self.layout)
        return build_recent(self.db, hours, **kwargs)

    def window(self, start_utc: str, end_utc: str, **kwargs) -> ActivityDocument:
        """Activity frames for an explicit UTC window."""
        kwargs.setdefault("min_minutes", self.min_minutes)
        kwargs.setdefault("layout", self.layout)
        return build_frames(self.db, start_utc, end_utc, **kwargs)

    # ---- agent-ready strings ----

    def context(self, hours: float = 2.0, *, max_frames: int = 40, **kwargs) -> str:
        """Compact plaintext context block for the last N hours."""
        return context_block(self.recent(hours, **kwargs), max_frames=max_frames)

    def day_context(self, day: str | None = None, *, max_frames: int = 40, **kwargs) -> str:
        """Compact plaintext context block for a local day."""
        return context_block(self.day(day, **kwargs), max_frames=max_frames)

    # ---- extras ----

    def patterns(self, days: int = 7, *, include_text: bool = False) -> list[WorkPattern]:
        """Repetitive workflow patterns over the last N days.

        include_text enables the repeated-text detector, whose labels
        quote raw typed content. Off by default.
        """
        from datetime import datetime, timedelta, timezone

        from ._time import utc_string

        now = datetime.now(timezone.utc)
        return detect_patterns(
            self.db, utc_string(now - timedelta(days=days)), utc_string(now),
            include_text=include_text,
        )

    def apps(self, day: str | None = None):
        """Per-app usage ledger for a local day."""
        from ._time import local_day_string, local_day_window_utc

        start, end = local_day_window_utc(day or local_day_string())
        return app_ledger(self.db, start, end)

    def communications(
        self,
        hours: float = 24.0,
        *,
        day: str | None = None,
        kinds: frozenset[str] | set[str] | None = None,
    ) -> list[CommSurface]:
        """Communication surfaces (email / messaging / notifications) with the
        window titles measured on each. `day` (YYYY-MM-DD) overrides `hours`.

        Titles only — message bodies are never read. See communications.py.
        """
        from ._time import hours_ago_window_utc, local_day_window_utc

        if day:
            start, end = local_day_window_utc(day)
        else:
            start, end = hours_ago_window_utc(hours)
        return comm_surfaces(self.db, start, end, kinds=kinds)

    def close(self) -> None:
        self.db.close()
