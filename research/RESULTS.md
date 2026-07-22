# Breakthrough experiments - measured results

Corpus: `~/.screenpipe/db.sqlite` (working copy `/tmp/corpus_ro.sqlite`), read-only.
128,756 frames / 232,898 ui_events, 2026-05-11 .. 2026-07-22. Single user (n=1, author).
Tokenizer: tiktoken cl100k_base (same as paper).

## 1. Routine Overhead Ratio R  [FLAGSHIP]  (research/measure_overhead.py)

R = tokens a memoryless screenshot-driven agent spends RE-DERIVING a recurring
routine  /  tokens of the deterministic replay script that routine compiles to.
Numerator MODELED (Anthropic image tokens = w*h/750; per step = screenshot + 350 ctx + 180 reason).
Denominator MEASURED (tiktoken of replay script). Routines = frequent (>=3x) action n-grams
with >=2 named targets (specificity rule pre-declared to defeat the granularity trap).

| metric | action granularity | url granularity |
|---|---|---|
| **R median** | **343×** | 198× |
| R IQR (p25-p75) | 297 - 390× | 165 - 228× |
| R recurrence-weighted mean | 337× | 194× |
| R by routine length k | k3-4: 301×, k5-8: 358×, k9-15: 405×, k16+: 401× | ~200× |
| R across screen res (median) | 259× (1280×800) .. 425× (1728×1117) | 150 .. 245× |
| recurring routines found | 614 (maximal) / 1,726 n-grams | 261 |
| max occurrences of one routine | 181× | 25× |

**Honest denominator ladder (never cherry-pick one).** A computer-use agent pays ~2,509
tokens per routine STEP (dominated by the per-step screenshot). What "replay" costs depends
on what it sends:
| framing | denominator | R | meaning |
|---|---|---|---|
| **R_inject (operational headline)** | full guarded skill plan injected as context (~60 tok/step) | **60× median** (IQR 59-62, research/results_replay.json) | injecting the compiled routine costs ~60× fewer tokens than re-deriving |
| R_info (ceiling) | minimal action script / info content (~8 tok/step) | ~343× median | the routine's information content is ~340× smaller than re-deriving it |
| on-hit local replay | executor holds the plan, LLM out of the loop on a guard match | ~99% saving | bounded by guard coverage (0.43 median) and drift; reserved for live bench |

Lead with the conservative **~60×**; present 343× as the information-content ceiling. Never
stacked with the 86× compression or any cache discount (different token populations).

## 2. Repetition rate h  (same script, honest two-level)

- **h_specific (delegable routines, >=2 named targets): 9.0% (action), 13.1% (url)**
- h_raw (any recurring n-gram incl. generic typing/nav micro-structure): 83.1% (action)
- The gap 83%→9% is generic input micro-structure, reported explicitly. We do NOT claim the
  web-era 40-58% page-revisit constants (Tauscher/Adar/Teevan) as h; those are a different,
  inflated quantity (page visits ≠ delegable tasks). Measured desktop delegable-h ≈ 0.09-0.13.

Fleet token saving = h_specific × (1 - 1/R) ≈ 0.09 × 0.997 ≈ **9% of all action-step tokens**,
or **~99.7% of the tokens spent on the repeated fraction**. (Never stacked with 86× or cache.)

## TODO (this session)
- [ ] temporal holdout: fit routine table days 1-40, predict recurrence 41-61 (the ONE predictive claim)
- [ ] IVM byte-equality certification (rebuild==incremental)
- [ ] instrument card: capture overhead (bytes/day, %AX-tree, OCR duty) - "capture is not free"
- [ ] replay-plan compiler (shipped module + tests) - turns a routine into a guarded script
- [ ] public-dataset second subject (break n=1)

## 3. Replay-plan compiler (research/compile_replay.py) - the mechanism
20 recurring routines compiled into deterministic guarded action plans, LLM-free.
- median compile cost B = **0.51 ms**, 0 tokens (CPU only)
- median plan payload = 253 tokens; median guard coverage = 0.43 (fraction of steps with a concrete element/url guard; rest fall back to role/app guards - honest, reflects element-tree gaps)
- Guards = correctness contract: replay acts only on guard match, else deopts to a full agent.

## 4. IVM / reproducibility certification (research/certify_ivm.py) - PASS
- reproducibility byte-identical CONTENT: PASS across 51 active days
- rebuild == incremental: PASS ; append-only earlier-days-unchanged: PASS
- compile cost grows with history? NO (0.86× first-half vs second-half) => O(|delta|)
- median per-day compile 221 ms on this larger corpus (paper's 68 ms was one specific day)
- HONEST caveat surfaced + fixed: one emission-metadata field `generated_at` (wall-clock) must be excluded from the byte-identical claim; compiled content is identical. Concede DBSP/event-sourcing lineage - claim only a CI-checkable byte-equality contract, not IVM novelty.

## 5. Instrument card (research/measure_corpus.py) - capture is NOT free
- DB 9.52 GB ; 0.187 GB/active-day ; 128,756 frames ; 51 active days ; 2,525 frames/active-day
- 81.5% frames carry an accessibility tree (replay-grounding ceiling; up from paper's ~41%)
- 96.0% frames carry OCR text (on-device OCR duty)
- ui_event mix: 149,723 click / 67,026 text / 8,447 key / 5,125 clipboard / 2,577 app_switch

## 6. Temporal holdout h (research/measure_corpus.py) - the predictive claim
- train days 1-40 -> 4,847 routine signatures
- **h out-of-sample predicted on days 41-51 = 7.7%** (in-sample train 8.6%) - minimal overfit
- This is the non-circular, forward-looking desktop routine-recurrence rate the economics may use.

## Reserved for Nossa (safety rail)
Live three-arm cost-per-successful-task (memoryless agent / agent+injected-plan / local replay)
on his real authenticated routines, with real Anthropic usage JSON. Built + one command away
(research/bench_three_arm.py); needs his machine + accounts + eyes. Converts modeled R into a
demonstrated dollar saving.

## 7. Three-arm bench (modeled, research/bench_three_arm.py)
On the repeated routines, weighted by occurrences (Sonnet-class $3/$15):
- Arm A memoryless: $125.81 / 32.8M tok
- Arm B agent+injected plan: $20.95 -> **83.3% saving**
- Arm C deterministic local replay: $72.61 -> **42.3% saving** (capped by 0.43 guard coverage; the 57% deopt fraction pays full price - rises as element-tree coverage climbs toward the measured 81.5%)
- `--live` prints the operator wiring; live billed run reserved for Nossa.
