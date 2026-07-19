"""Deterministic URL -> page reference parsing.

Turns raw browser URLs into typed page references an agent can read at
a glance: "profile: najmuzzaman" instead of a 120-char URL. Pure string
parsing, no network, no AI, no guessing about intent.

Resolution order (all deterministic):
  1. a site-specific parser (exact host, then apex domain),
  2. a generic search-parameter detector (?q=/?query=),
  3. subdomain/path heuristics (dashboard, sign-in, email, calendar,
     meeting) that type common infrastructure pages without a bespoke
     parser,
  4. a total generic fallback ({kind: "page", domain, path}).

Every URL maps to something; typing is never lossy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass
class PageRef:
    kind: str                 # e.g. "profile", "search", "repo", "video", "page"
    domain: str               # normalized host, no "www."
    entity: str | None = None  # the human-relevant identifier, if any
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.entity is not None:
            # collapse whitespace runs and trim; drop empties
            self.entity = " ".join(str(self.entity).split()) or None

    def label(self) -> str:
        if self.entity:
            return f"{self.kind}: {self.entity}"
        return self.kind


def _host(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _parts(url: str) -> list[str]:
    try:
        return [p for p in urlsplit(url).path.split("/") if p]
    except ValueError:
        return []


def _query(url: str) -> dict[str, list[str]]:
    try:
        return parse_qs(urlsplit(url).query)
    except ValueError:
        return {}


def _apex(domain: str) -> str:
    bits = domain.split(".")
    return ".".join(bits[-2:]) if len(bits) > 2 else domain


def _slug(s: str) -> str:
    """Turn a URL slug into readable text: 'my-page-x9f' -> 'my page x9f'."""
    return unquote(s).replace("-", " ").replace("_", " ").strip()


def parse_url(url: str) -> PageRef:
    """Parse a URL into a PageRef. Never raises; never returns None."""
    domain = _host(url)
    if not domain:
        return PageRef(kind="page", domain="", entity=None)
    parts = _parts(url)
    q = _query(url)

    parser = _SITE_PARSERS.get(domain) or _SITE_PARSERS.get(_apex(domain))
    if parser:
        ref = parser(domain, parts, q)
        if ref:
            return ref

    # Generic search detection (?q= / ?query=): meaningful on any site.
    for key in ("q", "query", "search_query"):
        if key in q and q[key] and q[key][0].strip():
            return PageRef(kind="search", domain=domain, entity=unquote(q[key][0]).strip())

    # Subdomain / path heuristics: type common infrastructure pages.
    heur = _heuristic(domain, parts)
    if heur:
        return heur

    # Generic fallback: first path segment as context.
    entity = parts[0] if parts else None
    return PageRef(kind="page", domain=domain, entity=entity)


# ---- Heuristic layer (applies to any site without a bespoke parser) ----

_SIGNIN_HINTS = frozenset(
    {"login", "signin", "sign-in", "sign_in", "auth", "oauth", "sso", "logout"}
)


def _heuristic(domain: str, parts: list[str]) -> PageRef | None:
    first = parts[0].lower() if parts else ""
    # Whole path segments only: "auth" must not match "/authors",
    # "sso" must not match "/lessons".
    segs = {p.lower() for p in parts[:3]}

    # Authentication pages, by host or by path segment.
    if (
        domain.startswith(("accounts.", "login.", "auth.", "signin."))
        or segs & _SIGNIN_HINTS
    ):
        return PageRef(kind="sign_in", domain=domain)
    # Operational surfaces exposed on conventional subdomains.
    if domain.startswith(("dashboard.", "app.", "console.", "admin.", "portal.")):
        return PageRef(kind="dashboard", domain=domain,
                       entity=parts[0] if parts else None)
    if first in ("dashboard", "admin", "console", "settings"):
        return PageRef(kind="dashboard", domain=domain, entity=first)
    if domain.startswith("mail.") or first in ("mail", "inbox"):
        return PageRef(kind="email", domain=domain)
    if domain.startswith("calendar.") or first == "calendar":
        return PageRef(kind="calendar", domain=domain)
    if domain.startswith(("meet.", "zoom.")) or "zoom.us" in domain:
        return PageRef(kind="meeting", domain=domain)
    return None


# ---- Site parsers (each returns PageRef or None to fall through) ----

def _linkedin(domain, parts, q):
    if not parts:
        return PageRef(kind="feed", domain=domain)
    head = parts[0]
    if head == "in" and len(parts) > 1:
        return PageRef(kind="profile", domain=domain, entity=unquote(parts[1]))
    if head == "company" and len(parts) > 1:
        return PageRef(kind="company", domain=domain, entity=unquote(parts[1]))
    if head == "search":
        kw = q.get("keywords", [""])[0]
        what = parts[2] if len(parts) > 2 else "results"
        return PageRef(kind=f"{what}_search", domain=domain, entity=unquote(kw) or None)
    if head == "jobs":
        return PageRef(kind="jobs", domain=domain)
    if head == "feed":
        return PageRef(kind="feed", domain=domain)
    if head == "messaging":
        return PageRef(kind="messaging", domain=domain)
    if head == "mynetwork":
        return PageRef(kind="network", domain=domain)
    if head == "notifications":
        return PageRef(kind="notifications", domain=domain)
    if head == "posts" and len(parts) > 1:
        return PageRef(kind="post", domain=domain, entity=_slug(parts[1])[:50] or None)
    if head in ("uas", "login", "checkpoint"):
        return PageRef(kind="sign_in", domain=domain)
    return None


def _github(domain, parts, q):
    if not parts:
        return PageRef(kind="home", domain=domain)
    if len(parts) == 1:
        return PageRef(kind="user", domain=domain, entity=parts[0])
    repo = f"{parts[0]}/{parts[1]}"
    if len(parts) >= 4 and parts[2] == "pull":
        return PageRef(kind="pull_request", domain=domain, entity=f"{repo}#{parts[3]}")
    if len(parts) >= 4 and parts[2] == "issues":
        return PageRef(kind="issue", domain=domain, entity=f"{repo}#{parts[3]}")
    if len(parts) >= 3 and parts[2] in ("commits", "commit"):
        return PageRef(kind="commits", domain=domain, entity=repo)
    if len(parts) >= 3 and parts[2] in ("blob", "tree"):
        return PageRef(kind="code", domain=domain, entity=repo)
    return PageRef(kind="repo", domain=domain, entity=repo)


def _google(domain, parts, q):
    if "q" in q and q["q"] and q["q"][0].strip():
        return PageRef(kind="search", domain=domain, entity=unquote(q["q"][0]).strip())
    if parts and parts[0] == "maps":
        if len(parts) >= 2 and parts[1] == "place":
            return PageRef(kind="map_place", domain=domain,
                           entity=_slug(parts[2]) if len(parts) > 2 else None)
        if len(parts) >= 2 and parts[1] == "dir":
            return PageRef(kind="map_directions", domain=domain)
        return PageRef(kind="map", domain=domain)
    if parts and parts[0] == "search":
        return None  # let search-param detector handle
    return None


def _google_docs(domain, parts, q):
    kinds = {"document": "doc", "spreadsheets": "sheet", "presentation": "slides", "forms": "form"}
    if parts and parts[0] in kinds:
        return PageRef(kind=kinds[parts[0]], domain=domain)
    return None


def _gmail(domain, parts, q):
    if parts and parts[0] == "mail":
        return PageRef(kind="email", domain=domain)
    return None


def _youtube(domain, parts, q):
    if parts and parts[0] == "watch":
        return PageRef(kind="video", domain=domain, entity=q.get("v", [None])[0])
    if parts and parts[0] == "results":
        return PageRef(kind="search", domain=domain,
                       entity=unquote(q.get("search_query", [""])[0]) or None)
    if parts and parts[0].startswith("@"):
        return PageRef(kind="channel", domain=domain, entity=parts[0])
    if parts and parts[0] == "shorts" and len(parts) > 1:
        return PageRef(kind="video", domain=domain, entity=parts[1])
    return None


def _x(domain, parts, q):
    if not parts:
        return PageRef(kind="feed", domain=domain)
    if parts[0] in ("home", "explore"):
        return PageRef(kind="feed", domain=domain)
    if parts[0] == "notifications":
        return PageRef(kind="notifications", domain=domain)
    if parts[0] == "messages":
        return PageRef(kind="messages", domain=domain)
    if parts[0] == "search":
        return PageRef(kind="search", domain=domain,
                       entity=unquote(q.get("q", [""])[0]) or None)
    if parts[0] == "i":
        sub = parts[1] if len(parts) > 1 else ""
        if sub == "chat":
            return PageRef(kind="messages", domain=domain)
        if sub == "flow":
            return PageRef(kind="sign_in", domain=domain)
        return PageRef(kind="page", domain=domain, entity=sub or "i")
    if parts[0] in ("settings", "compose", "jobs", "logout"):
        return PageRef(kind="page", domain=domain, entity=parts[0])
    if len(parts) >= 3 and parts[1] == "status":
        return PageRef(kind="post", domain=domain, entity=parts[0])
    return PageRef(kind="profile", domain=domain, entity=parts[0])


def _instagram(domain, parts, q):
    if not parts:
        return PageRef(kind="feed", domain=domain)
    head = parts[0]
    if head == "p" and len(parts) > 1:
        return PageRef(kind="post", domain=domain, entity=parts[1])
    if head == "reel" and len(parts) > 1:
        return PageRef(kind="reel", domain=domain, entity=parts[1])
    if head == "reels":
        return PageRef(kind="reels", domain=domain)
    if head == "stories" and len(parts) > 1:
        return PageRef(kind="story", domain=domain, entity=parts[1])
    if head == "explore":
        return PageRef(kind="explore", domain=domain)
    if head == "direct":
        return PageRef(kind="messages", domain=domain)
    if head in ("accounts", "emails"):
        return PageRef(kind="sign_in", domain=domain)
    return PageRef(kind="profile", domain=domain, entity=head)


def _reddit(domain, parts, q):
    if not parts:
        return PageRef(kind="feed", domain=domain)
    head = parts[0]
    if head == "r" and len(parts) >= 2:
        if len(parts) >= 5 and parts[2] == "comments":
            title = _slug(parts[4])[:50] if len(parts) > 4 else None
            return PageRef(kind="post", domain=domain, entity=title or f"r/{parts[1]}")
        return PageRef(kind="subreddit", domain=domain, entity=f"r/{parts[1]}")
    if head in ("user", "u") and len(parts) >= 2:
        return PageRef(kind="profile", domain=domain, entity=f"u/{parts[1]}")
    return None


def _stackoverflow(domain, parts, q):
    if len(parts) >= 2 and parts[0] == "questions":
        return PageRef(kind="question", domain=domain,
                       entity=parts[2].replace("-", " ") if len(parts) > 2 else parts[1])
    return None


def _calendly(domain, parts, q):
    if parts:
        return PageRef(kind="booking", domain=domain, entity="/".join(parts[:2]))
    return None


def _luma(domain, parts, q):
    if not parts:
        return PageRef(kind="home", domain=domain)
    if parts[0] in ("home", "discover", "explore", "signin", "user", "settings"):
        return PageRef(kind=parts[0] if parts[0] != "signin" else "sign_in", domain=domain)
    # lu.ma/<eventid> is a short event slug
    return PageRef(kind="event", domain=domain, entity=parts[0])


def _partiful(domain, parts, q):
    if parts and parts[0] == "e" and len(parts) > 1:
        return PageRef(kind="event", domain=domain, entity=parts[1])
    if parts:
        return PageRef(kind="event", domain=domain, entity=parts[0])
    return PageRef(kind="home", domain=domain)


def _producthunt(domain, parts, q):
    if len(parts) >= 2 and parts[0] in ("posts", "products"):
        return PageRef(kind="product", domain=domain, entity=_slug(parts[1]))
    if len(parts) >= 2 and parts[0] == "@":
        return PageRef(kind="profile", domain=domain, entity=parts[1])
    return None


def _google_meet(domain, parts, q):
    return PageRef(kind="meeting", domain=domain, entity=parts[0] if parts else None)


def _google_calendar(domain, parts, q):
    return PageRef(kind="calendar", domain=domain)


def _vercel(domain, parts, q):
    if not parts:
        return PageRef(kind="dashboard", domain=domain)
    if len(parts) >= 2:
        return PageRef(kind="project", domain=domain, entity=f"{parts[0]}/{parts[1]}")
    return PageRef(kind="dashboard", domain=domain, entity=parts[0])


def _supabase(domain, parts, q):
    if len(parts) >= 3 and parts[0] == "dashboard" and parts[1] == "project":
        return PageRef(kind="project", domain=domain, entity=parts[2])
    if parts and parts[0] == "dashboard":
        return PageRef(kind="dashboard", domain=domain)
    return None


def _stripe(domain, parts, q):
    return PageRef(kind="dashboard", domain=domain,
                   entity=parts[0] if parts else None)


def _discord(domain, parts, q):
    if len(parts) >= 2 and parts[0] == "channels":
        if parts[1] == "@me":
            return PageRef(kind="messages", domain=domain)
        return PageRef(kind="channel", domain=domain, entity=parts[1])
    return PageRef(kind="app", domain=domain)


def _slack(domain, parts, q):
    """Type the browser client without inferring a channel name from IDs."""
    if domain == "app.slack.com" and parts and parts[0] == "client":
        # Client URLs are /client/<workspace>/<channel-or-DM>. The IDs are
        # useful evidence, but the title is the human-readable measured name.
        entity = "/".join(parts[1:3]) or None
        return PageRef(kind="messaging", domain=domain, entity=entity)
    if domain.endswith(".slack.com") and len(parts) > 1 and parts[0] == "archives":
        # Workspace archive permalinks identify the channel but not its name.
        return PageRef(kind="messaging", domain=domain, entity=parts[1])
    return None


def _notion(domain, parts, q):
    if parts:
        # Notion slugs end with the page id; the readable part is the prefix.
        last = parts[-1]
        slug = last.rsplit("-", 1)[0] if "-" in last else last
        return PageRef(kind="doc", domain=domain, entity=_slug(slug) or None)
    return PageRef(kind="home", domain=domain)


def _figma(domain, parts, q):
    if len(parts) >= 3 and parts[0] in ("file", "design", "board"):
        return PageRef(kind="design", domain=domain, entity=_slug(parts[2]))
    return None


def _chat_ai(domain, parts, q):
    return PageRef(kind="ai_chat", domain=domain)


def _linear(domain, parts, q):
    """Parse linear.app URLs into typed page references.

    URL shapes:
      /signin  /join/<token>               -> sign_in
      /<workspace>/issue/<ID>/...          -> issue   (entity = issue ID, e.g. ENG-123)
      /<workspace>/project/<slug>          -> project (entity = readable project name)
      /<workspace>/cycles                  -> cycles
      /<workspace>/roadmap                 -> roadmap
      /<workspace>/inbox                   -> notifications
      /<workspace>/my-issues               -> my_issues
      /<workspace>/views[/<view>]          -> views
      /<workspace>  (no sub-path)          -> dashboard (entity = workspace slug)
    """
    if not parts:
        return PageRef(kind="home", domain=domain)
    head = parts[0]
    if head in ("signin", "join"):
        return PageRef(kind="sign_in", domain=domain)
    # At this point parts[0] is the workspace slug.
    workspace = head
    if len(parts) == 1:
        return PageRef(kind="dashboard", domain=domain, entity=workspace)
    sub = parts[1]
    if sub == "issue" and len(parts) >= 3:
        # parts[2] is the issue ID (e.g. "ENG-123"); ignore slug tail
        return PageRef(kind="issue", domain=domain, entity=parts[2])
    if sub == "project" and len(parts) >= 3:
        return PageRef(kind="project", domain=domain, entity=_slug(parts[2]))
    if sub == "cycles":
        return PageRef(kind="cycles", domain=domain, entity=workspace)
    if sub == "roadmap":
        return PageRef(kind="roadmap", domain=domain, entity=workspace)
    if sub == "inbox":
        return PageRef(kind="notifications", domain=domain)
    if sub == "my-issues":
        return PageRef(kind="my_issues", domain=domain)
    if sub == "views":
        return PageRef(kind="views", domain=domain)
    return None


def _gitlab(domain, parts, q):
    """Parse gitlab.com URLs into typed page references.

    GitLab separates the project path (which may contain subgroups) from
    the resource with a literal "-" path segment:
      /users/sign_in | sign_up | password   -> sign_in
      /search?search=...                    -> search  (GitLab uses ?search=)
      /dashboard/...                        -> dashboard
      /explore/...                          -> explore
      /groups/<group>[/subgroup]            -> group
      /<ns>/<repo>/-/issues/<n>             -> issue          (entity "ns/repo#n")
      /<ns>/<repo>/-/merge_requests/<n>     -> merge_request  (entity "ns/repo!n")
      /<ns>/<repo>/-/blob|tree/...          -> code
      /<ns>/<repo>/-/commit(s)/...          -> commits
      /<ns>/<repo>/-/pipelines...           -> pipelines
      /<ns>/<repo>[/-/<other>]              -> repo
    A single path segment may be a user or a group; that ambiguity is
    left to the generic fallback rather than guessed.
    """
    if not parts:
        return PageRef(kind="home", domain=domain)
    head = parts[0]
    if head == "users":
        if len(parts) > 1 and parts[1] in ("sign_in", "sign_up", "password", "confirmation"):
            return PageRef(kind="sign_in", domain=domain)
        return None
    if head == "search":
        return PageRef(kind="search", domain=domain,
                       entity=unquote(q.get("search", [""])[0]).strip() or None)
    if head == "dashboard":
        return PageRef(kind="dashboard", domain=domain,
                       entity=parts[1] if len(parts) > 1 else None)
    if head == "explore":
        return PageRef(kind="explore", domain=domain)
    if head == "groups" and len(parts) > 1:
        tail = parts[1:]
        if "-" in tail:
            tail = tail[:tail.index("-")]
        return PageRef(kind="group", domain=domain, entity="/".join(tail) or None)
    if "-" in parts:
        cut = parts.index("-")
        project = "/".join(parts[:cut])
        rest = parts[cut + 1:]
        if not project:
            return None
        res = rest[0] if rest else ""
        if res == "issues" and len(rest) > 1 and rest[1].isdigit():
            return PageRef(kind="issue", domain=domain, entity=f"{project}#{rest[1]}")
        if res == "merge_requests" and len(rest) > 1 and rest[1].isdigit():
            return PageRef(kind="merge_request", domain=domain, entity=f"{project}!{rest[1]}")
        if res in ("blob", "tree"):
            return PageRef(kind="code", domain=domain, entity=project)
        if res in ("commit", "commits"):
            return PageRef(kind="commits", domain=domain, entity=project)
        if res == "pipelines":
            return PageRef(kind="pipelines", domain=domain, entity=project)
        return PageRef(kind="repo", domain=domain, entity=project)
    if len(parts) >= 2:
        return PageRef(kind="repo", domain=domain, entity="/".join(parts))
    return None


def _localhost(domain, parts, q):
    return PageRef(kind="local_dev", domain=domain, entity="/".join(parts[:2]) or None)


_SITE_PARSERS = {
    "linkedin.com": _linkedin,
    "github.com": _github,
    "gitlab.com": _gitlab,
    "google.com": _google,
    "docs.google.com": _google_docs,
    "mail.google.com": _gmail,
    "meet.google.com": _google_meet,
    "calendar.google.com": _google_calendar,
    "youtube.com": _youtube,
    "x.com": _x,
    "twitter.com": _x,
    "instagram.com": _instagram,
    "reddit.com": _reddit,
    "stackoverflow.com": _stackoverflow,
    "calendly.com": _calendly,
    "luma.com": _luma,
    "lu.ma": _luma,
    "partiful.com": _partiful,
    "producthunt.com": _producthunt,
    "vercel.com": _vercel,
    "supabase.com": _supabase,
    "dashboard.stripe.com": _stripe,
    "discord.com": _discord,
    "slack.com": _slack,
    "notion.so": _notion,
    "notion.com": _notion,
    "app.notion.com": _notion,
    "figma.com": _figma,
    "linear.app": _linear,
    "chatgpt.com": _chat_ai,
    "chat.openai.com": _chat_ai,
    "claude.ai": _chat_ai,
    "localhost": _localhost,
    "127.0.0.1": _localhost,
}
