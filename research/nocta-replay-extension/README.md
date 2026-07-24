# Nocta Replay (extension) - DEPRECATED

> **DEPRECATED (2026-07-23, Nossa's call).** The executor is NOT an extension. It is a
> deterministic script over `agent-browser` (the nocta-execute substrate): `../replay_agentbrowser.py`.
> That reuses the mature agent-browser daemon (stealth, cookies, real profile) and is ONE
> mechanism with nocta-execute (nocta-execute = LLM interpreter/deopt; replay_agentbrowser = compiled
> fast-path). This extension proved the grounding-ladder LOGIC works (headless, real page, zero
> vision, blocked Send); that logic was ported into replay_agentbrowser.py. Kept only as a reference
> for the grounding ladder, and as a possible future shippable consumer product. Do not build on it.

---

# (reference) Nocta Replay extension - the grounding-ladder proof

This is the real thing that was a `NotImplementedError` stub: it takes a compiled
routine plan and replays it on a page by locating each target through the DOM's
accessibility info and visible text - **no vision model, no screenshots** - and it
**never sends** unless you explicitly opt in.

It is "arm C" for web routines, running in your own Chrome with your own logged-in
sessions. Native apps (iMovie, Finder, Terminal) are out of scope - they need the
OS-level executor.

## What's proven
`test/run_headless.mjs` drives a real headless Chromium against `fixtures/compose.html`
and asserts the executor:
- grounds targets by tier 1 (accessibility: aria-label / role+name) and tier 2 (visible text),
- deopts (hands back for a vision step) when a target is absent,
- BLOCKS a destructive step (Send) via the safety gate,
- actually clicks and types via DOM events,
- never clicks Send.

Run it:
```bash
cd research/nocta-replay-extension
node test/run_headless.mjs      # (playwright chromium; a local node_modules symlink is used)
```
Last run: 5/6 steps grounded with zero vision, 1 deopt, Send blocked, PASS.

## The grounding ladder (src/executor.js)
Pure DOM, no extension APIs, so it is unit-testable headless and identical in the page:
1. **Tier 1 - accessibility:** exact `aria-label`, then role + accessible-name, then accessible-name, then `aria-label` contains.
2. **Tier 2 - visible text:** exact text / value / placeholder, then smallest containing element.
3. **Deopt:** neither tier found it -> the step is marked for a vision fallback (a future version messages an LLM; today it is logged).

Safety: any element whose accessible name matches a destructive verb (send, post, publish,
pay, delete, ...) is blocked unless `{allowDestructive:true}`.

## Load it in Chrome
1. `chrome://extensions` -> enable Developer mode -> "Load unpacked" -> pick this folder.
2. Open a page (e.g. LinkedIn messaging, logged in as your test account).
3. Click the Nocta Replay toolbar icon, paste a plan, keep "Dry run" checked first, click Replay.
4. Dry run locates + guard-checks every step and shows the tier hits without clicking. Uncheck Dry run to actually execute. Leave "Allow destructive" OFF so it stops before Send.

## Plans come from the compiler
`research/compile_replay.py` already emits plans in the exact shape the executor consumes:
```json
{"op": "click", "target": "Compose message", "role": "AXButton",
 "guard": {"expect_element": "Compose message", "expect_role": "AXButton", "expect_url_template": "linkedin.com/messaging/*"}}
```
For `type` steps, add a `"value"` with the text to enter (the compiler leaves it as a
`<value>` placeholder because the content is what varies per run). Everything else transfers directly.

## How this measures the token win (arm A vs arm C)
- **Arm C (this):** replays the routine for ~0 LLM tokens - the executor is plain DOM code; the model is only involved on a deopt step.
- **Arm A (baseline):** a vision agent doing the same task screenshots and reasons every step (~2,500 tokens/step).
So on a clean run the token bill for the routine drops from thousands to roughly zero, and the
grounding-tier log tells you exactly how many steps stayed deterministic vs deopted. To get a
billed A-vs-C number, run arm A through a computer-use agent on the same routine and compare;
this extension is the arm-C half, now real.

## Status
v0.1: executor + grounding ladder + safety + popup + headless proof, all working. Not built yet:
receiving plans from the Nocta app over native messaging, and routing deopt steps to an LLM for
the vision fallback (both noted in `src/background.js`).
