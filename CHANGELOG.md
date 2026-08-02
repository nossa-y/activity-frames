# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions follow semantic
versioning. The document schema version is tracked separately in [SPEC.md](SPEC.md).

## [Unreleased]

### Changed
- `context_block()` serializes only the frames it emits; output is
  byte-identical (#23).

### Fixed
- Revisit-then-dwell page counts: `_pages_for_segment()` credited dwell frames
  to the last-appended page instead of the current page (A,B,A,A counted
  A:2/B:2 instead of A:3/B:1). Page `count` values in emitted documents and
  context blocks change where a page was revisited then dwelled on; totals,
  ordering, and evidence are unchanged (#31).
- Accurately count app sessions and longest session duration in `app_ledger()` across app switches and 20s flickers (`aframes apps` and `get_day_summary` MCP tool) (#36).

## [0.2.2] - 2026-07-28

### Added
- **`get_steps` MCP tool and `aframes steps` CLI**: expand one activity frame
  into its ordered click-by-click script (clicks with element name / role /
  AXIdentifier / URL, typed runs, pastes, focus changes) - the replay view of
  a demonstrated run. `aframes steps --find "task query"` resolves a
  natural-language task to the demonstrated frame without a frame id.
- Entity parsers for Atlassian (Jira/Confluence).
- `--debug` CLI flag: include sessionization debug info (why segments split
  or merge).
- End-to-end MCP agent integration example under `examples/` (#19).
- Date-range header on `aframes patterns` output, and an explicit message
  when no patterns are detected (#29).

### Changed
- Engine downloads fail closed: sha256 verification now covers every
  published nocta-recorder build, and `aframes record` refuses to install a
  build it has no pinned hash for.
- The package version is single-sourced from `activity_frames.__version__`
  (hatchling dynamic version).
- The default capture database is resolved by newest mtime instead of
  candidate order.

### Fixed
- O(n²) page scan in `_pages_for_segment()`: large sessions compile
  measurably faster, byte-identical output (#26).
- Product Hunt profile URLs (`producthunt.com/@user`) now parse to a
  `profile` page reference.

### Docs
- `aframes comms` documented in the README CLI block.
- SPEC dwell and flicker-merge formulas aligned with the reference
  implementation (the final frame of a session contributes 0 dwell; a
  flicker's recorded `seconds` is its measured active time and may exceed
  the 20s span threshold).

## [0.2.1] - 2026-07-25

### Added
- Entity parsers for GitLab, Slack, Linear, and Crunchbase.

### Changed
- Capture engine is provisioned as [nocta-recorder](https://github.com/nossa-y/nocta-recorder)
  (MIT); the compiler stays engine-agnostic via `$AFRAMES_DB`.

### Packaging
- The source distribution ships the library and docs only; research artifacts,
  the paper, and benchmarks are excluded from the published package.

## [0.2.0] - 2026-07-16

### Added
- **Communications view**: `ActivityLog.communications()` and the
  module-level `comm_surfaces()`, the `get_communications` MCP tool, and the
  `aframes comms` CLI command — email/messaging/notification surfaces
  with the window titles measured on each (timing, counts, frame-id
  evidence). Titles only, measured tier: message bodies are never read;
  a client that doesn't put the conversation in its window title leaves
  only its presence to report.

## [0.1.0] - 2026-07-04

Initial release.

### Added
- **Schema v1** ([SPEC.md](SPEC.md)): two-tier document format separating measured
  fields from an optional confidence-tagged inferred tier, with coverage gaps,
  blind spots, and evidence pointers as first-class elements.
- **Deterministic compiler**: dwell-capped sessionization (per capture device,
  so multi-monitor streams never fragment each other), session-gap detection,
  flicker merging with interruption records, and single-assignment input
  accounting across overlapping monitor segments.
- **Enrichment library API** (`activity_frames.enrich`): nearest-frame app
  attribution, coordinate-based click resolution against the recorded element
  tree (with neighbor-frame rescue), and optional keyboard-layout decoding.
- **Entity typing**: deterministic URL parsers for LinkedIn, GitHub, Google
  (Search/Docs/Gmail/Maps/Meet/Calendar), YouTube, X, Instagram, Reddit, Luma,
  Partiful, Product Hunt, Vercel, Supabase, Stripe, Discord, Notion, Figma,
  Stack Overflow, Calendly, AI-chat sites, and localhost, plus a
  subdomain/path heuristic layer (sign-in, dashboard, email, calendar,
  meeting) and a total generic fallback.
- **Built-in capture**: `aframes record` provisions and runs a pinned,
  MIT-licensed capture-engine build, sha256-verified before first run (audio off
  by default; `--status` checks that frames are actually flowing and points
  at macOS permissions when they are not).
- **MCP server**: zero-dependency stdio JSON-RPC server exposing `get_context`,
  `get_activity`, `get_day_summary`, and `get_patterns`.
- **CLI** (`aframes`): `record`, `today`, `day`, `context`, `apps`, `patterns`,
  `mcp`, with JSON / YAML / Markdown / context-block output.
- **Python API** (`ActivityLog`) and workflow-pattern detection.
- Test suite (58 tests) and CI on macOS and Linux (Python 3.9, 3.11, 3.13).

[0.2.2]: https://github.com/nossa-y/activity-frames/releases/tag/v0.2.2
[0.2.1]: https://github.com/nossa-y/activity-frames/releases/tag/v0.2.1
[0.2.0]: https://github.com/nossa-y/activity-frames/releases/tag/v0.2.0
[0.1.0]: https://github.com/nossa-y/activity-frames/releases/tag/v0.1.0
