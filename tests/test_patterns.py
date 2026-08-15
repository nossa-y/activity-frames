"""Pattern detectors: a pattern is reported only when it actually repeated."""
import os
import sqlite3
import time
from contextlib import contextmanager

import pytest

from activity_frames.db import Database
from activity_frames.patterns import (
    _GENERIC_ELEMENTS,
    _is_generic,
    action_sequences,
    daily_habits,
    repeated_clicks,
)

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


def _named_clicks_db(tmp_path, clicks):
    """clicks: list of (utc_timestamp, element_name, element_role)."""
    path = tmp_path / "named.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE ui_events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp DATETIME NOT NULL, event_type TEXT NOT NULL, x INTEGER, "
        "y INTEGER, text_content TEXT, app_name TEXT, window_title TEXT, "
        "browser_url TEXT, element_name TEXT, element_role TEXT)"
    )
    for ts, name, role in clicks:
        conn.execute(
            "INSERT INTO ui_events (timestamp, event_type, element_name,"
            " element_role) VALUES (?, 'click', ?, ?)",
            (f"{ts}.000000+00:00", name, role),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


# ---- generic-element filtering (shared by all three click detectors) ----

def test_is_generic_ignores_case_and_padding():
    for name in _GENERIC_ELEMENTS:
        assert _is_generic(name)
        assert _is_generic(name.upper())
        assert _is_generic(name.title())
        assert _is_generic(f"  {name}  ")
    assert not _is_generic("Send")
    assert not _is_generic("")
    assert not _is_generic(None)


def test_repeated_clicks_drops_generic_elements_whatever_the_case(tmp_path):
    """'Scroll Area' is the same generic element as 'scroll area'. A
    case-sensitive SQL NOT IN against a lowercase list let it through."""
    db = _named_clicks_db(
        tmp_path,
        [(f"2026-07-08T12:0{i}:00", "Scroll Area", "AXScrollArea") for i in range(5)]
        + [(f"2026-07-08T13:0{i}:00", "Send", "AXButton") for i in range(5)],
    )
    labels = [p.label for p in repeated_clicks(db, *WINDOW)]
    assert not any("Scroll Area" in label for label in labels)
    assert any("Send" in label for label in labels), "real elements still reported"


@needs_tzset
def test_daily_habits_drops_every_generic_element(tmp_path):
    """daily_habits excluded only 'scroll area' and 'group', so 'cell' was
    reported as a habit while repeated_clicks filtered the very same name."""
    db = _named_clicks_db(
        tmp_path,
        [(f"2026-07-08T02:3{i}:00", "cell", "AXCell") for i in range(3)]
        + [(f"2026-07-09T02:3{i}:00", "cell", "AXCell") for i in range(3)],
    )
    with local_timezone("Asia/Kolkata"):
        assert daily_habits(db, *WINDOW) == []


def test_action_sequences_stands_in_for_generic_elements(tmp_path):
    """Generic elements keep their slot in a sequence but appear as their role,
    so the sequence describes a shape rather than a name that means nothing."""
    db = _named_clicks_db(
        tmp_path,
        [(f"2026-07-08T12:{i:02d}:00", "Scroll Area", "AXScrollArea")
         for i in range(9)],
    )
    labels = [p.label for p in action_sequences(db, *WINDOW)]
    assert labels, "the clicks are still mined as a sequence"
    assert all("Scroll Area" not in label for label in labels)
    assert any("[AXScrollArea]" in label for label in labels)


def test_all_three_detectors_agree_on_what_is_generic(tmp_path):
    """One capture, every generic name in the set: no detector names any of
    them. This is the property the three filters used to disagree on."""
    # A role that shares no substring with any generic name, so the assertion
    # below tests the NAME and is not tripped by the "[AXRole]" stand-in.
    clicks = []
    for slot, name in enumerate(sorted(_GENERIC_ELEMENTS)):
        for day in ("2026-07-08", "2026-07-09"):
            clicks += [(f"{day}T1{slot}:0{i}:00", name.title(), "AXUnknown")
                       for i in range(4)]
    db = _named_clicks_db(tmp_path, clicks)
    reported = " ".join(
        p.label
        for p in repeated_clicks(db, *WINDOW)
        + daily_habits(db, *WINDOW)
        + action_sequences(db, *WINDOW)
    )
    for name in _GENERIC_ELEMENTS:
        assert name.title() not in reported


# ---- local-day bucketing ----

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
