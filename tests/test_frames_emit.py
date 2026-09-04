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


def _context_block_legacy(doc, *, max_frames: int = 40) -> str:
    """Pre-optimization legacy implementation of context_block() for golden testing."""
    d = doc.to_dict(False)
    cov = d["coverage"]
    frames = d["frames"]

    if len(frames) > max_frames:
        keep = sorted(frames, key=lambda f: -f["duration_min"])[:max_frames]
        keep_ids = {f["id"] for f in keep}
        dropped = len(frames) - len(keep)
        frames = [f for f in frames if f["id"] in keep_ids]
    else:
        dropped = 0

    day = d["window"].get("day", d["window"]["start_utc"][:10])
    lines = [
        f"USER ACTIVITY ({day}, local time; measured from screen capture, "
        "no interpretation):",
        f"coverage: {cov['first_activity']}-{cov['last_activity']}, "
        f"{cov['active_minutes']} active min, {cov['distinct_apps']} apps",
    ]
    for g in cov.get("gaps", []):
        lines.append(f"away: {g['start']}-{g['end']} ({g['minutes']}m)")
    for f in frames:
        where = f["app"] + (f"/{f['site']}" if f.get("site") else "")
        bits = []
        for p in f.get("pages", [])[:4]:
            b = p["kind"]
            if p.get("entity"):
                b += f":{p['entity']}"
            if p.get("count"):
                b += f" x{p['count']}"
            bits.append(b)
        if not bits and f.get("windows"):
            bits = [f["windows"][0][:60]]
        inp = f.get("input", {})
        if inp.get("keys", 0) > 50:
            bits.append(f"typed ~{inp['keys']} chars")
        lines.append(
            f"- {f['start'][:5]}-{f['end'][:5]} {where} ({f['duration_min']}m): "
            + ("; ".join(bits) if bits else "on screen")
        )
    if dropped:
        lines.append(f"(+{dropped} frames over the size budget omitted)")
    omitted = d.get("omitted", {}).get("below_min_minutes", 0)
    if omitted:
        lines.append(
            f"(+{omitted} brief frames under "
            f"{d['omitted']['min_minutes']} min omitted)"
        )
    return "\n".join(lines)


def test_context_block_golden_equivalence(fixture_db, day_window):
    """Golden equivalence test: verify optimized context_block() matches legacy 1:1."""
    for max_frames in (1, 2, 5, 10, 40):
        for min_minutes in (0.0, 1.0, 5.0):
            for inc_text in (False, True):
                doc = build_frames(
                    fixture_db, *day_window, min_minutes=min_minutes, include_text=inc_text
                )
                actual = context_block(doc, max_frames=max_frames)
                expected = _context_block_legacy(doc, max_frames=max_frames)
                assert actual == expected, (
                    f"Golden mismatch for max_frames={max_frames}, "
                    f"min_minutes={min_minutes}, include_text={inc_text}"
                )
