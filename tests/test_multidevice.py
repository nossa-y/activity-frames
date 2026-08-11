"""Multi-monitor correctness: simultaneous capture streams must not
shred each other's sessions or double-count input events."""
import sqlite3

from activity_frames import build_frames
from activity_frames.db import Database
from activity_frames.sessionize import segments

BASE = "2026-07-04T"


def ts(hhmmss: str) -> str:
    return f"{BASE}{hhmmss}.000000+00:00"


def _two_monitor_db(tmp_path):
    """10 minutes: Cursor on monitor_1 and Chrome on monitor_2, frames
    interleaved every ~10s, plus 100 keystrokes attributed near monitor_1."""
    path = tmp_path / "mm.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN,
            browser_url TEXT, device_name TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE ui_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL, event_type TEXT NOT NULL,
            x INT, y INT, text_content TEXT, app_name TEXT,
            element_name TEXT, element_role TEXT
        );
        """
    )
    for sec in range(0, 600, 10):
        hh, rem = divmod(sec, 3600)
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused, device_name)"
            " VALUES (?, 'Cursor', 'main.py', 1, 'monitor_1')",
            (ts(f"17:{rem // 60:02d}:{rem % 60:02d}"),),
        )
    for sec in range(5, 600, 10):  # offset by 5s: perfectly interleaved
        rem = sec
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused,"
            " browser_url, device_name)"
            " VALUES (?, 'Google Chrome', 'Docs', 1,"
            " 'https://docs.google.com/document/d/x', 'monitor_2')",
            (ts(f"17:{rem // 60:02d}:{rem % 60:02d}"),),
        )
    # 100 keystrokes across the window
    for i in range(100):
        sec = 3 + i * 5
        conn.execute(
            "INSERT INTO ui_events (timestamp, event_type, app_name)"
            " VALUES (?, 'key', 'Cursor')",
            (ts(f"17:{sec // 60:02d}:{sec % 60:02d}"),),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_interleaved_monitors_do_not_shred_sessions(tmp_path):
    db = _two_monitor_db(tmp_path)
    segs = segments(db, "2026-07-04T00:00:00", "2026-07-05T00:00:00")
    # One continuous segment per monitor, not hundreds of flickers.
    assert len(segs) == 2
    apps = {s.app for s in segs}
    assert apps == {"Cursor", "Google Chrome"}
    for s in segs:
        assert not s.interruptions, "interleaving must not fake interruptions"
        assert s.active_seconds > 500  # ~10 min each, dwell within own stream


def test_input_events_counted_exactly_once(tmp_path):
    db = _two_monitor_db(tmp_path)
    doc = build_frames(db, "2026-07-04T00:00:00", "2026-07-05T00:00:00")
    total_keys = sum(f.input.keystrokes for f in doc.frames)
    assert total_keys == 100, f"expected 100 keystrokes once, got {total_keys}"


def test_flicker_never_merges_across_session_gap(tmp_path):
    """A -> gap(45min) -> B(10s) -> A must stay separate segments."""
    path = tmp_path / "gap.sqlite"
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
    rows = [
        ("10:00:00", "Chrome"), ("10:00:30", "Chrome"), ("10:01:00", "Chrome"),
        # 45-minute away gap
        ("10:46:00", "Slack"), ("10:46:08", "Slack"),   # 8s flicker after gap
        ("10:46:20", "Chrome"), ("10:46:50", "Chrome"),
    ]
    for hms, app in rows:
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, focused) VALUES (?, ?, 1)",
            (f"2026-07-04T{hms}.000000+00:00", app),
        )
    conn.commit()
    conn.close()
    db = Database(str(path))
    segs = segments(db, "2026-07-04T00:00:00", "2026-07-05T00:00:00")
    chrome_segs = [s for s in segs if s.app == "Chrome"]
    assert len(chrome_segs) == 2, "flicker merge must not bridge the 45min gap"
    # No Chrome segment may span the gap
    for s in chrome_segs:
        assert s.wall_seconds() < 600


def test_device_debug_notes_only_emitted_when_multi_device(tmp_path):
    # 1. Multi-monitor DB: contains device: monitor_1 and device: monitor_2
    db_multi = _two_monitor_db(tmp_path)
    doc_multi = build_frames(db_multi, "2026-07-04T00:00:00", "2026-07-05T00:00:00", debug=True)
    reasons_multi = list(doc_multi.to_dict()["_debug"]["sessionization"].values())
    assert any("device: monitor_1" in r for r in reasons_multi)
    assert any("device: monitor_2" in r for r in reasons_multi)

    # 2. Single-monitor DB: device: note should NOT be present
    path_single = tmp_path / "single.sqlite"
    conn = sqlite3.connect(path_single)
    conn.executescript(
        """
        CREATE TABLE frames (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP NOT NULL,
            app_name TEXT, window_name TEXT, focused BOOLEAN,
            browser_url TEXT, device_name TEXT NOT NULL DEFAULT 'monitor_1'
        );
        """
    )
    conn.execute(
        "INSERT INTO frames (timestamp, app_name, focused) VALUES ('2026-07-04T10:00:00.000000+00:00', 'Chrome', 1)"
    )
    conn.commit()
    conn.close()

    db_single = Database(str(path_single))
    doc_single = build_frames(db_single, "2026-07-04T00:00:00", "2026-07-05T00:00:00", debug=True)
    reasons_single = list(doc_single.to_dict()["_debug"]["sessionization"].values())
    assert not any("device:" in r for r in reasons_single)

