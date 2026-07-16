"""aframes: command-line interface.

  aframes today                       today's frames (yaml)
  aframes day 2026-07-04 -f json      a specific day
  aframes context --hours 3           paste-ready agent context block
  aframes apps                        per-app ledger for today
  aframes patterns --days 7           repetitive workflows
  aframes comms --hours 24            email/messaging surfaces + titles
  aframes mcp                         run the MCP stdio server
"""
from __future__ import annotations

import argparse
import sys

from . import ActivityLog, __version__
from .db import RecorderDBNotFound
from .emit import context_block, to_json, to_markdown, to_yaml


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", help="path to the capture SQLite database")
    p.add_argument("-f", "--format", choices=["yaml", "json", "md", "context"],
                   default="yaml", help="output format (default yaml)")
    p.add_argument("--min-minutes", type=float, default=0.5,
                   help="drop frames shorter than this (default 0.5)")
    p.add_argument("--include-text", action="store_true",
                   help="include typed text snippets (off by default)")
    p.add_argument("--layout", default=None,
                   help="keyboard layout decode map (e.g. azerty)")


def _emit(doc, fmt: str, include_text: bool) -> str:
    if fmt == "json":
        return to_json(doc, include_input_text=include_text)
    if fmt == "md":
        return to_markdown(doc, include_input_text=include_text)
    if fmt == "context":
        return context_block(doc)
    try:
        return to_yaml(doc, include_input_text=include_text)
    except ImportError:
        print("note: PyYAML not installed, emitting JSON "
              "(pip install activity-frames[yaml])", file=sys.stderr)
        return to_json(doc, include_input_text=include_text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aframes",
        description="activity-frames: episodic memory for AI agents. "
        "Compiles screen capture into structured activity frames.",
    )
    parser.add_argument("--version", action="version", version=f"aframes {__version__}")
    sub = parser.add_subparsers(dest="cmd")

    p_today = sub.add_parser("today", help="activity frames for today")
    _add_common(p_today)

    p_day = sub.add_parser("day", help="activity frames for a local day")
    p_day.add_argument("date", help="YYYY-MM-DD")
    _add_common(p_day)

    p_ctx = sub.add_parser("context", help="compact agent context block")
    p_ctx.add_argument("--hours", type=float, default=2.0,
                       help="how many hours back (default 2)")
    p_ctx.add_argument("--max-frames", type=int, default=40)
    _add_common(p_ctx)

    p_apps = sub.add_parser("apps", help="per-app usage ledger")
    p_apps.add_argument("date", nargs="?", help="YYYY-MM-DD (default today)")
    _add_common(p_apps)

    p_pat = sub.add_parser("patterns", help="repetitive workflow patterns")
    p_pat.add_argument("--days", type=int, default=7)
    _add_common(p_pat)

    p_comms = sub.add_parser(
        "comms", help="communication surfaces (email/messaging) + titles seen")
    p_comms.add_argument("--hours", type=float, default=24.0,
                         help="how many hours back (default 24)")
    p_comms.add_argument("--day", help="local day YYYY-MM-DD (overrides --hours)")
    p_comms.add_argument("--kind", choices=["email", "messaging", "messages",
                                            "notifications"],
                         help="filter to one kind (default: all)")
    p_comms.add_argument("--json", action="store_true", help="emit JSON")
    p_comms.add_argument("--db", help="path to the capture SQLite database")

    p_mcp = sub.add_parser("mcp", help="run the MCP stdio server")
    p_mcp.add_argument("--db", help="path to the capture SQLite database")
    p_mcp.add_argument("--layout", default=None)

    p_rec = sub.add_parser("record", help="start/stop the built-in capture engine")
    p_rec.add_argument("--stop", action="store_true", help="stop capturing")
    p_rec.add_argument("--status", action="store_true", help="show capture status")
    p_rec.add_argument("--audio", action="store_true",
                       help="also capture audio (off by default)")
    p_rec.add_argument("--foreground", action="store_true",
                       help="run in the foreground instead of detaching")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 1

    if args.cmd == "record":
        from . import capture

        try:
            if args.stop:
                capture.stop()
            elif args.status:
                print(capture.status())
            else:
                capture.start(audio=args.audio, foreground=args.foreground)
        except capture.CaptureError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0

    if args.cmd == "mcp":
        from .mcp_server import MCPServer

        MCPServer(args.db, args.layout).serve()
        return 0

    if args.cmd == "comms":
        import json

        try:
            log = ActivityLog(args.db)
        except RecorderDBNotFound as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        kinds = frozenset({args.kind}) if args.kind else None
        try:
            surfaces = log.communications(args.hours, day=args.day, kinds=kinds)
        except ValueError as e:
            print(f"error: {e} (dates are YYYY-MM-DD)", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps([s.to_dict() for s in surfaces],
                             indent=2, ensure_ascii=False))
        else:
            for s in surfaces:
                where = f"{s.app}/{s.site}" if s.site else s.app
                print(f"[{s.kind}] {where}  {s.first[:5]}-{s.last[:5]}"
                      f"  ({s.frames_analyzed} frames)")
                for t in s.titles:
                    print(f"    {t.first[:5]}  x{t.count:<3} {t.text}")
                if s.omitted_titles:
                    print(f"    (+{s.omitted_titles} more titles)")
        return 0

    if getattr(args, "layout", None):
        from .enrich import LAYOUTS

        if args.layout not in LAYOUTS:
            known = ", ".join(sorted(LAYOUTS))
            print(f"error: unknown layout '{args.layout}' (known: {known})",
                  file=sys.stderr)
            return 2

    try:
        log = ActivityLog(args.db, layout=args.layout, min_minutes=args.min_minutes)
    except RecorderDBNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    kw = dict(min_minutes=args.min_minutes, include_text=args.include_text)

    try:
        if args.cmd == "today":
            print(_emit(log.day(**kw), args.format, args.include_text))
        elif args.cmd == "day":
            print(_emit(log.day(args.date, **kw), args.format, args.include_text))
        elif args.cmd == "context":
            doc = log.recent(args.hours, **kw)
            print(context_block(doc, max_frames=args.max_frames))
        elif args.cmd == "apps":
            for a in log.apps(args.date):
                wins = f"  ({'; '.join(a.top_windows[:2])})" if a.top_windows else ""
                print(f"{a.minutes:8.1f} min  {a.app}  "
                      f"[{a.sessions} sessions, longest {a.longest_session_min}m]{wins}")
        elif args.cmd == "patterns":
            for p in log.patterns(args.days, include_text=args.include_text):
                print(f"[{p.kind}] {p.label}")
    except ValueError as e:
        print(f"error: {e} (dates are YYYY-MM-DD)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
