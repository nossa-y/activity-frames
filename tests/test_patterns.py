"""Pattern detectors: a pattern is reported only when it actually repeated."""
import os
import sqlite3
import time
from contextlib import contextmanager

import pytest

from activity_frames.db import Database
from activity_frames.patterns import daily_habits

WINDOW = ("2026-07-01T00:00:00", "2026-07-15T00:00:00")

needs_tzset = pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="time.tzset() is Unix-only"
)


@contextmanager
def local_timezone(name):
    """Run a block with the process local timezone pinned, then restore it."""
    previous = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        time.tzset()


def _clicks_db(tmp_path, utc_timestamps, name="Standup"):
    path = tmp_path / "clicks.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ui_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp DATETIME NOT NULL, event_type TEXT NOT NULL, x INTEGER, "
        "y INTEGER, text_content TEXT, app_name TEXT, window_title TEXT, "
        "browser_url TEXT, element_name TEXT, element_role TEXT)"
    )
    for ts in utc_timestamps:
        conn.execute(
            "INSERT INTO ui_events (timestamp, event_type, element_name,"
            " element_role) VALUES (?, 'click', ?, 'AXButton')",
            (f"{ts}.000000+00:00", name),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


@needs_tzset
def test_one_local_day_is_not_a_habit(tmp_path):
    """Clicks spread across a single LOCAL day never make a habit, even when
    that day straddles a UTC boundary.

    In Asia/Kolkata (UTC+5:30) these six clicks are all on Wed 8 Jul 2026
    local -- 03:00 and 08:00 -- but they fall on two different UTC dates.
    Bucketing by UTC would count "2 days" and clear the days >= 2 bar.
    """
    db = _clicks_db(
        tmp_path,
        [
            "2026-07-07T21:30:00", "2026-07-07T21:31:00", "2026-07-07T21:32:00",
            "2026-07-08T02:30:00", "2026-07-08T02:31:00", "2026-07-08T02:32:00",
        ],
    )
    with local_timezone("Asia/Kolkata"):
        assert daily_habits(db, *WINDOW) == []


@needs_tzset
def test_two_local_days_is_a_habit(tmp_path):
    """The same six clicks, moved so they land on two distinct LOCAL days,
    are still detected -- the fix narrows nothing that was genuinely repeated."""
    db = _clicks_db(
        tmp_path,
        [
            "2026-07-08T02:30:00", "2026-07-08T02:31:00", "2026-07-08T02:32:00",
            "2026-07-09T02:30:00", "2026-07-09T02:31:00", "2026-07-09T02:32:00",
        ],
    )
    with local_timezone("Asia/Kolkata"):
        habits = daily_habits(db, *WINDOW)
    assert len(habits) == 1
    assert habits[0].kind == "daily_habit"
    assert habits[0].count == 6
    assert "2 days" in habits[0].label


@needs_tzset
def test_per_day_click_floor_still_applies(tmp_path):
    """A day with fewer than MIN_CLICKS_PER_DAY clicks does not count as a day
    (the floor the SQL HAVING clause used to enforce)."""
    db = _clicks_db(
        tmp_path,
        [
            "2026-07-08T02:30:00", "2026-07-08T02:31:00", "2026-07-08T02:32:00",
            "2026-07-09T02:30:00", "2026-07-09T02:31:00",  # only 2 -> below floor
        ],
    )
    with local_timezone("Asia/Kolkata"):
        assert daily_habits(db, *WINDOW) == []


@needs_tzset
def test_habit_detection_is_timezone_relative(tmp_path):
    """The same capture data yields different habits in different timezones,
    because "a day" is the user's day. In UTC these six clicks span two dates;
    in Asia/Kolkata they are one evening."""
    stamps = [
        "2026-07-07T21:30:00", "2026-07-07T21:31:00", "2026-07-07T21:32:00",
        "2026-07-08T02:30:00", "2026-07-08T02:31:00", "2026-07-08T02:32:00",
    ]
    db = _clicks_db(tmp_path, stamps)
    with local_timezone("UTC"):
        assert len(daily_habits(db, *WINDOW)) == 1
    with local_timezone("Asia/Kolkata"):
        assert daily_habits(db, *WINDOW) == []


@needs_tzset
def test_daily_habits_skips_malformed_timestamps(tmp_path):
    """Rows the time parser cannot read are dropped, not bucketed at epoch 0.

    The SQL range is a string comparison, so a value like "2026-07-08T99:99:99"
    passes it and reaches the parser. Left unchecked those rows would all land
    on the same 1970 local day and invent a third day for the habit.
    """
    db = _clicks_db(
        tmp_path,
        [
            "2026-07-08T02:30:00", "2026-07-08T02:31:00", "2026-07-08T02:32:00",
            "2026-07-09T02:30:00", "2026-07-09T02:31:00", "2026-07-09T02:32:00",
            "2026-07-10T99:99:99", "2026-07-10T99:99:98", "2026-07-10T99:99:97",
        ],
    )
    with local_timezone("Asia/Kolkata"):
        habits = daily_habits(db, *WINDOW)
    assert len(habits) == 1
    assert "2 days" in habits[0].label
    assert habits[0].count == 6
