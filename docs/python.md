# Python API reference

Zero-dependency core; `pip install activity-frames`.

## The one class most users need

```python
from activity_frames import ActivityLog

log = ActivityLog()                  # finds the default capture DB, or honors $AFRAMES_DB
doc = log.day()                      # today, structured
doc = log.day("2026-07-03")          # any local day
doc = log.recent(hours=2)            # last N hours
print(log.context(hours=2))          # compact paste-ready context block
```

Documents are plain Python data (dicts/lists) following the schema contract in [SPEC.md](../SPEC.md): coverage, frames (app, site, start/end, duration, typed pages, input counts, evidence pointers), gaps, and blind spots.

## Lower-level building blocks

All exported from `activity_frames`:

| Export | What it is |
|---|---|
| `Database`, `find_default_db`, `RecorderDBNotFound` | Capture-DB access (read-only) and discovery |
| `segments`, `app_ledger`, `coverage`, `Segment`, `Coverage` | Sessionization primitives (dwell, gaps, flicker merge) |
| `context_block`, `to_json`, `to_yaml`, `to_markdown` | Emitters |
| `parse_url`, `PageRef` | Deterministic URL -> typed entity parsing (25+ site parsers, total fallback) |
| `detect_patterns`, `WorkPattern` | Repetitive-workflow detection |
| `comm_surfaces`, `CommSurface`, `TitleItem`, `COMM_KINDS` | Email/messaging surfaces (titles only) |

## Bring your own capture DB

```python
import os
os.environ["AFRAMES_DB"] = "/path/to/your/capture.db"   # or pass db path where accepted
```

Any SQLite DB with compatible `frames` / `ui_events` / `elements` tables works; the default engine is provisioned by `aframes record`.

## Notes

- YAML emission needs the optional extra: `pip install "activity-frames[yaml]"` (otherwise JSON fallback).
- Compilation is pure code - no model calls, no network. Same DB + window = identical output.
