"""Communication surfaces: a measured view of email / messaging / notification activity.

A *communication surface* is an (app, site) context whose pages are typed as
communication by the entity layer (``email``, ``messaging``, ``messages``,
``notifications``) — or a native app known to be one (WhatsApp, Slack, Mail, …).
For each surface this module reports what the recorder measured there: the
window titles seen, when, and how often, with evidence pointers back to raw
frames.

Why titles: for most clients the window title carries the human-relevant line
("GetCleed not working - user@gmail.com - Gmail" is an email subject; "general -
Slack" is a channel), and titles are already part of the measured tier — the
same field ``frames``' ``windows`` reports. This is a focused, chronological
view of that data, not a new capture channel.

Tier-1 honesty (SPEC §1, §7): everything here is a pure, deterministic function
of the capture rows. No message bodies are read, nothing is ranked or labeled
as urgent, and no unread state is inferred — deciding what a title *means* is
the consumer's job. Blind spot, stated: a client that does not put the message
in its window title leaves nothing for this view to report.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ._time import fmt_local_hms
from .db import Database
from .entities import parse_url
from .sessionize import clean_name, load_frames

# Page kinds (as typed by entities.parse_url) that count as communication.
COMM_KINDS = frozenset({"email", "messaging", "messages", "notifications"})

# Native apps that ARE a communication surface (no URL to type). Keys are
# exact app names after clean_name().lower(); values are the surface kind.
APP_SURFACES = {
    "whatsapp": "messaging",
    "messages": "messaging",
    "slack": "messaging",
    "telegram": "messaging",
    "discord": "messaging",
    "signal": "messaging",
    "messenger": "messaging",
    "microsoft teams": "messaging",
    "mail": "email",
    "outlook": "email",
    "microsoft outlook": "email",
    "thunderbird": "email",
}


@dataclass
class TitleItem:
    """One distinct window title observed on a surface."""

    text: str
    first: str          # local HH:MM:SS of first sighting
    last: str           # local HH:MM:SS of last sighting
    count: int          # frames it appeared on
    frames: str         # frame ids of first and last sighting, "first..last"
                        # (interior ids of the range may belong to other contexts;
                        # `count` is the number of actual sightings)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "first": self.first,
            "last": self.last,
            "count": self.count,
            "evidence": {"frame_ids": self.frames},
        }


@dataclass
class CommSurface:
    """A communication context and the titles measured on it."""

    kind: str                 # email | messaging | messages | notifications
    app: str
    site: str | None          # URL host for browser surfaces, None for native apps
    first: str                # local HH:MM:SS
    last: str                 # local HH:MM:SS
    titles: list[TitleItem] = field(default_factory=list)
    omitted_titles: int = 0   # distinct titles beyond max_titles (disclosed, never silent)
    frames_analyzed: int = 0

    def to_dict(self) -> dict:
        d = {
            "kind": self.kind,
            "app": self.app,
            "first": self.first,
            "last": self.last,
            "frames_analyzed": self.frames_analyzed,
            "titles": [t.to_dict() for t in self.titles],
            "scope": "window titles only — message bodies are not read at this tier",
        }
        if self.site:
            d["site"] = self.site
        if self.omitted_titles:
            d["omitted"] = {"titles_beyond_max": self.omitted_titles}
        return d


def surfaces(
    db: Database,
    start_utc: str,
    end_utc: str,
    *,
    kinds: frozenset[str] | set[str] | None = None,
    max_titles: int = 30,
) -> list[CommSurface]:
    """Communication surfaces for a UTC window, chronological.

    Deterministic: same database and window, same output. Surfaces are
    ordered by first sighting (ties: app, site, then kind); titles within
    a surface likewise by first sighting (ties: text).
    """
    wanted = frozenset(kinds) if kinds else COMM_KINDS

    # (kind, app, site) -> accumulator
    groups: dict[tuple, dict] = {}
    for f in load_frames(db, start_utc, end_utc):
        if f.url:
            ref = parse_url(f.url)
            kind, site = ref.kind, ref.domain or None
        else:
            kind, site = APP_SURFACES.get(f.app.lower(), ""), None
        if kind not in wanted:
            continue

        g = groups.setdefault(
            (kind, f.app, site),
            {"first": f.epoch, "last": f.epoch, "frames": 0, "titles": {}},
        )
        g["first"] = min(g["first"], f.epoch)
        g["last"] = max(g["last"], f.epoch)
        g["frames"] += 1

        title = clean_name(f.window) if f.window else ""
        if not title:
            continue
        t = g["titles"].setdefault(
            title,
            {"first": f.epoch, "last": f.epoch, "count": 0,
             "first_id": f.id, "last_id": f.id},
        )
        t["first"] = min(t["first"], f.epoch)
        t["last"] = max(t["last"], f.epoch)
        t["count"] += 1
        t["first_id"] = min(t["first_id"], f.id)
        t["last_id"] = max(t["last_id"], f.id)

    out: list[CommSurface] = []
    for (kind, app, site), g in sorted(
        groups.items(),
        key=lambda kv: (kv[1]["first"], kv[0][1], kv[0][2] or "", kv[0][0]),
    ):
        items = [
            TitleItem(
                text=text,
                first=fmt_local_hms(t["first"]),
                last=fmt_local_hms(t["last"]),
                count=t["count"],
                frames=(
                    str(t["first_id"])
                    if t["first_id"] == t["last_id"]
                    else f"{t['first_id']}..{t['last_id']}"
                ),
            )
            for text, t in sorted(
                g["titles"].items(), key=lambda kv: (kv[1]["first"], kv[0])
            )
        ]
        out.append(
            CommSurface(
                kind=kind,
                app=app,
                site=site,
                first=fmt_local_hms(g["first"]),
                last=fmt_local_hms(g["last"]),
                titles=items[:max_titles],
                omitted_titles=max(0, len(items) - max_titles),
                frames_analyzed=g["frames"],
            )
        )
    return out
