# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and versions follow semantic
versioning. The document schema version is tracked separately in [SPEC.md](SPEC.md).

## [Unreleased]

### Added
- **Communications view**: `ActivityLog.communications()` /
  `comm_surfaces()`, the `get_communications` MCP tool, and the
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
  Stack Overflow, Calendly, and AI-chat sites, plus a subdomain/path heuristic
  layer (sign-in, dashboard, email, calendar, meeting) and a total generic
  fallback.
- **Built-in capture**: `aframes record` provisions and runs a pinned,
  MIT-licensed screenpipe build, sha512-verified before first run (audio off
  by default; `--status` checks that frames are actually flowing and points
  at macOS permissions when they are not).
- **MCP server**: zero-dependency stdio JSON-RPC server exposing `get_context`,
  `get_activity`, `get_day_summary`, and `get_patterns`.
- **CLI** (`aframes`): `record`, `today`, `day`, `context`, `apps`, `patterns`,
  `mcp`, with JSON / YAML / Markdown / context-block output.
- **Python API** (`ActivityLog`) and workflow-pattern detection.
- Test suite (58 tests) and CI on macOS and Linux (Python 3.9, 3.11, 3.13).

[0.1.0]: https://github.com/nossa-y/activity-frames/releases/tag/v0.1.0
