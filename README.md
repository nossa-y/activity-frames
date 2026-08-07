# activity-frames powering Nocta

[![arXiv](https://img.shields.io/badge/arXiv-2608.05784-b31b1b.svg)](https://arxiv.org/abs/2608.05784)
[![HackerNoon](https://img.shields.io/badge/HackerNoon-top%20story-00E980?logo=hackernoon&logoColor=white)](https://hackernoon.com/i-compiled-55-days-of-screen-activity-into-episodic-memory-for-my-ai-agent)
[![Hugging Face](https://img.shields.io/badge/🤗%20HF-Papers-yellow)](https://huggingface.co/papers/2608.05784)
[![PyPI](https://img.shields.io/pypi/v/activity-frames)](https://pypi.org/project/activity-frames/)
[![Downloads](https://static.pepy.tech/badge/activity-frames)](https://pepy.tech/projects/activity-frames)
[![Downloads/month](https://static.pepy.tech/badge/activity-frames/month)](https://pepy.tech/projects/activity-frames)
[![GitHub stars](https://img.shields.io/github/stars/nossa-y/activity-frames)](https://github.com/nossa-y/activity-frames/stargazers)
[![Python](https://img.shields.io/pypi/pyversions/activity-frames)](https://pypi.org/project/activity-frames/)
[![MCP](https://img.shields.io/badge/MCP-server-6E56CF)](https://modelcontextprotocol.io)
[![tests](https://github.com/nossa-y/activity-frames/actions/workflows/test.yml/badge.svg)](https://github.com/nossa-y/activity-frames/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Website](https://img.shields.io/badge/website-usenocta.app-blue)](https://usenocta.app)


**Turn your workday into structured workflows agents can execute.**

Computer-use agents work every task out from scratch, even one you've done a hundred times. And between tasks, your agent has no idea what you've been doing all day, so it starts every conversation blind.

activity-frames fixes both. It records your screen locally and compiles what it sees into structured **activity frames**: bounded, deterministic records of the tasks you actually did. The recurring ones become **workflows an agent can execute** instead of working out again. So your repetitive computer tasks get done **cheaper** (running a compiled workflow costs almost no tokens) and **more reliable** (the same steps, grounded the same way every time, instead of guessing from a screenshot) - and everything else becomes context your agent can use.

```bash
pip install activity-frames
aframes record      # start capturing (local, audio off by default)
aframes context     # your last 2 hours, agent-ready
```

> **Want to go deeper?** [docs/mcp.md](docs/mcp.md) covers the six MCP tools, [docs/python.md](docs/python.md) the Python API, [docs/cli.md](docs/cli.md) every flag, [docs/troubleshooting.md](docs/troubleshooting.md) the errors you might actually hit. AI agent integrating this? [AGENTS.md](AGENTS.md) is written for you.

## What your agent sees

Capture stores instants: thousands of snapshot rows a day, each one saying "at 22:53:05, Chrome showed linkedin.com/in/...". Useless to reason over.

activity-frames compiles those instants into activity frames:

```yaml
- id: f-0007
  app: Google Chrome
  site: linkedin.com
  start: "20:24:04"
  end: "20:42:11"
  duration_min: 18.0
  pages:
    - {kind: people_search, entity: "cto berlin", count: 2}
    - {kind: profile, entity: john-doe}
    - {kind: company, entity: acme-ai}
  input: {keys: 214, clicks: 31}
  evidence: {frame_ids: "99871..100147"}
```

And into a compact context block for any system prompt:

```
USER ACTIVITY (2026-07-04, local time; measured from screen capture, no interpretation):
coverage: 09:12-20:42, 342 active min, 11 apps
away: 12:30-13:15 (45m)
away: 18:47-20:24 (97m)
- 09:12-09:58 Cursor (46.2m): main.py - api
- 10:01-10:44 Google Chrome/github.com (41.3m): pull_request:acme/api#412; code:acme/api
- 20:24-20:42 Google Chrome/linkedin.com (18.0m): people_search:cto berlin x2; profile:john-doe; company:acme-ai; typed ~214 chars
```

Drop that into a prompt and your agent knows your day. A full day compiles in under a second and costs zero tokens.

## Workflows agents can execute

Computer-use agents re-derive every task from scratch - screenshot, reason, act, repeat - even for a workflow they've run a hundred times. That re-derivation is where the token cost goes, and it's waste: the workflow hasn't changed.

Because activity-frames compiles recurring activity deterministically, a task you've demonstrated becomes an executable script:

```bash
aframes steps --find "message john doe"
```

```json
{
  "steps": [
    {"t": "20:24:09", "op": "focus", "target": "Google Chrome · LinkedIn", "n": 1},
    {"t": "20:24:14", "op": "click", "target": "Search", "role": "TextField", "url": "https://www.linkedin.com/feed/", "n": 2},
    {"t": "20:24:16", "op": "type", "chars": 8, "text": "john doe", "n": 3},
    {"t": "20:24:21", "op": "click", "target": "John Doe", "role": "Link", "url": "https://www.linkedin.com/search/results/people/", "n": 4},
    {"t": "20:24:29", "op": "click", "target": "Message", "role": "Button", "url": "https://www.linkedin.com/in/john-doe/", "n": 5},
    {"t": "20:24:35", "op": "type", "chars": 71, "text": "hey, loved your post on agent memory - open to a quick chat next week?", "n": 6}
  ],
  "step_count": 6,
  "unresolved_clicks": 0
}
```

That's the replay view of a demonstrated run - ordered clicks grounded by element name and role, typed runs, focus changes. An agent repeats the task instead of re-deriving it: fill the slots with new values (a different name, the same steps) and execute. On the happy path it replays at zero model calls; anything unexpected halts and asks instead of guessing.

We measured how much agents overpay to re-derive workflows they've already performed - the **Routine Overhead Ratio** - on weeks of real activity, replicated it on a public web-task dataset, and built a deterministic executor that replays a compiled workflow in a real browser. Instrument, measurements, and executor: [`research/`](research/).

Passively-captured activity becomes **deterministic action** - and the cheapest computer task is the one an agent never reasons through twice.

## Measured, not guessed

Agent memory today means conversation memory: what you told the model. What you actually *did* is the missing half - and the hard part is representing it without lying.

activity-frames enforces a two-tier contract ([SPEC.md](SPEC.md)):

- **Tier 1, measured (this package):** everything is derivable by deterministic code from capture data - sessions, durations, typed page entities, input volume, coverage gaps. No interpretation, no intent labels. Same input, same output, every time.
- **Tier 2, inferred (optional extension):** tools that add interpretation must namespace it, tag confidence (`high | medium | speculative`), and link evidence. Facts and guesses can never silently mix.

Every frame carries evidence pointers back to raw capture rows. Every document declares its blind spots. What the system did not see, it says it did not see.

## Use it from an agent (MCP)

```bash
# Claude Code
claude mcp add activity-frames -- aframes mcp
```

Any MCP client works: command `aframes`, args `["mcp"]`. Six tools: `get_context`, `get_activity`, `get_steps` (expand one activity frame into its ordered click-by-click script - the replay view of a demonstrated run, so an agent can repeat the task instead of re-deriving it), `get_day_summary`, `get_patterns` (repetitive-workflow detection: repeated clicks, action sequences, URL patterns, app-switching loops, daily habits), and `get_communications` (email/messaging surfaces with the window titles seen on each — for many clients the title carries the subject or conversation name; a client that doesn't title its windows with the conversation leaves only its presence to report. Titles only, measured tier: message bodies are never read).

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
 input events            entity typing (25+ sites)       JSON, YAML, md
 (local SQLite)          enrichment, patterns
```

The default capture engine is [nocta-recorder](https://github.com/nossa-y/nocta-recorder): `aframes record` provisions a pinned, MIT-licensed build, verifies its published sha256 before first run, and manages it for you (see [ACKNOWLEDGMENTS.md](ACKNOWLEDGMENTS.md)); a `nocta-recorder` binary already on your PATH is used as-is (bring your own build). Already running your own recorder? Point `$AFRAMES_DB` at any capture database with compatible `frames` / `ui_events` / `elements` tables and skip `aframes record` entirely.

## CLI

```bash
aframes record                   # start capture (--stop / --status / --audio)
aframes today                    # today's frames (YAML*)
aframes day 2026-07-03 -f json   # any day, JSON
aframes context --hours 3        # agent context block
aframes apps                     # per-app time ledger
aframes patterns --days 7        # repetitive workflow detection
aframes comms --hours 24         # email/messaging surfaces + titles seen
aframes steps --frame f-0002     # one frame's click-by-click script (or --find "task query")
aframes mcp                      # MCP stdio server
```

*YAML output uses PyYAML (`pip install "activity-frames[yaml]"`); without it the CLI falls back to JSON.

Run `aframes <cmd> --help` for the full flag set (`--db`, `--include-text`, `--layout`, ...).

## Docs

| | |
|---|---|
| [AGENTS.md](AGENTS.md) | Integration guide for AI agents (install, MCP tools, replay loop, cautions) |
| [SPEC.md](SPEC.md) | The schema contract: measured vs inferred tiers |
| [docs/mcp.md](docs/mcp.md) | MCP setup and the six tools in detail |
| [docs/python.md](docs/python.md) | Python API reference |
| [docs/cli.md](docs/cli.md) | Every command and flag |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Keyed by the actual error you see |
| [research/](research/) | The cost instrument, measurements, and replay executor behind the paper |
| [llms.txt](llms.txt) | Compressed repo map for LLM context windows |

## Status

v0.2. Developed and tested on macOS (Apple Silicon); an Intel macOS engine build is published but less exercised - reports welcome. No prebuilt Linux engine yet: on Linux, run your own recorder and point `$AFRAMES_DB` at its database (the compiler itself is tested on Linux in CI). Entity parsers cover LinkedIn, GitHub, GitLab, Google (Search/Docs/Gmail/Maps/Meet/Calendar), YouTube, X, Instagram, Reddit, Luma, Partiful, Product Hunt, Vercel, Supabase, Stripe (dashboard), Discord, Slack, Notion, Figma, Linear, Stack Overflow, Calendly, Crunchbase, Atlassian (Jira/Confluence), ChatGPT/Claude, localhost; unknown sites fall back to a generic page reference - always total, never lossy. Issues and parser PRs welcome.

## Paper

**Activity Frames: Deterministic Screen-Activity Compilation for Agent Memory and Replay**  
Nossa Iyamu, arXiv 2026

[![arXiv](https://img.shields.io/badge/arXiv-2608.05784-b31b1b.svg)](https://arxiv.org/abs/2608.05784) [![Hugging Face](https://img.shields.io/badge/🤗%20HF-Papers-yellow)](https://huggingface.co/papers/2608.05784)

```bibtex
@misc{iyamu2026activityframes,
  title={Activity Frames: Deterministic Screen-Activity Compilation for Agent Memory and Replay},
  author={Nossa Iyamu},
  year={2026},
  eprint={2608.05784},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
  url={https://arxiv.org/abs/2608.05784}
}
```

Built by [Nossa](https://github.com/nossa-y), maker of [Nocta](https://usenocta.app). MIT.
