import sqlite3

from activity_frames.db import Database
from activity_frames.enrich import (
    _TOL_PX,
    _resolve_click,
    decode_text,
    enrich_events,
    nearest_index,
)


def test_layout_decode_azerty():
    # "hello zorld" recorded on AZERTY hardware decoded via the map:
    # z -> w, so "zorld" becomes "world"; plain letters pass through.
    assert decode_text("hello zorld", "azerty") == "hello world"
    assert decode_text("qm", "azerty") == "a,"  # a<->q swap, m -> ','


def test_layout_none_is_identity():
    assert decode_text("hello zorld", None) == "hello zorld"


def test_nearest_index():
    epochs = [10.0, 20.0, 30.0]
    assert nearest_index(epochs, 9.0) == 0
    assert nearest_index(epochs, 14.0) == 0
    assert nearest_index(epochs, 16.0) == 1
    assert nearest_index(epochs, 100.0) == 2
    assert nearest_index([], 5.0) is None


SCREEN = dict(screen_w=1728.0, screen_h=1117.0)  # fixture coords assume this


def test_enrich_attributes_app_from_frames(fixture_db, day_window):
    events = enrich_events(fixture_db, *day_window, **SCREEN)
    assert events, "should enrich fixture events"
    # Every event should be attributed to a frame.
    assert all(e.frame_id is not None for e in events)
    clicks = [e for e in events if e.event_type == "click"]
    assert all(e.app for e in clicks)


def test_click_resolution_from_elements(fixture_db, day_window):
    events = enrich_events(fixture_db, *day_window, **SCREEN)
    anon = [e for e in events if e.event_type == "click" and e.resolution != "native"]
    assert anon, "fixture has one anonymous click"
    resolved = anon[0]
    # The fixture element 'Message' covers the click point.
    assert resolved.label == "Message"
    assert resolved.resolution in ("exact", "tolerance")


def test_native_labels_kept_high_confidence(fixture_db, day_window):
    events = enrich_events(fixture_db, *day_window, **SCREEN)
    native = [e for e in events if e.resolution == "native"]
    assert native
    assert all(e.confidence == "high" for e in native)
    assert all(e.label == "Connect" for e in native)


def test_click_rescued_via_element_bearing_neighbor(fixture_db, day_window):
    """A click whose nearest frame has no element tree resolves against a
    neighboring frame (within the rescue window) that has one."""
    events = enrich_events(fixture_db, *day_window, **SCREEN)
    rescued = [
        e for e in events
        if e.event_type == "click" and e.label == "Follow"
    ]
    assert rescued, "click at 17:15:01 should resolve via the 17:15:03 frame"
    assert rescued[0].resolution in ("exact", "tolerance")


def test_rescue_respects_window(fixture_db, day_window):
    """With the rescue window shrunk below the 2s gap, the click stays
    unresolved instead of borrowing a too-distant frame."""
    events = enrich_events(fixture_db, *day_window, element_rescue_window=0.5, **SCREEN)
    at_15 = [
        e for e in events
        if e.event_type == "click" and abs(e.epoch % 3600 - 901) < 2  # 17:15:01
        and e.label == "Follow"
    ]
    assert not at_15


def _one_element_db(tmp_path, *, left, top, width, height):
    """One frame carrying one element box, for click-geometry tests."""
    path = tmp_path / "elements.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL, app_name TEXT, window_name TEXT,
            focused BOOLEAN, browser_url TEXT);
        CREATE TABLE elements (id INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_id INTEGER NOT NULL, source TEXT NOT NULL DEFAULT 'accessibility',
            role TEXT NOT NULL DEFAULT 'AXButton', text TEXT,
            left_bound REAL, top_bound REAL, width_bound REAL, height_bound REAL);
        """
    )
    conn.execute(
        "INSERT INTO frames (timestamp, app_name, window_name, focused)"
        " VALUES ('2026-07-04T17:00:00.000000+00:00', 'App', 'W', 1)"
    )
    conn.execute(
        "INSERT INTO elements (frame_id, text, left_bound, top_bound,"
        " width_bound, height_bound) VALUES (1, 'Btn', ?, ?, ?, ?)",
        (left, top, width, height),
    )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_tolerance_ring_is_symmetric_in_pixels(tmp_path):
    """The tolerance ring is specified in logical pixels, so a near-miss the
    same distance past the edge must resolve on either axis.

    Element bounds are normalized per axis (x by width, y by height); a single
    screen_w-normalized tolerance would shrink the vertical ring by the display
    aspect ratio and drop these clicks to the coarse zone branch instead.
    """
    w, h = SCREEN["screen_w"], SCREEN["screen_h"]
    db = _one_element_db(tmp_path, left=0.40, top=0.40, width=0.20, height=0.10)
    near = _TOL_PX - 10  # inside the ring, outside the box

    past_right = _resolve_click(db, 1, 0.60 * w + near, 0.45 * h, {}, w, h)
    past_bottom = _resolve_click(db, 1, 0.50 * w, 0.50 * h + near, {}, w, h)

    assert past_right == ("Btn", "tolerance")
    assert past_bottom == ("Btn", "tolerance")


def test_tolerance_ring_still_has_an_edge(tmp_path):
    """Well outside the ring on either axis, resolution falls through to the
    coarse zone branch rather than claiming a nearby element."""
    w, h = SCREEN["screen_w"], SCREEN["screen_h"]
    db = _one_element_db(tmp_path, left=0.40, top=0.40, width=0.20, height=0.10)
    far = _TOL_PX * 3

    assert _resolve_click(db, 1, 0.60 * w + far, 0.45 * h, {}, w, h)[1] == "zone"
    assert _resolve_click(db, 1, 0.50 * w, 0.50 * h + far, {}, w, h)[1] == "zone"


def test_text_excluded_by_default(fixture_db, day_window):
    events = enrich_events(fixture_db, *day_window, **SCREEN)
    assert all(e.text is None for e in events)
    with_text = enrich_events(fixture_db, *day_window, include_text=True, layout="azerty", **SCREEN)
    texts = [e.text for e in with_text if e.text]
    assert "hello world" in texts
