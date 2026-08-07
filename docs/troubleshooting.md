# Troubleshooting

Keyed by the error or symptom you actually see.

## `RecorderDBNotFound`

No capture database was found. Either the capture engine isn't running (`aframes record`, check with `aframes record --status`), or you're on a machine with a different recorder - point `$AFRAMES_DB` (or `--db`) at your capture SQLite file.

## Output is JSON when I asked for YAML

The `[yaml]` extra isn't installed. `pip install "activity-frames[yaml]"`. Without PyYAML the CLI deliberately falls back to JSON rather than failing.

## `CaptureError: Unexpected engine archive layout`

The downloaded capture-engine build didn't match what the provisioner expects (hash verification protects you here). Re-run `aframes record`; if it persists, an engine release may be mid-publish - open an issue with the printed hash.

## Empty or tiny documents on macOS

The capture engine needs **Screen Recording** permission (System Settings -> Privacy & Security). After granting (or after any engine update), stop and restart capture: `aframes record --stop && aframes record`.

## Frames exist but typed text is missing

That's the default. Typed-text content only appears with the explicit `--include-text` opt-in; input *counts* are always present.

## `unresolved_clicks > 0` in `aframes steps`

Some clicks had no nameable target element (the accessibility tree didn't cover that surface at capture time). The script is still ordered and usable; unresolved steps carry coordinates context instead of names, and an agent should treat them as lower-confidence.

## Linux

There's no prebuilt Linux capture engine yet. The compiler itself runs fine on Linux (it's tested in CI): run your own recorder and point `$AFRAMES_DB` at its database (compatible `frames` / `ui_events` / `elements` tables).

## Weird characters in typed runs (wrong letters)

Your capture stack may record physical key positions as QWERTY while you type another layout. Pass `--layout azerty` (or your layout) to decode.

Still stuck? Open an issue or email n@usenocta.app.
