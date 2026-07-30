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


def test_app_ledger_multiple_sessions_across_app_switches(monkeypatch):
    from activity_frames.sessionize import RawFrame, app_ledger
    import activity_frames.sessionize as sess

    # App A (10m) -> App B (10m) -> App A (10m) within session gap (gap=60s <= 300s)
    raw_frames = []
    t = 0.0
    for _ in range(10):  # Cursor 10m
        raw_frames.append(RawFrame(id=len(raw_frames) + 1, epoch=t, app="Cursor", window="w1", url=None, domain=None))
        t += 60.0
    for _ in range(10):  # Chrome 10m
        raw_frames.append(RawFrame(id=len(raw_frames) + 1, epoch=t, app="Google Chrome", window="w2", url="https://google.com", domain="google.com"))
        t += 60.0
    for _ in range(10):  # Cursor 10m again
        raw_frames.append(RawFrame(id=len(raw_frames) + 1, epoch=t, app="Cursor", window="w3", url=None, domain=None))
        t += 60.0
    raw_frames.append(RawFrame(id=len(raw_frames) + 1, epoch=t, app="Cursor", window="w3", url=None, domain=None))

    monkeypatch.setattr(sess, "load_frames", lambda db, s, e: raw_frames)
    ledger = app_ledger(None, "start", "end")
    cursor = next(a for a in ledger if a.app == "Cursor")
    assert cursor.sessions == 2
    assert cursor.minutes == 20.0
    assert cursor.longest_session_min == 10

