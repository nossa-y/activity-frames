"""Read-only access to the local capture database.

The capture engine owns this database; activity-frames never writes to
it. Connections are opened with SQLite's read-only URI flag so a bug
here cannot corrupt capture data.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB_CANDIDATES = (
    "~/.nocta/db.sqlite",            # nocta-recorder default
    "~/.nocta/data/db.sqlite",
)


class RecorderDBNotFound(FileNotFoundError):
    """Raised when no capture database can be located."""


def find_default_db() -> str:
    """Locate the capture DB, honoring $AFRAMES_DB.

    When several candidate files exist (e.g. an older recorder layout's DB
    next to the current one), pick the most recently MODIFIED: the live
    capture DB is written every few seconds, so mtime identifies it reliably,
    whereas first-match-wins could select a stale file and make recent-window
    queries look empty.
    """
    for var in ("AFRAMES_DB",):
        env = os.environ.get(var)
        if env:
            p = Path(env).expanduser()
            if p.exists():
                return str(p)
            raise RecorderDBNotFound(f"${var} points to a missing file: {env}")
    existing = [p for cand in DEFAULT_DB_CANDIDATES if (p := Path(cand).expanduser()).exists()]
    if existing:
        return str(max(existing, key=lambda p: p.stat().st_mtime))
    raise RecorderDBNotFound(
        "No capture database found. Start recording with: aframes record "
        "(or point $AFRAMES_DB at an existing capture database)."
    )


class Database:
    """Minimal read-only SQLite wrapper (port of Nocta's SQLiteDB.swift)."""

    def __init__(self, path: str | None = None):
        self.path = path or find_default_db()
        p = Path(self.path).expanduser()
        if not p.exists():
            raise RecorderDBNotFound(f"Database not found: {self.path}")
        # as_uri() percent-encodes special characters and handles Windows
        # drive letters, unlike naive f"file:{path}" interpolation.
        uri = p.resolve().as_uri() + "?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, timeout=3.0)
        self._conn.execute("PRAGMA query_only = ON")

    def rows(self, sql: str, params: Iterable[Any] = ()) -> list[tuple]:
        cur = self._conn.execute(sql, tuple(params))
        try:
            return cur.fetchall()
        finally:
            cur.close()

    def scalar(self, sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
        rows = self.rows(sql, params)
        if rows and rows[0] and rows[0][0] is not None:
            return rows[0][0]
        return default

    def table_exists(self, name: str) -> bool:
        return (
            self.scalar(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            )
            > 0
        )

    def close(self) -> None:
        self._conn.close()

    def __del__(self) -> None:
        """Close the connection when the object is garbage-collected.

        This is a defensive backstop for callers that do not use the context
        manager or call close() explicitly (e.g. long-running MCP servers).
        Silently ignores errors because __del__ must never raise.
        """
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
