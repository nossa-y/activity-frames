# activity-frames

[![Downloads](https://static.pepy.tech/badge/activity-frames)](https://pepy.tech/projects/activity-frames)
[![Paper](https://img.shields.io/badge/paper-PDF-b31b1b)](https://github.com/nossa-y/activity-frames/blob/main/paper/activity-frames-paper.pdf)
[![HackerNoon](https://img.shields.io/badge/HackerNoon-top%20story-00E980?logo=hackernoon&logoColor=white)](https://hackernoon.com/i-compiled-55-days-of-screen-activity-into-episodic-memory-for-my-ai-agent)
[![Python](https://img.shields.io/pypi/pyversions/activity-frames)](https://pypi.org/project/activity-frames/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-server-6E56CF)](https://modelcontextprotocol.io)
[![tests](https://github.com/nossa-y/activity-frames/actions/workflows/test.yml/badge.svg)](https://github.com/nossa-y/activity-frames/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/activity-frames)](https://pypi.org/project/activity-frames/)


> **[Download the desktop app](https://usenocta.app)** - Nocta uses activity-frames to watch how you work and brief you daily on what needs your attention. 100% local.

**Episodic memory for AI agents.**

Your agent can read your code, search the web, and call APIs - but it has no idea what you have been doing for the last 8 hours. It starts every conversation blind.

activity-frames gives your agent eyes. It records your screen locally, compiles what it sees into structured **activity frames** (bounded episodes of what you actually did), and serves them to any agent over MCP. No cloud, no LLM in the pipeline, no guessing.

```bash
pip install activity-frames
aframes record      # start capturing (local, audio off by default)
aframes context     # your last 2 hours, agent-ready
```

## What your agent sees

Capture stores instants: thousands of snapshot rows a day, each one saying "at 22:53:05, Chrome showed linkedin.com/in/...". Useless to reason over.

activity-frames compiles those instants into episodes:

```yaml
- id: f-0007
  app: Google Chrome
  site: linkedin.com
  start: "20:24:04"
  end: "20:42:11"
  duration_min: 18.0
  pages:
    - {kind: people_search, entity: "cto paris", count: 2}
    - {kind: profile, entity: najmuzzaman}
    - {kind: company, entity: nexdotai}
  input: {keys: 214, clicks: 31}
  evidence: {frame_ids: "99871..100147"}
```

And into a compact context block for any system prompt:

```
USER ACTIVITY (2026-07-04, local time; measured from screen capture, no interpretation):
coverage: 09:12-18:47, 342 active min, 11 apps
away: 12:30-13:15 (45m)
- 09:12-09:58 Cursor (46.2m): main.py - api
- 10:01-10:44 Google Chrome/github.com (41.3m): pull_request:acme/api#412; code:acme/api
- 20:24-20:42 Google Chrome/linkedin.com (18.0m): people_search:cto paris x2; profile:najmuzzaman; company:nexdotai
```

Drop that into a prompt and your agent knows your day. A full day compiles in under a second and costs zero tokens.

## Episodic memory, done honestly

Agent memory today means conversation memory: what you told the model. Episodic memory is what you actually *did* - and the hard part is representing it without lying.

activity-frames enforces a two-tier contract ([SPEC.md](SPEC.md)):

- **Tier 1, measured (this package):** everything is derivable by deterministic code from capture data. Sessions, durations, typed page entities, input volume, coverage gaps. Same input, same output, every time. There are no intent labels - code cannot know that 2 profile views + a people search was "prospecting". That is your agent's job; it is an LLM.
- **Tier 2, inferred (optional extension):** tools that add interpretation must namespace it, tag confidence (`high | medium | speculative`), and link evidence. Facts and guesses can never silently mix.

Every frame carries evidence pointers back to raw capture rows. Every document declares its blind spots. What the system did not see, it says it did not see.

## Use it from an agent (MCP)

```bash
# Claude Code
claude mcp add activity-frames -- aframes mcp
```

Any MCP client works: command `aframes`, args `["mcp"]`. Five tools: `get_context`, `get_activity`, `get_day_summary`, `get_patterns` (repetitive-workflow detection: repeated clicks, URL loops, daily habits), and `get_communications` (email/messaging surfaces with the window titles seen on each — for many clients the title carries the subject or conversation name; a client that doesn't title its windows with the conversation leaves only its presence to report. Titles only, measured tier: message bodies are never read).

## Use it from Python

```python
from activity_frames import ActivityLog

log = ActivityLog()
doc = log.day()                      # today, structured
doc = log.recent(hours=2)            # last 2 hours
print(log.context(hours=2))          # paste-ready context block
```

## Privacy model

- **Local only.** Capture, storage, and compilation all happen on your machine. Nothing is uploaded anywhere, ever.
- **Read-only compilation.** The compiler opens the capture database read-only.
- **Content opt-in at the output.** Compiled documents carry input *counts* by default; typed-text content appears only if you explicitly pass `--include-text` (this also gates the repeated-text pattern detector). Be clear about the boundary: the capture database itself does store what the recorder sees, locally, so protect it like any sensitive file (FileVault, permissions).
- **Audio off by default.** `aframes record --audio` to opt in.
- **No LLM in the compile path.** Compilation is plain code, so no language model, local or remote, is involved in producing memory. The capture engine does run on-device OCR to read what is on screen; that stays on your machine.
- **You choose what leaves**, when you paste a context block into an agent. Note that window titles and page entities originate from your screen and can contain third-party text; agents should treat them as data, not instructions.

## Architecture

```
 capture engine          compiler (this package)         your agent
 ------------------      ---------------------------     -----------------
 screen snapshots   -->  sessionize (dwell, gaps,   -->  MCP tools /
 accessibility tree      flicker merge)                  context blocks /
 input events            entity typing (20+ sites)       JSON, YAML, md
 (local SQLite)          enrichment, patterns
```

The default capture engine is [screenpipe](https://github.com/mediar-ai/screenpipe): `aframes record` provisions a pinned, MIT-licensed build (v0.3.324), verifies its published sha512 before first run, and manages it for you (see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)). Already running your own recorder? Point `$AFRAMES_DB` at any capture database with compatible `frames` / `ui_events` / `elements` tables and skip `aframes record` entirely.

## CLI

```bash
aframes record                   # start capture (--stop / --status / --audio)
aframes today                    # today's frames (YAML*)
aframes day 2026-07-03 -f json   # any day, JSON
aframes context --hours 3        # agent context block
aframes apps                     # per-app time ledger
aframes patterns --days 7        # repetitive workflow detection
aframes mcp                      # MCP stdio server
```

*YAML output uses PyYAML (`pip install "activity-frames[yaml]"`); without it the CLI falls back to JSON.

## Status

v0.1. Developed and tested on macOS (Apple Silicon); Intel macOS and Linux x64 engine builds exist but are less exercised - reports welcome. Entity parsers cover LinkedIn, GitHub, Google (Search/Docs/Gmail/Maps/Meet/Calendar), YouTube, X, Instagram, Reddit, Luma, Partiful, Product Hunt, Vercel, Supabase, Stripe, Discord, Notion, Figma, Stack Overflow, Calendly, ChatGPT/Claude, localhost; unknown sites fall back to a generic page reference - always total, never lossy. Issues and parser PRs welcome.

Built by [Nossa Iyamu](https://github.com/nossa-y), maker of [Nocta](https://usenocta.app). MIT.
