# CLI reference

Entry point: `aframes` (installed with the package). Every command accepts `--help`.

## Commands

| Command | What it does |
|---|---|
| `aframes record` | Start the local capture engine (`--stop`, `--status`, `--audio`, `--foreground`) |
| `aframes today` | Today's activity frames |
| `aframes day 2026-07-03` | Any local day |
| `aframes context --hours 3` | Compact agent context block |
| `aframes apps` | Per-app time ledger |
| `aframes patterns --days 7` | Repetitive workflow detection |
| `aframes comms --hours 24` | Email/messaging surfaces + titles seen |
| `aframes steps --frame f-0002` | One frame's click-by-click script (replay view) |
| `aframes steps --find "task query"` | Resolve a task query to the demonstrated frame, no id needed |
| `aframes mcp` | Run the MCP stdio server |

## Shared flags (on the document-producing commands)

| Flag | Meaning |
|---|---|
| `--db PATH` | Path to the capture SQLite database (else default discovery / `$AFRAMES_DB`) |
| `-f {yaml,json,md,context}` | Output format (default yaml; YAML needs the `[yaml]` extra, else JSON fallback) |
| `--min-minutes N` | Drop frames shorter than N minutes (default 0.5) |
| `--include-text` | Include typed text snippets (off by default - deliberate privacy gate) |
| `--layout NAME` | Keyboard layout decode map (e.g. `azerty`) for typed-run reconstruction |
| `--debug` | Include sessionization debug info (why segments split/merge) |

## `aframes steps` specifics

| Flag | Meaning |
|---|---|
| `--frame ID` | Frame id (e.g. `f-0002`) from a compiled document |
| `--find QUERY` | Fuzzy task resolution ("linkedin invoice") to the right frame + URL slice |
| `--hours N` / `--day YYYY-MM-DD` | Window to index frames over (default 3 hours) |
| `--no-text` | Serve typed/pasted text as lengths only |
| `--max-steps N` | Cap the script length |

Output includes `step_count` and `unresolved_clicks` (clicks whose target element could not be named; 0 means a fully grounded script).
