"""Tests for the temporal_rhythms detector (patterns.py, detector #7).

Strategy: build synthetic frame databases that encode known rhythms so
the assertions are deterministic and self-documenting.

Fixtures are standalone (no conftest dependency) so this file can be
run on its own with:
    PYTHONPATH=src python -m pytest tests/test_temporal_rhythm.py -v
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from activity_frames.db import Database
from activity_frames.patterns import (
    MIN_DAYS_FOR_RHYTHM,
    RHYTHM_BIN_MINUTES,
    RHYTHM_REGULARITY_THRESHOLD,
    WorkPattern,
    temporal_rhythms,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")


def _make_db(tmp_path, frames: list[tuple[str, str]]) -> Database:
    """Create a minimal capture DB from a list of (utc_iso_timestamp, app_name) pairs."""
    path = tmp_path / "rhythm_test.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN,
            browser_url TEXT,
            device_name TEXT NOT NULL DEFAULT 'monitor_1'
        );
        """
    )
    for ts, app in frames:
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused) VALUES (?,?,?,1)",
            (ts, app, f"{app} - window"),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def _window(frames: list[tuple[str, str]]) -> tuple[str, str]:
    """Return a [start, end) UTC string window that covers all frames."""
    times = [f[0][:19] for f in frames]
    return times[0], times[-1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTemporalRhythmsDetected:
    """Cases where temporal_rhythms SHOULD fire."""

    def test_strong_rhythm_detected(self, tmp_path):
        """App appearing in the same 30-min bin on 5 consecutive days triggers a pattern."""
        frames = []
        base = datetime(2026, 7, 1, 9, 5, 0, tzinfo=timezone.utc)  # ~09:05 UTC
        for day in range(5):
            for minute_offset in range(0, 25, 5):   # 5 frames per day inside same bin
                ts = base + timedelta(days=day, minutes=minute_offset)
                frames.append((_utc_iso(ts), "Cursor"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)

        assert len(results) >= 1, "Expected at least one temporal_rhythm pattern"
        kinds = {r.kind for r in results}
        assert kinds == {"temporal_rhythm"}

        # count should equal the number of distinct days that hit the bin
        top = results[0]
        assert top.count == 5

    def test_label_format(self, tmp_path):
        """Label contains HH:MM-HH:MM, days fraction, and regularity."""
        frames = []
        base = datetime(2026, 7, 1, 14, 0, 0, tzinfo=timezone.utc)  # 14:00 UTC
        for day in range(4):
            for offset in range(0, 20, 5):
                ts = base + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "Slack"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)

        assert results, "Expected pattern to be detected"
        label = results[0].label
        assert "Slack" in label
        assert "regularity" in label
        # should contain a HH:MM-HH:MM substring
        import re
        assert re.search(r"\d{2}:\d{2}-\d{2}:\d{2}", label), f"No time range in label: {label!r}"

    def test_multiple_apps_multiple_rhythms(self, tmp_path):
        """Two apps with different strong rhythms both appear in results."""
        frames = []
        # App A: 09:00 UTC daily for 5 days
        base_a = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        for day in range(5):
            for offset in range(0, 10, 5):
                ts = base_a + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "AppA"))

        # App B: 15:00 UTC daily for 5 days
        base_b = datetime(2026, 7, 1, 15, 0, 0, tzinfo=timezone.utc)
        for day in range(5):
            for offset in range(0, 10, 5):
                ts = base_b + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "AppB"))

        db = _make_db(tmp_path, sorted(frames))
        start, end = _window(sorted(frames))
        results = temporal_rhythms(db, start, end)

        apps = {r.label.split()[0] for r in results}
        assert "AppA" in apps, "AppA rhythm not detected"
        assert "AppB" in apps, "AppB rhythm not detected"

    def test_regularity_value_within_range(self, tmp_path):
        """Regularity score should be between 0.0 and 1.0 (inclusive)."""
        frames = []
        base = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        # 3 of 4 days → regularity 0.75 (above threshold)
        for day in [0, 1, 2]:
            for offset in range(0, 15, 5):
                ts = base + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "Firefox"))
        # Day 3: different hour (no match)
        ts_other = base + timedelta(days=3, hours=6)
        frames.append((_utc_iso(ts_other), "Firefox"))

        db = _make_db(tmp_path, sorted(frames))
        start, end = _window(sorted(frames))
        results = temporal_rhythms(db, start, end)

        for r in results:
            if "Firefox" in r.label:
                import re
                m = re.search(r"regularity (\d+\.\d+)", r.label)
                assert m, f"regularity not in label: {r.label!r}"
                reg = float(m.group(1))
                assert 0.0 <= reg <= 1.0

    def test_results_sorted_by_regularity_desc(self, tmp_path):
        """Results should be sorted highest regularity first."""
        frames = []
        base = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        # AppA: all 5 days (regularity 1.0)
        for day in range(5):
            ts = base + timedelta(days=day, minutes=2)
            frames.append((_utc_iso(ts), "AppA"))
        # AppB: 3 of 5 days (regularity 0.6, just at threshold)
        for day in [0, 1, 2]:
            ts = base + timedelta(days=day, hours=3, minutes=2)
            frames.append((_utc_iso(ts), "AppB"))

        db = _make_db(tmp_path, sorted(frames))
        start, end = _window(sorted(frames))
        results = temporal_rhythms(db, start, end)

        assert len(results) >= 2
        assert results[0].label.startswith("AppA"), (
            f"Expected AppA first (regularity 1.0), got: {results[0].label!r}"
        )


class TestTemporalRhythmsNotFired:
    """Cases where temporal_rhythms SHOULD NOT emit patterns."""

    def test_too_few_days_no_result(self, tmp_path):
        """Below MIN_DAYS_FOR_RHYTHM days in same bin → no pattern."""
        assert MIN_DAYS_FOR_RHYTHM >= 3, "Test assumes threshold >= 3"
        frames = []
        base = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        for day in range(MIN_DAYS_FOR_RHYTHM - 1):  # one fewer than threshold
            for offset in range(0, 10, 5):
                ts = base + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "AppX"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)

        rhythm_results = [r for r in results if "AppX" in r.label]
        assert not rhythm_results, (
            f"Should not detect rhythm with only {MIN_DAYS_FOR_RHYTHM - 1} days"
        )

    def test_low_regularity_no_result(self, tmp_path):
        """Activity spread thin across bins so no bin hits threshold."""
        frames = []
        base = datetime(2026, 7, 1, tzinfo=timezone.utc)
        # Each of 6 days uses a different 30-min bin → regularity = 1/6 each
        for day in range(6):
            ts = base + timedelta(days=day, hours=day * 2)
            frames.append((_utc_iso(ts), "Wanderer"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)

        wanderer_results = [r for r in results if "Wanderer" in r.label]
        assert not wanderer_results, "Low-regularity activity should not trigger a rhythm"

    def test_empty_db_no_result(self, tmp_path):
        """No frames → no patterns."""
        path = tmp_path / "empty.sqlite"
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE frames (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TIMESTAMP NOT NULL,
                app_name TEXT, window_name TEXT, focused BOOLEAN,
                browser_url TEXT,
                device_name TEXT NOT NULL DEFAULT 'monitor_1'
            );
            """
        )
        conn.commit()
        conn.close()
        db = Database(str(path))
        results = temporal_rhythms(db, "2026-07-01T00:00:00", "2026-07-08T00:00:00")
        assert results == []

    def test_single_day_no_result(self, tmp_path):
        """Only one calendar day → cannot establish a cross-day rhythm."""
        frames = []
        base = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        for offset in range(0, 60, 5):
            ts = base + timedelta(minutes=offset)
            frames.append((_utc_iso(ts), "Terminal"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)
        assert results == [], "Single-day window should produce no temporal rhythms"


class TestTemporalRhythmsIntegration:
    """Smoke test: temporal_rhythms integrates correctly with detect()."""

    def test_detect_includes_temporal_rhythm_kind(self, tmp_path):
        """detect() includes temporal_rhythm results when a rhythm exists."""
        from activity_frames.patterns import detect

        frames = []
        base = datetime(2026, 7, 1, 9, 0, 0, tzinfo=timezone.utc)
        for day in range(5):
            for offset in range(0, 10, 5):
                ts = base + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "Cursor"))

        db = _make_db(tmp_path, sorted(frames))
        start, end = _window(sorted(frames))
        all_patterns = detect(db, start, end)
        kinds = {p.kind for p in all_patterns}
        assert "temporal_rhythm" in kinds

    def test_workpattern_fields_well_formed(self, tmp_path):
        """Every returned WorkPattern has kind, label (str), count (int > 0)."""
        frames = []
        base = datetime(2026, 7, 1, 10, 0, 0, tzinfo=timezone.utc)
        for day in range(4):
            for offset in range(0, 15, 5):
                ts = base + timedelta(days=day, minutes=offset)
                frames.append((_utc_iso(ts), "Zed"))

        db = _make_db(tmp_path, frames)
        start, end = _window(frames)
        results = temporal_rhythms(db, start, end)

        for r in results:
            assert isinstance(r, WorkPattern)
            assert r.kind == "temporal_rhythm"
            assert isinstance(r.label, str) and r.label
            assert isinstance(r.count, int) and r.count > 0
