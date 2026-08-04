from activity_frames.sessionize import app_ledger, coverage, segments

from activity_frames import build_frames


def test_segments_basic_shape(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    keys = [(s.app, s.domain) for s in segs]
    # Slack flicker merged away: linkedin, cursor, github (after gap).
    assert ("Google Chrome", "linkedin.com") in keys
    assert ("Cursor", None) in keys
    assert ("Google Chrome", "github.com") in keys
    assert ("Slack", None) not in keys  # merged as interruption


def test_flicker_recorded_as_interruption(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    li = next(s for s in segs if s.domain == "linkedin.com")
    assert len(li.interruptions) == 1
    assert li.interruptions[0].app == "Slack"
    assert li.interruptions[0].seconds <= 20


def test_session_gap_creates_new_segment_not_dwell(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    gh = next(s for s in segs if s.domain == "github.com")
    # GitHub session is 15 frames over 14 min; active time must not
    # include the 60-minute away gap before it.
    assert 10 <= gh.active_seconds / 60 <= 15


def test_dwell_cap_limits_sparse_frames(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    cur = next(s for s in segs if s.app == "Cursor")
    # 25 frames at 60s spacing, dwell 60s each (under the 90s cap).
    assert 20 <= cur.active_seconds / 60 <= 26


def test_coverage_gap_detected(fixture_db, day_window):
    cov = coverage(fixture_db, *day_window)
    assert cov.frame_count > 0
    assert any(55 <= g.minutes <= 65 for g in cov.gaps)
    assert cov.distinct_apps == 3  # Chrome, Slack, Cursor... Slack counts here
    assert cov.coverage_pct <= 100


def test_app_ledger_ordering_and_sessions(fixture_db, day_window):
    ledger = app_ledger(fixture_db, *day_window)
    assert ledger[0].app in ("Google Chrome", "Cursor")
    chrome = next(a for a in ledger if a.app == "Google Chrome")
    assert chrome.minutes > 10
    assert chrome.sessions >= 2  # linkedin block + github block


def test_app_ledger_splits_on_app_switch(tmp_path):
    """Cursor -> Chrome -> Cursor within 300s: Cursor should be 2 sessions."""
    import sqlite3
    from activity_frames.db import Database

    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN,
            browser_url TEXT, document_path TEXT,
            device_name TEXT NOT NULL DEFAULT 'monitor_1'
        );
        """
    )
    # Cursor 10:00:00-10:10:00 (every 20s, 30 frames)
    # Chrome  10:10:00-10:20:00 (every 20s, 30 frames)
    # Cursor  10:20:00-10:30:00 (every 20s, 30 frames)
    base = "2026-08-01T"
    t = 0
    for _ in range(30):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused)"
            " VALUES (?, 'Cursor', 'main.py', 1)",
            (f"{base}10:{t//60:02d}:{t%60:02d}.000000+00:00",),
        )
        t += 20
    for _ in range(30):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused)"
            " VALUES (?, 'Google Chrome', 'github.com', 1)",
            (f"{base}10:{t//60:02d}:{t%60:02d}.000000+00:00",),
        )
        t += 20
    for _ in range(30):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused)"
            " VALUES (?, 'Cursor', 'server.py', 1)",
            (f"{base}10:{t//60:02d}:{t%60:02d}.000000+00:00",),
        )
        t += 20
    conn.commit()
    conn.close()

    db = Database(str(path))
    ledger = app_ledger(db, f"{base}00:00:00", f"{base}12:00:00")
    cursor = next(a for a in ledger if a.app == "Cursor")
    chrome = next(a for a in ledger if a.app == "Google Chrome")

    # Chrome had 1 uninterrupted block, so 1 session.
    assert chrome.sessions == 1
    # Cursor was interrupted by Chrome, so 2 distinct sessions.
    assert cursor.sessions == 2


def test_empty_window(fixture_db):
    assert segments(fixture_db, "2020-01-01T00:00:00", "2020-01-02T00:00:00") == []
    cov = coverage(fixture_db, "2020-01-01T00:00:00", "2020-01-02T00:00:00")
    assert cov.frame_count == 0


# ---- break_reason (Issue #7) ----

def test_break_reason_first_segment_is_start(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    assert len(segs) > 0
    assert segs[0].break_reason == "start"


def test_break_reason_context_switch(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    # The LinkedIn -> Cursor transition is a context_switch.
    cursor = next(s for s in segs if s.app == "Cursor")
    assert cursor.break_reason == "context_switch"


def test_break_reason_session_gap(fixture_db, day_window):
    segs = segments(fixture_db, *day_window)
    # The GitHub segment follows a 60-min away gap.
    github = next(s for s in segs if s.domain == "github.com")
    assert github.break_reason == "session_gap"


def test_break_reason_not_in_output_by_default(fixture_db, day_window):
    doc = build_frames(fixture_db, *day_window)
    d = doc.to_dict()
    assert "_debug" not in d


def test_break_reason_in_output_with_debug(fixture_db, day_window):
    doc = build_frames(fixture_db, *day_window, debug=True)
    d = doc.to_dict()
    assert "_debug" in d
    sessionization = d["_debug"]["sessionization"]
    assert isinstance(sessionization, dict)
    assert len(sessionization) == len(doc.frames)
    # Every reason must be one of the known values.
    valid = {"start", "context_switch", "session_gap"}
    for fid, reason in sessionization.items():
        assert fid.startswith("f-")
        assert reason in valid
