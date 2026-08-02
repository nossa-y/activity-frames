"""Unit tests for the temporal rhythm detector (patterns.py)."""
import sqlite3
from pathlib import Path

from activity_frames.db import Database
from activity_frames.patterns import detect, temporal_rhythms


def _create_rhythm_db(tmp_path: Path, days_app_map: list[tuple[str, str, int, int]]) -> Database:
    """Helper creating a test capture DB with frames.

    days_app_map: list of (day_str, app_name, hour, minute) tuples.
    """
    path = tmp_path / f"rhythm_{hash(tuple(days_app_map)) & 0xFFFFFFFF}.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN,
            browser_url TEXT, device_name TEXT NOT NULL DEFAULT ''
        );
        """
    )
    for day, app, h, m in days_app_map:
        ts = f"{day}T{h:02d}:{m:02d}:00.000000+00:00"
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, focused) VALUES (?, ?, 1)",
            (ts, app),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_temporal_rhythm_detected(tmp_path: Path):
    # Cursor active at 09:15 UTC (bin 09:00-09:30 local/UTC) on 5 distinct days
    data = []
    days = [f"2026-07-0{i}" for i in range(1, 6)]
    for day in days:
        data.append((day, "Cursor", 9, 15))

    db = _create_rhythm_db(tmp_path, data)
    rhythms = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-06T00:00:00")
    assert len(rhythms) == 1
    r = rhythms[0]
    assert r.kind == "temporal_rhythm"
    assert "Cursor active" in r.label
    assert "5/5 days" in r.label
    assert "regularity 1.00" in r.label


def test_temporal_rhythm_adjacent_bins_merged(tmp_path: Path):
    # Slack active at 09:15 and 09:45 on 4 distinct days
    data = []
    days = [f"2026-07-0{i}" for i in range(1, 5)]
    for day in days:
        data.append((day, "Slack", 9, 15))
        data.append((day, "Slack", 9, 45))

    db = _create_rhythm_db(tmp_path, data)
    rhythms = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-05T00:00:00")
    # Adjacent 30-min bins (09:00-09:30 and 09:30-10:00) should merge into a 1-hour span
    assert len(rhythms) == 1
    r = rhythms[0]
    assert "Slack active" in r.label
    assert "on 4/4 days (regularity 1.00)" in r.label
    # Verify the label represents a merged 1-hour span (e.g. 14:30-15:30)
    assert r.count == 8


def test_temporal_rhythm_not_fired_under_days_threshold(tmp_path: Path):
    # Cursor active on 2 days only (< 3 days required)
    data = [
        ("2026-07-01", "Cursor", 9, 15),
        ("2026-07-02", "Cursor", 9, 15),
    ]
    db = _create_rhythm_db(tmp_path, data)
    rhythms = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-03T00:00:00")
    assert len(rhythms) == 0


def test_temporal_rhythm_not_fired_under_regularity_threshold(tmp_path: Path):
    # Total 10 active days, but Cursor is active at 09:15 on only 3 of 10 days (regularity 0.30 < 0.60)
    data = []
    for i in range(1, 11):
        day = f"2026-07-{i:02d}"
        data.append((day, "Chrome", 14, 0))  # Chrome active every day
        if i <= 3:
            data.append((day, "Cursor", 9, 15))  # Cursor active only on 3 of 10 days

    db = _create_rhythm_db(tmp_path, data)
    rhythms = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-11T00:00:00")
    cursor_rhythms = [r for r in rhythms if "Cursor" in r.label]
    assert len(cursor_rhythms) == 0


def test_temporal_rhythm_empty_db(tmp_path: Path):
    path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN
        );
        """
    )
    conn.close()
    db = Database(str(path))
    rhythms = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-05T00:00:00")
    assert rhythms == []


def test_temporal_rhythm_integration_detect(tmp_path: Path):
    data = [(f"2026-07-0{i}", "Slack", 9, 15) for i in range(1, 5)]
    db = _create_rhythm_db(tmp_path, data)
    patterns = detect(db, "2026-07-01T00:00:00", "2026-07-05T00:00:00")
    rhythms = [p for p in patterns if p.kind == "temporal_rhythm"]
    assert len(rhythms) == 1
