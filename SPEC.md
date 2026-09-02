# Activity Frames Specification

**Version:** 1 (`schema_version: 1`)
**Status:** stable
**Purpose:** a standard, deterministic representation of human computer activity for consumption by AI agents.

## 1. Motivation

Screen recorders capture instants: thousands of snapshot rows per day, each one saying "at time T, app A showed window W at URL U". Agents cannot reason over that efficiently. They need bounded episodes: "the user was in app A on site S from T1 to T2, looked at these pages, typed this much."

This spec defines that episode format, and the rules that keep it trustworthy:

1. **Measured, not guessed.** Every field at this tier is derivable by deterministic code from recorder data. No intent labels, no summaries, no model output.
2. **Reproducible.** The same input database and window must always produce the same document.
3. **Evidenced.** Every frame points back to the raw rows it was compiled from.
4. **Honest about absence.** Coverage gaps and known blind spots are part of the document, not an implementation detail.

## 2. Document structure

A document is a single JSON/YAML object:

```yaml
schema_version: 1
generated_at: "2026-07-04T21:14:03Z"     # UTC, ISO-8601
source:
  recorder: nocta-recorder                # schema family of the capture DB
window:
  start_utc: "2026-07-04T07:00:00"
  end_utc: "2026-07-05T07:00:00"
  day: "2026-07-04"                       # present when the window is a local day
coverage: { ... }                         # section 3
frames: [ ... ]                           # section 4
blind_spots: [ ... ]                      # section 6
```

All times inside `coverage` and `frames` are **local** wall-clock times (`HH:MM` or `HH:MM:SS`), because they describe a human's day. The `window` is UTC, because it describes a database query.

## 3. Coverage

What the recorder actually saw. Consumers must treat anything outside covered time as unknown, not as inactivity.

```yaml
coverage:
  first_activity: "09:12"
  last_activity: "18:47"
  active_minutes: 342        # distinct minutes with at least one frame
  span_minutes: 575          # last - first
  coverage_pct: 59           # active / span, capped at 100
  frames_analyzed: 4211      # raw snapshot count in the window
  distinct_apps: 11
  gaps:                      # periods >= 5 min with no capture
    - {start: "12:30", end: "13:15", minutes: 45}
```

## 4. Frames

A frame is one bounded stretch of attention in a single context. The context key is `(app, site)`: the site is the URL host for browser activity, absent otherwise.

```yaml
- id: f-0007                  # stable within the document
  app: "Google Chrome"
  site: "linkedin.com"        # omitted for non-browser apps
  start: "20:24:04"           # local
  end: "20:42:11"
  duration_min: 18.0          # ACTIVE minutes (dwell-based, see 5.1)
  wall_min: 21.5              # end - start; emitted when it differs by > 1 min
  windows:                    # up to 3 most-seen window titles
    - "Search | LinkedIn"
  pages:                      # typed page references, browser frames only
    - {kind: people_search, entity: "cto berlin", count: 2}
    - {kind: profile, entity: "john-doe"}
    - {kind: company, entity: "acme-ai"}
  input:                      # omitted when empty
    keys: 214                 # keystrokes + typed characters
    clicks: 31
    copies: 2
  interruptions:              # brief context switches folded in (see 5.3)
    - {app: "Slack", seconds: 12}       # site key present for browser flickers
  evidence:
    frame_ids: "99871..100147"   # raw row id range in the source DB
```

### 4.1 Page references

A page reference types a URL into something an agent can read: `kind` (what sort of page), optional `entity` (the human-relevant identifier), optional `count` (views within the frame, emitted when > 1).

Standard kinds include: `profile`, `company`, `feed`, `messaging`, `people_search`, `search`, `repo`, `pull_request`, `issue`, `code`, `doc`, `sheet`, `email`, `video`, `channel`, `post`, `question`, `booking`, `design`, `ai_chat`, `local_dev`, `notifications`, and the fallback `page`. Kinds are open: producers may add site-specific kinds, but must keep them deterministic functions of the URL.

### 4.2 Input privacy rule

Input **volume** (counts) is part of the standard document. Input **content** (typed text) must be excluded by default and only included on an explicit opt-in from the operator of the producing tool. When opted in, the reference producer emits it as `input.text`.

## 5. Determinism rules

Implementations must produce identical documents for identical inputs. The reference constants:

### 5.1 Dwell
Capture is event-driven: a frame is stored when the screen changes. A raw frame contributes `min(gap_to_next_frame, 90s)` of active time when the gap to the next frame is within the session gap (`300s`); the final frame of a session contributes `0`. The cap prevents a static screen (or an absent user) from earning unbounded credit. `duration_min` is the sum of dwells inside the frame.

### 5.2 Session gap
A gap larger than `300s` between consecutive raw frames closes the current activity frame and is a candidate coverage gap (reported when >= 5 min).

### 5.3 Flicker merge
The sequence A -> B -> A collapses into a single A frame when B's own frame span (first to last of B's raw frames; a single-snapshot B spans `0s`) is at most `20s` and no session gap intervenes on either side of B. B is recorded in `interruptions` with its measured `seconds`: B's active time including its dwell-capped gap back to A, which can exceed the `20s` span threshold. B's time is **not** added to A's `duration_min`. Nothing is silently dropped.

### 5.4 Minimum duration
Producers may offer a `min_minutes` filter for consumer convenience. Filtered-out frames are omitted with the count disclosed in the document (`omitted: {below_min_minutes: N, min_minutes: X}`); they must never be silently merged into neighbors.


### 5.5 Capture devices

Each monitor records its own frame stream (`device_name`). Segmentation runs per device, so simultaneous monitors do not fragment each other's frames; the document lists all devices' frames sorted by start time, which means frames MAY overlap in time. Input events are assigned to exactly one containing segment (ties across devices resolved by the nearest captured frame), so input volume is never double-counted. A consequence to disclose: the same app visible on two monitors at once earns active time on both.

### 5.6 Event-aware segmentation (opt-in)

Consumers may optionally provide authoritative event metadata from external systems such as `git.merge`, `pr.approved`, or `deploy.success`. These are not inferred from screen activity and are supplied explicitly by callers. The event type is a simple string; the package does not classify, rank, or guess event meaning.

An `Event` object is a minimal record:

```python
Event(event_type="git.merge", timestamp=1700.0, source="ci", priority=10)
```

When a caller passes `events` alongside the normal segmentation call, the existing context/session segmentation remains the base pipeline. After those segments are formed, the optional event boundary step may split an existing segment only at an observed boundary between consecutive captured frames. It never invents a frame at the event timestamp. When an event falls between two captured frames, the implementation chooses the closer inter-frame midpoint as the deterministic split point; ties are resolved in timestamp order and then by event index. This keeps the result reproducible for identical inputs.

The event-aware layer is intentionally opt-in. If `events=()` and `forced_event_types=()` and `force_priority is None`, the output is identical to the old implementation. `forced_event_types` allows splitting only on specific event types; `force_priority` allows splitting on any externally supplied event whose `priority` is at least the threshold. Both filters are additive: an event qualifies when it matches either a forced type or the priority threshold. Events outside all segments are ignored safely.

## 6. Blind spots

Every document carries a `blind_spots` list: plain-language statements of what the capture pipeline systematically cannot see (e.g. "browser URLs are only captured for browser apps"). Producers must not remove entries to make output look more complete.

## 7. Tier 2: inferred fields (extension, not part of this package)

Tools MAY extend documents with interpretation: task labels, project clusters, meeting summaries. Such fields are **inference**, and the spec requires them to be:

- namespaced under `inferred` (at document or frame level),
- tagged with `confidence: high | medium | speculative`,
- tagged with `evidence`: which measured fields or raw rows support the inference.

```yaml
inferred:
  - label: "LinkedIn prospecting"
    frames: [f-0007, f-0009]
    confidence: medium
    evidence: "people_search + 2 profile views + 1 company page in 18 min"
```

A consumer must always be able to strip everything under `inferred` and be left with a purely measured document. This package emits tier 1 only.

## 8. Versioning

`schema_version` increments only on breaking changes to tier-1 field semantics. Additive fields do not bump the version. Consumers should ignore unknown fields.

Producers may emit underscore-prefixed diagnostics (the reference implementation's `--debug` flag adds a `_debug` block explaining segment splits/merges). Underscore-prefixed fields are not part of the schema and consumers must ignore them.

## 9. Sources

The reference producer reads the local SQLite database written by the built-in capture engine (nocta-recorder, provisioned by `aframes record`). Required surface: a `frames` table (`id`, `timestamp` UTC ISO, `app_name`, `window_name`, `browser_url`, `focused`; `device_name` optional, treated as one stream when absent), and optionally `ui_events` (input volume) and `elements` (click resolution) - the compiler degrades gracefully when the optional tables are missing. Any capture system can be supported by a producer that satisfies sections 2 through 6; `source.recorder` identifies the schema family.
