"""Tests for the communications view (measured comm surfaces + titles)."""
from __future__ import annotations

import sqlite3

import pytest

from activity_frames.communications import COMM_KINDS, surfaces
from activity_frames.db import Database

BASE = "2026-07-06T"
WINDOW = ("2026-07-06T00:00:00", "2026-07-07T00:00:00")


def ts(hhmmss: str) -> str:
    return f"{BASE}{hhmmss}.000000+00:00"


@pytest.fixture()
def comms_db(tmp_path):
    """Capture rows shaped like real data: Gmail tab titles (subjects),
    a native WhatsApp app (with its real leading U+200E format char),
    LinkedIn messaging, and non-communication activity that must be excluded.
    """
    path = tmp_path / "db.sqlite"
    conn = sqlite3.connect(path)
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
    rows = [
        # Gmail: an opened email seen twice, then a second subject once.
        ("17:00:00", "Google Chrome",
         "GetCleed not working - user@gmail.com - Gmail",
         "https://mail.google.com/mail/u/0/#inbox/abc"),
        ("17:00:40", "Google Chrome",
         "GetCleed not working - user@gmail.com - Gmail",
         "https://mail.google.com/mail/u/0/#inbox/abc"),
        ("17:02:00", "Google Chrome",
         "$24.00 payment unsuccessful - user@gmail.com - Gmail",
         "https://mail.google.com/mail/u/0/#inbox/def"),
        # Native WhatsApp with the leading U+200E the real app ships.
        ("17:05:00", "‎WhatsApp", "‎WhatsApp", None),
        ("17:05:30", "‎WhatsApp", "‎WhatsApp", None),
        # LinkedIn messaging (typed by the URL parser, not the app map).
        ("17:10:00", "Google Chrome", "Messaging | LinkedIn",
         "https://www.linkedin.com/messaging/thread/123/"),
        # Non-communication activity: must never appear.
        ("17:20:00", "Cursor", "main.py - project", None),
        ("17:21:00", "Google Chrome", "acme/api: PR #7",
         "https://github.com/acme/api/pull/7"),
        # A comm frame with no usable title: counted, contributes no item.
        ("17:30:00", "Google Chrome", None,
         "https://mail.google.com/mail/u/0/#inbox"),
    ]
    for hms, app, win, url in rows:
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused,"
            " browser_url) VALUES (?,?,?,1,?)",
            (ts(hms), app, win, url),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_surfaces_and_kinds(comms_db):
    out = surfaces(comms_db, *WINDOW)
    by_kind = {(s.kind, s.app) for s in out}
    assert ("email", "Google Chrome") in by_kind
    assert ("messaging", "WhatsApp") in by_kind        # U+200E stripped
    assert ("messaging", "Google Chrome") in by_kind   # linkedin messaging
    # Non-communication activity is excluded entirely.
    assert all("Cursor" != s.app for s in out)
    assert all(s.site != "github.com" for s in out)


def test_gmail_titles_are_subjects(comms_db):
    email = next(s for s in surfaces(comms_db, *WINDOW) if s.kind == "email")
    assert email.site == "mail.google.com"
    texts = [t.text for t in email.titles]
    assert texts == [
        "GetCleed not working - user@gmail.com - Gmail",
        "$24.00 payment unsuccessful - user@gmail.com - Gmail",
    ]
    seen_twice = email.titles[0]
    assert seen_twice.count == 2
    assert ".." in seen_twice.frames          # evidence spans two frame ids
    # Times are local HH:MM:SS (value depends on the machine's timezone).
    assert len(seen_twice.first) == 8 and seen_twice.first.count(":") == 2
    # The titleless inbox frame counts toward the surface, not the items.
    assert email.frames_analyzed == 4


def test_kind_filter(comms_db):
    only_email = surfaces(comms_db, *WINDOW, kinds={"email"})
    assert {s.kind for s in only_email} == {"email"}
    nothing = surfaces(comms_db, *WINDOW, kinds={"notifications"})
    assert nothing == []


def test_deterministic(comms_db):
    a = [s.to_dict() for s in surfaces(comms_db, *WINDOW)]
    b = [s.to_dict() for s in surfaces(comms_db, *WINDOW)]
    assert a == b


def test_max_titles_disclosed_not_silent(comms_db):
    out = surfaces(comms_db, *WINDOW, max_titles=1)
    email = next(s for s in out if s.kind == "email")
    assert len(email.titles) == 1
    assert email.omitted_titles == 1
    assert email.to_dict()["omitted"] == {"titles_beyond_max": 1}


def test_bodies_never_claimed(comms_db):
    d = surfaces(comms_db, *WINDOW)[0].to_dict()
    assert "bodies are not read" in d["scope"]


def test_comm_kinds_frozen():
    assert COMM_KINDS == {"email", "messaging", "messages", "notifications"}


def test_empty_window(comms_db):
    assert surfaces(comms_db, "2026-01-01T00:00:00", "2026-01-02T00:00:00") == []
