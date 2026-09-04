import json

from activity_frames.emit import context_block, to_json, to_markdown, to_yaml
from activity_frames.frames import SCHEMA_VERSION, build_frames


def _doc(fixture_db, day_window, **kw):
    return build_frames(fixture_db, *day_window, **kw)


def test_document_shape(fixture_db, day_window):
    doc = _doc(fixture_db, day_window)
    d = doc.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["source"]["recorder"] == "nocta-recorder"
    assert d["coverage"]["frames_analyzed"] > 0
    assert d["frames"], "should produce frames"
    assert d["blind_spots"]


def test_frame_fields(fixture_db, day_window):
    doc = _doc(fixture_db, day_window)
    li = next(f for f in doc.frames if f.site == "linkedin.com")
    assert li.duration_min > 5
    assert li.evidence["frame_ids"]
    kinds = {p.kind for p in li.pages}
    assert "people_search" in kinds
    assert "profile" in kinds
    # Page views aggregate: two distinct profiles, each one entry.
    profiles = [p for p in li.pages if p.kind == "profile"]
    assert {p.entity for p in profiles} == {"jane-doe", "john-smith"}


def test_input_counts_attached(fixture_db, day_window):
    doc = _doc(fixture_db, day_window)
    cur = next(f for f in doc.frames if f.app == "Cursor")
    assert cur.input.keystrokes >= 2  # 2 key events + text chars
    assert cur.input.copies == 1


def test_text_snippets_opt_in(fixture_db, day_window):
    default = _doc(fixture_db, day_window)
    cur = next(f for f in default.frames if f.app == "Cursor")
    assert cur.input.text_snippets == []
    with_text = _doc(fixture_db, day_window, include_text=True, layout="azerty")
    cur2 = next(f for f in with_text.frames if f.app == "Cursor")
    assert any("hello world" in s for s in cur2.input.text_snippets)
    # And even then, JSON excludes text unless the emitter is asked too.
    d = json.loads(to_json(with_text))
    f = next(x for x in d["frames"] if x["app"] == "Cursor")
    assert "text" not in f.get("input", {})
    d2 = json.loads(to_json(with_text, include_input_text=True))
    f2 = next(x for x in d2["frames"] if x["app"] == "Cursor")
    assert f2["input"]["text"]


def test_min_minutes_filter(fixture_db, day_window):
    all_frames = _doc(fixture_db, day_window, min_minutes=0.0).frames
    big_frames = _doc(fixture_db, day_window, min_minutes=5.0).frames
    assert len(big_frames) <= len(all_frames)
    assert all(f.duration_min >= 5.0 for f in big_frames)


def test_emitters_produce_output(fixture_db, day_window):
    doc = _doc(fixture_db, day_window)
    j = json.loads(to_json(doc))
    assert j["frames"]
    y = to_yaml(doc)
    assert "schema_version" in y
    md = to_markdown(doc)
    assert "| # |" in md.splitlines()[4] or "| # |" in md
    ctx = context_block(doc)
    assert "USER ACTIVITY" in ctx
    assert "linkedin.com" in ctx


def test_every_emitter_discloses_frames_dropped_by_min_minutes(fixture_db, day_window):
    """A filtered view must say it is filtered, in every format.

    --min-minutes defaults to 0.5, so the default CLI invocation is already
    filtering; a markdown table that omits the disclosure reads as the whole
    day. JSON and the context block have always said so.
    """
    # The fixture's frames run 14.0 / 19.7 / 24.0 min, so a 15 min floor drops
    # exactly one and leaves a non-empty table to disclose against.
    doc = _doc(fixture_db, day_window, min_minutes=15.0)
    assert doc.omitted_below_min == 1
    assert doc.frames, "the surviving frames still render"

    assert json.loads(to_json(doc))["omitted"] == {
        "below_min_minutes": 1,
        "min_minutes": 15.0,
    }
    assert "(+1 brief frames under 15.0 min omitted)" in context_block(doc)
    assert "**Omitted:** 1 frames under 15.0 min" in to_markdown(doc)


def test_unfiltered_markdown_has_no_omitted_footer(fixture_db, day_window):
    """Nothing dropped, nothing claimed."""
    doc = _doc(fixture_db, day_window, min_minutes=0.0)
    assert doc.omitted_below_min == 0
    assert "**Omitted:**" not in to_markdown(doc)


def test_context_block_respects_max_frames(fixture_db, day_window):
    doc = _doc(fixture_db, day_window)
    ctx = context_block(doc, max_frames=1)
    frame_lines = [l for l in ctx.splitlines() if l.startswith("- ")]
    assert len(frame_lines) == 1
    assert "omitted" in ctx


def test_pages_for_segment_revisit_then_dwell():
    from activity_frames.frames import _pages_for_segment
    from activity_frames.sessionize import RawFrame, Segment

    url_a = "https://github.com/nossa-y/activity-frames"
    url_b = "https://github.com/nossa-y/activity-frames/issues/31"
    raw_frames = [
        RawFrame(id=1, epoch=100.0, app="Google Chrome", window="Code", url=url_a, domain="github.com"),
        RawFrame(id=2, epoch=110.0, app="Google Chrome", window="Issues", url=url_b, domain="github.com"),
        RawFrame(id=3, epoch=120.0, app="Google Chrome", window="Code", url=url_a, domain="github.com"),
        RawFrame(id=4, epoch=130.0, app="Google Chrome", window="Code", url=url_a, domain="github.com"),
    ]
    seg = Segment(app="Google Chrome", domain="github.com", start_epoch=100.0, end_epoch=130.0, frames=raw_frames)
    pages = _pages_for_segment(seg)

    assert len(pages) == 2
    page_a = next(p for p in pages if p.kind == "repo")
    page_b = next(p for p in pages if p.kind == "issue")
    assert page_a.count == 3
    assert page_b.count == 1


