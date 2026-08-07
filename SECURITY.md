# Security Policy

activity-frames deals with unusually sensitive data - a local database of what was on your screen - so we take reports seriously.

## Reporting a vulnerability

Email **n@usenocta.app** with details (proof of concept appreciated). You'll get a reply within 72 hours. Please don't open a public issue for anything exploitable before we've had a chance to fix it.

## Scope

Reports we especially care about:

- Anything that causes capture data to leave the machine (the design guarantee is: nothing is uploaded, ever).
- Typed-text content appearing in compiled output without the explicit `--include-text` opt-in.
- Weaknesses in the capture-engine provisioning chain (`aframes record` downloads a pinned build and verifies its published sha256 before first run).
- Prompt-injection hazards in compiled output beyond the documented one (window titles / page entities are third-party text and must be treated as data, not instructions).

## Out of scope

- Security of the capture database at rest on a machine an attacker already controls - protect it like any sensitive file (FileVault, permissions).
- Vulnerabilities in third-party recorders you point `$AFRAMES_DB` at.

## Supported versions

The latest release on PyPI. Fixes ship as patch releases; nothing is backported.
