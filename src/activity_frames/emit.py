"""Output formats: JSON, YAML, markdown, and the agent context block.

The context block is the flagship output: a compact, token-efficient
plaintext representation of an ActivityDocument designed to be pasted
into an agent's system prompt or returned from an MCP tool.
"""
from __future__ import annotations

import json

from .frames import ActivityDocument


def to_json(doc: ActivityDocument, *, include_input_text: bool = False, indent: int = 2) -> str:
    return json.dumps(doc.to_dict(include_input_text), indent=indent, ensure_ascii=False)


def to_yaml(doc: ActivityDocument, *, include_input_text: bool = False) -> str:
    try:
        import yaml
    except ImportError as e:
        raise ImportError(
            "PyYAML is required for YAML output: pip install activity-frames[yaml]"
        ) from e
    return yaml.safe_dump(
        doc.to_dict(include_input_text),
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )


def to_markdown(doc: ActivityDocument, *, include_input_text: bool = False) -> str:
    d = doc.to_dict(include_input_text)
    cov = d["coverage"]
    lines = [
        f"# Activity {d['window'].get('day', d['window']['start_utc'][:10])}",
        "",
        f"**Coverage:** {cov['first_activity']} to {cov['last_activity']} local, "
        f"{cov['active_minutes']} active min across {cov['distinct_apps']} apps "
        f"({cov['coverage_pct']}% of span)",
        "",
        "| # | Time | App / Site | Active | What was on screen |",
        "|---|------|-----------|--------|--------------------|",
    ]
    for f in d["frames"]:
        where = f["app"] + (f" ({f['site']})" if f.get("site") else "")
        what = "; ".join(
            (f"{p['kind']}" + (f": {p['entity']}" if p.get("entity") else ""))
            for p in f.get("pages", [])[:3]
        ) or "; ".join(f.get("windows", [])[:2])
        lines.append(
            f"| {f['id']} | {f['start'][:5]}-{f['end'][:5]} | {where} "
            f"| {f['duration_min']}m | {what} |"
        )
    if cov.get("gaps"):
        lines.append("")
        gaps = ", ".join(f"{g['start']}-{g['end']} ({g['minutes']}m)" for g in cov["gaps"])
        lines.append(f"**Away:** {gaps}")
    return "\n".join(lines)


def context_block(doc: ActivityDocument, *, max_frames: int = 40) -> str:
    """Compact plaintext block for an agent's prompt.

    Chronological, one line per frame, entities inline. Roughly 15-25
    tokens per frame; a full working day fits in well under 1.5k tokens.
    """
    cov = doc.coverage
    all_frames = doc.frames

    # If over budget, keep the longest frames but preserve chronology.
    if len(all_frames) > max_frames:
        keep = sorted(all_frames, key=lambda f: -f.duration_min)[:max_frames]
        keep_indices = {f.index for f in keep}
        dropped = len(all_frames) - len(keep)
        kept_frames = [f.to_dict(False) for f in all_frames if f.index in keep_indices]
    else:
        dropped = 0
        kept_frames = [f.to_dict(False) for f in all_frames]

    day = doc.window.get("day", doc.window["start_utc"][:10])
    lines = [
        f"USER ACTIVITY ({day}, local time; measured from screen capture, "
        "no interpretation):",
        f"coverage: {cov['first_activity']}-{cov['last_activity']}, "
        f"{cov['active_minutes']} active min, {cov['distinct_apps']} apps",
    ]
    for g in cov.get("gaps", []):
        lines.append(f"away: {g['start']}-{g['end']} ({g['minutes']}m)")
    for f in kept_frames:
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
    omitted = doc.omitted_below_min
    if omitted:
        lines.append(
            f"(+{omitted} brief frames under "
            f"{doc.min_minutes} min omitted)"
        )
    return "\n".join(lines)
