import io
import json

from activity_frames.mcp_server import MCPServer


def _rpc(server, method, params=None, rid=1):
    return server.handle({"jsonrpc": "2.0", "id": rid, "method": method,
                          "params": params or {}})


def _make_server(fixture_db):
    s = MCPServer()
    # Inject an ActivityLog bound to the fixture DB.
    from activity_frames import ActivityLog

    log = ActivityLog.__new__(ActivityLog)
    log.db = fixture_db
    log.layout = None
    log.min_minutes = 0.5
    s._log = log
    return s


def test_initialize_and_tools_list(fixture_db):
    s = _make_server(fixture_db)
    init = _rpc(s, "initialize", {"protocolVersion": "2024-11-05"})
    assert init["result"]["serverInfo"]["name"] == "activity-frames"
    assert s.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    tools = _rpc(s, "tools/list")["result"]["tools"]
    names = {t["name"] for t in tools}
    assert names == {"get_context", "get_activity", "get_day_summary",
                 "get_patterns", "get_communications"}


def test_tool_call_get_activity(fixture_db):
    s = _make_server(fixture_db)
    resp = _rpc(s, "tools/call", {
        "name": "get_activity",
        "arguments": {"day": "2026-07-04"},
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["schema_version"] == 1
    # Frames exist unless the fixture day is empty in this tz; window
    # queries are UTC so the fixture day always matches.
    assert isinstance(payload["frames"], list)


def test_unknown_tool_and_method(fixture_db):
    s = _make_server(fixture_db)
    bad = _rpc(s, "tools/call", {"name": "drop_tables", "arguments": {}})
    assert "error" in bad
    unknown = _rpc(s, "no/such/method")
    assert unknown["error"]["code"] == -32601


def test_serve_loop_over_stdio(fixture_db):
    s = _make_server(fixture_db)
    lines = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        "not json at all",
    ]
    out = io.StringIO()
    s.serve(stdin=io.StringIO("\n".join(lines) + "\n"), stdout=out)
    responses = [json.loads(l) for l in out.getvalue().strip().splitlines()]
    assert len(responses) == 3  # init, list, parse error (notification silent)
    assert responses[0]["result"]["serverInfo"]["name"] == "activity-frames"
    assert responses[-1]["error"]["code"] == -32700


def test_tool_call_get_communications(fixture_db):
    s = _make_server(fixture_db)
    # The fixture's rows are 17:00-19:00 UTC on 2026-07-04; "day" is a LOCAL
    # day, so compute the local day that contains those instants — the naive
    # "2026-07-04" excludes them east of UTC+7.
    from datetime import datetime, timezone

    day = (datetime(2026, 7, 4, 17, 10, tzinfo=timezone.utc)
           .astimezone().strftime("%Y-%m-%d"))
    resp = _rpc(s, "tools/call", {
        "name": "get_communications",
        "arguments": {"day": day},
    })
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert isinstance(payload, list)
    # The fixture's Slack flicker is a native messaging surface.
    slack = next(p for p in payload if p["app"] == "Slack")
    assert slack["kind"] == "messaging"
    assert slack["titles"][0]["text"] == "general - Slack"
    assert "bodies are not read" in slack["scope"]
