"""Robustness: the inputs a stranger's machine will actually throw.

Empty databases, missing tables, malformed timestamps, garbage URLs,
and time windows that cross boundaries. None of these should raise; the
compiler must degrade to an honest empty/partial document.
"""
import sqlite3

import pytest

from activity_frames import build_day, build_frames, context_block, to_json, to_yaml
from activity_frames._time import (
    hours_ago_window_utc,
    local_day_window_utc,
    parse_epoch,
)
from activity_frames.db import Database, RecorderDBNotFound


def _empty_db(tmp_path, with_tables=True):
    path = tmp_path / "empty.sqlite"
    conn = sqlite3.connect(path)
    if with_tables:
        conn.executescript(
            """
            CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP,
                app_name TEXT, window_name TEXT, browser_url TEXT, focused BOOLEAN);
            CREATE TABLE ui_events (id INTEGER PRIMARY KEY, timestamp DATETIME,
                event_type TEXT, x INT, y INT, text_content TEXT, app_name TEXT,
                element_name TEXT, element_role TEXT);
            """
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_missing_db_raises_clean(tmp_path):
    with pytest.raises(RecorderDBNotFound):
        Database(str(tmp_path / "nope.sqlite"))


def test_empty_db_yields_empty_document(tmp_path):
    db = _empty_db(tmp_path)
    doc = build_day(db, "2026-07-04")
    assert doc.frames == []
    assert doc.coverage["active_minutes"] == 0
    assert doc.schema_version == 1
    # emitters must not choke on an empty doc
    assert "schema_version" in to_yaml(doc)
    assert "frames" in to_json(doc)
    assert "USER ACTIVITY" in context_block(doc)


def test_db_without_ui_events_table(tmp_path):
    """A capture engine that never recorded input still yields frames."""
    path = tmp_path / "framesonly.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP, "
        "app_name TEXT, window_name TEXT, browser_url TEXT, focused BOOLEAN)"
    )
    conn.execute(
        "INSERT INTO frames (timestamp, app_name, window_name, browser_url, focused) "
        "VALUES ('2026-07-04T17:00:00.000000+00:00', 'Safari', 'Home', "
        "'https://github.com/a/b', 1)"
    )
    conn.execute(
        "INSERT INTO frames (timestamp, app_name, window_name, browser_url, focused) "
        "VALUES ('2026-07-04T17:01:00.000000+00:00', 'Safari', 'Home', "
        "'https://github.com/a/b', 1)"
    )
    conn.commit()
    conn.close()
    db = Database(str(path))
    doc = build_frames(db, "2026-07-04T00:00:00", "2026-07-05T00:00:00")
    assert len(doc.frames) == 1
    assert doc.frames[0].app == "Safari"
    # no input table -> input counts are all zero, not an error
    assert doc.frames[0].input.clicks == 0


def test_malformed_timestamps_are_skipped(tmp_path):
    path = tmp_path / "bad.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP, "
        "app_name TEXT, window_name TEXT, browser_url TEXT, focused BOOLEAN)"
    )
    for ts in ("not-a-timestamp", "", "2026-13-45T99:99:99", None):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, focused) VALUES (?, 'App', 1)",
            (ts,),
        )
    # one good row so the day is not entirely empty
    conn.execute(
        "INSERT INTO frames (timestamp, app_name, window_name, focused) "
        "VALUES ('2026-07-04T17:00:00.000000+00:00', 'GoodApp', 'W', 1)"
    )
    conn.commit()
    conn.close()
    db = Database(str(path))
    doc = build_frames(db, "2026-07-04T00:00:00", "2026-07-05T00:00:00")
    # must not raise; malformed rows simply do not contribute
    assert all(f.app != "App" or f.start != "?" for f in doc.frames)


def test_unparseable_event_timestamps_are_not_attributed(tmp_path):
    """An event whose timestamp will not parse is dropped, not pinned to the
    window's first frame.

    The SQL window is a string comparison, so a stamp like
    "2026-07-04T99:99:99" is inside the range but yields epoch 0.0 -- and
    nearest_index(frame_epochs, 0.0) resolves to index 0. Left unguarded, a
    click made in one app is attributed to whichever app happened to be open
    first that day, at high confidence if it carried a native label.
    """
    from activity_frames.enrich import enrich_events

    path = tmp_path / "badevent.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (id INTEGER PRIMARY KEY, timestamp TIMESTAMP,
            app_name TEXT, window_name TEXT, browser_url TEXT, focused BOOLEAN);
        CREATE TABLE ui_events (id INTEGER PRIMARY KEY, timestamp DATETIME,
            event_type TEXT, x INT, y INT, text_content TEXT, app_name TEXT,
            element_name TEXT, element_role TEXT);
        """
    )
    for ts, app in (("09:00:00", "Mail"), ("15:00:00", "Chrome")):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused) "
            "VALUES (?, ?, 'W', 1)",
            (f"2026-07-04T{ts}.000000+00:00", app),
        )
    for bad in ("2026-07-04T99:99:99", "2026-07-04T10:00"):
        conn.execute(
            "INSERT INTO ui_events (timestamp, event_type, app_name, "
            "element_name, element_role) VALUES (?, 'click', 'Chrome', "
            "'Merge', 'AXButton')",
            (bad,),
        )
    conn.execute(
        "INSERT INTO ui_events (timestamp, event_type, app_name, element_name, "
        "element_role) VALUES ('2026-07-04T15:00:01.000000+00:00', 'click', "
        "'Chrome', 'Good', 'AXButton')"
    )
    conn.commit()
    conn.close()

    events = enrich_events(Database(str(path)), "2026-07-04T00:00:00",
                           "2026-07-05T00:00:00")
    assert [e.label for e in events] == ["Good"]
    assert all(e.epoch > 0 for e in events)
    # nothing was parked on the first frame of the day
    assert not any(e.app == "Mail" for e in events)


def test_parse_epoch_never_raises():
    for junk in ["", "x", "2026", "not-a-date", "2026-99-99T00:00:00", None or ""]:
        assert parse_epoch(junk) == 0.0
    assert parse_epoch("2026-07-04T17:00:00") > 0
    assert parse_epoch("2026-07-04T17:00:00.123456+00:00") > 0


def test_time_windows_are_ordered():
    s, e = local_day_window_utc("2026-07-04")
    assert s < e
    s2, e2 = hours_ago_window_utc(3)
    assert s2 < e2


def test_bad_day_string_raises_valueerror():
    with pytest.raises(ValueError):
        local_day_window_utc("not-a-day")


def test_read_only_connection_rejects_writes(fixture_db):
    with pytest.raises(sqlite3.OperationalError):
        fixture_db._conn.execute("INSERT INTO frames (timestamp) VALUES ('x')")


def test_large_min_minutes_filters_everything(fixture_db, day_window):
    doc = build_frames(fixture_db, *day_window, min_minutes=10_000)
    assert doc.frames == []
    # coverage still reports the underlying capture
    assert doc.coverage["frames_analyzed"] > 0
