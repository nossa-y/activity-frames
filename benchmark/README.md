# Downstream question-answering benchmark

This is the harness behind Section VI-B of the activity-frames paper. It asks a
simple question: **if you give an agent one day of your activity, which
representation lets it answer questions about that day most faithfully?**

It compares three representations of the *same* captured day:

| Representation | What it is |
|---|---|
| **raw rows** | the raw capture rows serialized as JSON (what a search API returns) |
| **LLM summary** | a natural-language summary of those rows, written by the same agent |
| **activity frames** | the deterministic per-app ledger (`aframes apps`) plus the compact context block (`aframes day -f context`) |

An agent answers a fixed set of ground-truth questions from each representation.
Answers are graded against an **independent oracle** that computes the true facts
directly from the capture database, deliberately *without* using the
activity-frames compiler, so the reference is not circular.

## Why this is honest, not rigged

The single biggest risk in a benchmark you build for your own system is that you
quietly tilt it. Here is exactly where the thumb could be on the scale, and what
we do about each:

- **The oracle is independent of the thing it grades.** `oracle.py` computes
  facts with a standard inactivity-timeout dwell (credit each frame the gap to the
  next, capped at 60 s) straight from the raw `frames` table. It shares no code
  with the compiler. We separately verify the compiler agrees with it: across the
  eight evaluated days, activity-frames' `active_minutes` matches the oracle to a
  **median of 0.9 min** (seven of eight days within 2.3 min; the eighth, a
  heavily fragmented late-night day, differs by 77 min because the compiler's 90 s
  dwell cap credits more than the oracle's 60 s), and distinct-app counts agree
  exactly or within one on every day.

- **The activity-frames representation is not free of advantage, and we say so.**
  It includes the measured per-app ledger, which already contains the durations
  many questions ask for; the raw-rows and summary agents must derive those
  durations themselves. That *is* the point of the compiler, but it means the
  duration questions are partly a test of "did the deterministic pipeline already
  do the arithmetic," not only "can the agent read." Read the numbers with that in
  mind.

- **Grading tolerances do not decide the winner.** Numeric answers pass within
  30 %, times within 45 min. That sounds generous, so we swept it: at a strict
  **10 % / 15 min** band the ranking is unchanged (activity frames 95.3 %, raw
  80.4 %, summary 66.1 %), and the summary is so far off that widening the band to
  50 % lifts it only to 67.9 %. The result is not an artifact of the threshold.

- **Two question types are excluded, on purpose, and here is which.** Longest
  single-session length depends on where you draw the session-gap boundary (a
  convention, not a fact), and per-profile recall (e.g. "how many distinct
  LinkedIn profiles") exceeds the compact block's token budget on busy days. Both
  would measure a convention or a deliberate compression choice rather than
  faithfulness, so they are not scored. Including them would lower the
  activity-frames number; we flag the exclusion so you can decide for yourself.

- **Absent-fact probes catch invention.** A quarter of the questions ask about
  apps and domains that were never used ("did you open Photoshop / visit
  netflix.com"). All three representations score 100 % here: none of them
  hallucinate. The gap between them is entirely about *magnitudes*, not fabricated
  facts, and the paper says so.

## Day selection

The evaluated days are the **seven most recent consecutive active days plus the
nearest preceding day whose raw serialization overflows the model's context
window** (that overflow day is included specifically to test the regime where raw
rows and summaries do not fit at all). This is a fixed rule, not a hand-picked
list.

## Known limitations

- **Single model.** The agent is Claude Sonnet 4.5, which also writes the summary
  baseline. The mechanism the benchmark isolates (deterministic magnitudes vs a
  summary's non-deterministic, duration-inflating paraphrase) is model
  independent, but the numbers here are one model. The harness is written so you
  can swap in another (see `harness.py:claude()`).
- **Single answering pass.** Accuracy is one answering run per representation;
  reproducibility (`R=3`) is measured for the representations themselves (the
  activity-frames block is byte-identical across regenerations; the summary is
  three distinct texts). Per-run confidence intervals on accuracy are future work.
- **Sampling is not pinned.** Calls go through the `claude` CLI at its default
  sampling; there is no temperature/seed control here, so exact answer text will
  vary run to run. The graded *ranking* is stable; individual cells may move by a
  question.
- **The corpus is private.** The 55-day capture database is one person's real
  screen activity and is not shipped. Point the harness at your own capture
  database to run it; you will get your own numbers, not ours.

## Running it

Requires a local capture database (default `~/.screenpipe/db.sqlite`) and the
`claude` CLI authenticated. From this directory:

```bash
# 1. ground truth, independent of the compiler
python3 oracle.py 2026-07-01 2026-07-02 2026-07-03 > oracle.json   # your days

# 2. build the three representations + token counts
python3 representations.py 2026-07-01 2026-07-02 2026-07-03

# 3. generate ground-truth questions from the oracle
python3 questions.py

# 4. run the agent over every representation and grade vs the oracle
python3 harness.py

# 5. aggregate into the paper's numbers + LaTeX table
python3 analyze.py
```

Every file this produces (`oracle.json`, `questions.json`, `results.json`,
`reps/`) is derived from your personal activity and is git-ignored. Do not commit
it.

## Files

| File | Role |
|---|---|
| `oracle.py` | independent ground-truth facts from the raw DB (no compiler) |
| `representations.py` | builds the three representations + `cl100k_base` token counts |
| `questions.py` | deterministic ground-truth question set (with absent-fact probes) |
| `harness.py` | runs the agent over each representation, grades vs oracle |
| `analyze.py` | pooled accuracy, per-category breakdown, LaTeX table |
