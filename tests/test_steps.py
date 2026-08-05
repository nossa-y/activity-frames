"""get_steps: the one-shot click-by-click drill-down behind an activity frame."""
import json
import sqlite3
from types import SimpleNamespace

from activity_frames.db import Database
from activity_frames.mcp_server import MCPServer

from conftest import ts
from test_mcp import _make_server, _rpc


def _steps_db(tmp_path, *, out_of_order_event=False):
    """A short demonstrated task: portal login -> download -> typed note.

    One Chrome session 17:00-17:02 with frames every ~20s, labeled and
    unlabeled clicks (the unlabeled one resolvable via the elements tree),
    a typed run, and a clipboard paste.
    """
    path = tmp_path / "steps.sqlite"
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
        CREATE TABLE ui_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            event_type TEXT NOT NULL,
            x INTEGER, y INTEGER,
            text_content TEXT,
            app_name TEXT, window_title TEXT, browser_url TEXT,
            element_name TEXT, element_role TEXT,
            element_automation_id TEXT, frame_id INTEGER
        );
        CREATE TABLE elements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            frame_id INTEGER NOT NULL,
            source TEXT NOT NULL DEFAULT 'accessibility',
            role TEXT NOT NULL DEFAULT 'AXButton',
            text TEXT,
            left_bound REAL, top_bound REAL, width_bound REAL, height_bound REAL
        );
        """
    )
    for i, hms in enumerate(["17:00:00", "17:00:20", "17:00:40", "17:01:00",
                             "17:01:20", "17:01:40", "17:02:00"]):
        conn.execute(
            "INSERT INTO frames (timestamp, app_name, window_name, focused, browser_url) "
            "VALUES (?, 'Google Chrome', 'ACME Portal', 1, "
            "'https://portal.acme.test/invoices')",
            (ts(hms),),
        )
    ev = [
        # labeled click straight from the event
        (ts("17:00:05"), "click", 500, 400, None, "Google Chrome", "ACME Portal",
         "https://portal.acme.test/login", "Sign in", "AXButton", "signin-btn", 1),
        # raw mouse-hook duplicate (role '') - must be dropped
        (ts("17:00:05", "500000"), "click", 500, 400, None, "Google Chrome",
         "ACME Portal", None, "", "", None, 1),
        # typed run
        (ts("17:00:12"), "text", None, None, "ap@acme.test", "Google Chrome",
         "ACME Portal", None, None, None, None, 1),
        # unlabeled click, resolvable via elements: center of screen candidates
        (ts("17:00:45"), "click", 864, 558, None, "Google Chrome", "ACME Portal",
         "https://portal.acme.test/invoices", "", "AXGroup", None, 3),
        # clipboard paste
        (ts("17:01:10"), "clipboard", None, None, "INV-2841", "Google Chrome",
         "ACME Portal", None, None, None, None, 4),
        # event from another app inside the window - must be filtered out
        (ts("17:01:15"), "click", 100, 100, None, "Slack", "general - Slack",
         None, "Send", "AXButton", None, 4),
    ]
    conn.executemany(
        "INSERT INTO ui_events (timestamp, event_type, x, y, text_content, "
        "app_name, window_title, browser_url, element_name, element_role, "
        "element_automation_id, frame_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ev,
    )
    # element covering the unlabeled click at ~ (0.5, 0.5) normalized
    conn.execute(
        "INSERT INTO elements (frame_id, role, text, left_bound, top_bound, "
        "width_bound, height_bound) VALUES (3, 'AXButton', 'Download PDF', "
        "0.45, 0.45, 0.1, 0.1)"
    )
    if out_of_order_event:
        conn.execute(
            "INSERT INTO ui_events (timestamp, event_type, app_name, element_name, "
            "element_role) VALUES (?, 'click', 'Google Chrome', 'Earlier', 'AXButton')",
            (ts("17:00:03"),),
        )
    conn.commit()
    conn.close()
    return Database(str(path))


def test_get_steps_end_to_end(tmp_path):
    s = _make_server(_steps_db(tmp_path))
    act = json.loads(
        _rpc(s, "tools/call", {"name": "get_activity",
                               "arguments": {"day": "2026-07-04"}})
        ["result"]["content"][0]["text"]
    )
    frame_id = act["frames"][0]["id"]

    out = json.loads(
        _rpc(s, "tools/call", {"name": "get_steps",
                               "arguments": {"frame": frame_id,
                                             "day": "2026-07-04",
                                             "include_text": True}})
        ["result"]["content"][0]["text"]
    )
    assert out["task"]["frame"] == frame_id
    ops = [st["op"] for st in out["steps"]]
    assert ops == ["click", "type", "click", "paste"]

    click1, typed, click2, paste = out["steps"]
    assert click1["target"] == "Sign in"
    assert click1["role"] == "Button"
    assert click1["automation_id"] == "signin-btn"
    assert click1["url"].startswith("https://portal.acme.test/login")
    assert typed["text"] == "ap@acme.test"
    # unlabeled click resolved through the elements tree
    assert click2["target"] == "Download PDF"
    assert click2.get("resolved") is True
    assert paste["chars"] == len("INV-2841")
    # the Slack stray was filtered; the mouse-hook dupe was dropped
    assert out["step_count"] == 4
    assert out["unresolved_clicks"] == 0


def test_get_steps_text_is_opt_in(tmp_path):
    s = _make_server(_steps_db(tmp_path))
    out = json.loads(
        _rpc(s, "tools/call", {"name": "get_steps",
                               "arguments": {"frame": "f-0000",
                                             "day": "2026-07-04"}})
        ["result"]["content"][0]["text"]
    )
    if "steps" in out:  # frame ids depend on segmentation; tolerate either id
        for st in out["steps"]:
            assert "text" not in st


def test_cli_steps_text_is_opt_in(tmp_path, capsys):
    from activity_frames.cli import main

    db = _steps_db(tmp_path)
    assert main(["steps", "--db", db.path, "--frame", "f-0001",
                 "--day", "2026-07-04"]) == 0
    default = json.loads(capsys.readouterr().out)
    assert all("text" not in step for step in default.get("steps", []))

    assert main(["steps", "--db", db.path, "--frame", "f-0001",
                 "--day", "2026-07-04", "--include-text"]) == 0
    opted_in = json.loads(capsys.readouterr().out)
    assert any("text" in step for step in opted_in.get("steps", []))

    assert main(["steps", "--db", db.path, "--frame", "f-0001",
                 "--day", "2026-07-04", "--no-text"]) == 0
    legacy_optout = json.loads(capsys.readouterr().out)
    assert all("text" not in step for step in legacy_optout.get("steps", []))


def test_get_steps_uses_activity_defaults_and_requested_minimum_duration():
    class RecordingLog:
        def __init__(self):
            self.recent_args = None

        def recent(self, hours, *, min_minutes):
            self.recent_args = (hours, min_minutes)
            return SimpleNamespace(frames=[])

    server = MCPServer()
    log = RecordingLog()
    server._log = log

    out = json.loads(server.get_steps(frame="f-0001", min_minutes=1.25))
    assert "error" in out
    assert log.recent_args == (2.0, 1.25)


def test_steps_are_sorted_by_timestamp_not_insert_order(tmp_path):
    db = _steps_db(tmp_path, out_of_order_event=True)

    from activity_frames.steps import steps_for_frame

    out = steps_for_frame(
        db, "Google Chrome", {"frame_ids": "1..7"}, include_text=False,
    )
    targets = [step["target"] for step in out["steps"] if step["op"] == "click"]
    assert targets[0] == "Earlier"


def test_get_steps_unknown_frame(tmp_path):
    s = _make_server(_steps_db(tmp_path))
    out = json.loads(
        _rpc(s, "tools/call", {"name": "get_steps",
                               "arguments": {"frame": "f-9999",
                                             "day": "2026-07-04"}})
        ["result"]["content"][0]["text"]
    )
    assert "error" in out and "available" in out


def test_find_frame_resolves_task_query(tmp_path):
    from activity_frames.steps import find_frame

    s = _make_server(_steps_db(tmp_path))
    doc = s.log.day("2026-07-04")
    out = find_frame(s.log.db, doc, "get my acme invoice")
    assert "error" not in out
    assert out["frame"] == f"f-{doc.frames[0].index:04d}"
    # "invoice" matched the /invoices URL via the synonym fan-out is not even
    # needed here (direct hit); from_url slices at the first matching URL
    assert out["from_url"] == "login"
    assert out["task_clicks"] == 2  # Sign in + resolved Download PDF


def test_find_frame_requires_full_coverage(tmp_path):
    from activity_frames.steps import find_frame

    s = _make_server(_steps_db(tmp_path))
    doc = s.log.day("2026-07-04")
    # "netflix" never appears anywhere: the frame must NOT match on
    # "billing" alone (full token-group coverage required)
    out = find_frame(s.log.db, doc, "netflix billing")
    assert "error" in out
    # stopword-only queries fail loudly instead of matching everything
    assert "error" in find_frame(s.log.db, doc, "replay my last run again")
