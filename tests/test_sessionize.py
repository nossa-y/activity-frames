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


def test_hour_histogram_partitions_active_minutes(fixture_db, day_window):
    """Every active minute lands in exactly one local hour, so the histogram
    sums to active_minutes rather than double-counting across bucket edges."""
    cov = coverage(fixture_db, *day_window)
    assert cov.hour_histogram, "fixture spans real hours"
    assert sum(cov.hour_histogram.values()) == cov.active_minutes
    assert all(0 <= hour <= 23 for hour in cov.hour_histogram)
    assert all(minutes > 0 for minutes in cov.hour_histogram.values())


def test_hour_histogram_reaches_the_document(fixture_db, day_window):
    """coverage() has always measured this; it is now emitted rather than
    recomputed downstream from every frame."""
    doc = build_frames(fixture_db, *day_window)
    by_hour = doc.to_dict()["coverage"]["active_minutes_by_hour"]
    assert by_hour == coverage(fixture_db, *day_window).hour_histogram
    assert sum(by_hour.values()) == doc.coverage["active_minutes"]


def test_hour_histogram_empty_window_is_empty(fixture_db):
    doc = build_frames(fixture_db, "2020-01-01T00:00:00", "2020-01-02T00:00:00")
    assert doc.to_dict()["coverage"]["active_minutes_by_hour"] == {}


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
    assert github.break_reason.startswith("session_gap")
    assert "> 300s" in github.break_reason


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
    valid_prefixes = ("start", "context_switch", "session_gap")
    for fid, reason in sessionization.items():
        assert fid.startswith("f-")
        assert any(reason.startswith(prefix) for prefix in valid_prefixes)


def test_quantified_debug_reasons_flicker_and_dwell():
    from activity_frames.sessionize import RawFrame, _segment_stream

    # Frame 1 & 2: gap of 150s (> dwell_cap of 90s, <= session_gap of 300s) -> dwell capped
    # Frame 3 & 4: brief flicker to Slack (start 260.0, end 265.0 => span 5s <= 20s)
    # Frame 5: return to Chrome at epoch 365.0 => Slack active_seconds is 90s (dwell capped), but span is 5s
    raw_frames = [
        RawFrame(id=1, epoch=100.0, app="Google Chrome", window="Main", url=None, domain="chrome.com"),
        RawFrame(id=2, epoch=250.0, app="Google Chrome", window="Main", url=None, domain="chrome.com"),
        RawFrame(id=3, epoch=260.0, app="Slack", window="Chat", url=None, domain=None),
        RawFrame(id=4, epoch=265.0, app="Slack", window="Chat", url=None, domain=None),
        RawFrame(id=5, epoch=365.0, app="Google Chrome", window="Main", url=None, domain="chrome.com"),
    ]
    segs = _segment_stream(raw_frames, dwell_cap=90.0, session_gap=300.0, merge_flicker=20.0)
    assert len(segs) == 1
    notes = segs[0].debug_notes
    assert any("dwell capped: 90s" in n for n in notes)
    # The note must quote the span (5s), not the active interruption seconds (90s)
    assert any("merged flicker: Slack 5s <= 20s" in n for n in notes)


