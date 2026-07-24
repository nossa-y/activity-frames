# Exhaustive validation suite - Routine Overhead Ratio

Everything here validates or extends the breakthrough findings on **your** capture
data. Read-only. Two layers: 11 offline experiments you run in one command, and a
3-step live money-demo you run by hand (the reserved step).

## Setup (30 seconds)

```bash
# 1. Copy your recorder DB (NEVER point the suite at the live file)
cp ~/.nocta/data/db.sqlite /tmp/corpus_ro.sqlite

# 2. Run the offline suite
cd ~/nocta/activity-frames/research/suite
AFRAMES_CORPUS=/tmp/corpus_ro.sqlite ./run_all.sh
#    add --with-network to also run the Mind2Web replication (t12)
```

Output: a `suite_report.md` table (PASS/FAIL per test) plus per-test JSON in
`results/`. Total offline runtime is roughly 8-15 minutes (t04 and the wrappers
that re-mine dominate; t05 and t10 are fast).

Requirements: Python 3.9+, `tiktoken` (already a repo dep). t12 additionally needs
`pip install datasets huggingface_hub` and network.

## The offline experiments

| test | validates (finding) | method | expected on your corpus |
|---|---|---|---|
| **t01** overhead_R | R_info ceiling (~343x action / 198x url) | mine routines, price re-derivation / minimal-script | R_info median 200-500x action; grows with routine length |
| **t02** replay_compile | R_inject operational (~60x) + guarded plans | compile routines to guarded plans, B and plan tokens | R_inject median 30-120x; B < 50 ms; 0 tokens |
| **t03** recurrence_h | out-of-sample recurrence h (~7.7%) | temporal holdout: mine days 1-40, predict 41-51 | h_out 2-30%; overfit gap < 5 points |
| **t04** h_null_baseline | h is real sequential structure, not marginal skew | frequency-preserving within-session shuffle surrogate | h_obs >> h_null; z > 2; ~0% surrogates reach h_obs |
| **t05** entropy_predictability | predictability surface (compressibility, honest) | LZ entropy rate + Fano ceiling + alphabet/length controls | Pi_max surface 0.6-0.95 across app/site/action, converging |
| **t06** ivm_certify | reproducibility as a certified property | recompile 51 days, byte-diff, incremental vs rebuild | PASS: byte-identical, rebuild==incremental, O(delta) |
| **t07** instrument_card | capture is NOT free (honest cost) | measure GB/day, a11y coverage, OCR duty | a11y ~81%, OCR ~96%, ~0.2 GB/active-day |
| **t08** bench_modeled | modeled three-arm token/dollar saving | price arms A/B/C from measured artifacts | inject saves ~83%, replay ~42% (capped by guard coverage) |
| **t09** R_sensitivity | R is not a knob artifact | sweep numerator grid + mining threshold | R_info stays low-hundreds across 27+ cells |
| **t10** determinism_stress | the instrument itself is deterministic | run mine+price twice, sha256 the outputs | identical hashes |
| **t11** routine_atlas | where the automatable work concentrates | attribute routines to app/site/hour | ranked apps/sites + hour histogram + top-yield routines |
| **t12** public_replication | R breaks n=1 | same numerator on Mind2Web (needs network) | public R in 100-500x, brackets your ~343x |

Each `tXX.py` is standalone: `AFRAMES_CORPUS=/tmp/corpus_ro.sqlite python3 tXX.py`.
Each prints PASS/FAIL against an expected band and writes `results/tXX.json`.

### What a PASS means
A PASS means your data reproduces the finding within the expected band. The bands
are deliberately wide (order-of-magnitude), because the claim is order-of-magnitude:
agents pay one to two orders of magnitude more to re-derive routines than to replay
them. A FAIL is interesting - it means your usage differs from the reference corpus,
and the JSON tells you how.

## Live: the billed money-demo (the reserved step)

This is the one thing not run autonomously - it needs your machine, your accounts,
and your eyes on reversible targets. It converts the modeled ~60x / 83% into a
billed dollar figure with real Anthropic usage JSON. Three steps:

```bash
cd live
# 1. select replay-SAFE routines (excludes anything that sends/posts/pays) and write a manifest
AFRAMES_CORPUS=/tmp/corpus_ro.sqlite python3 L01_pick_routines.py
#    -> review run_manifest.json, trim to 6-10 routines you are comfortable executing

# 2a. DRY RUN first (no API calls, no actuation) - proves the pipeline + scoring end to end
python3 L02_three_arm_live.py
python3 L03_score_billed.py            # prints modeled billed numbers from the dry run

# 2b. REAL run - wire the two TODO(real) hooks in L02 to your computer-use agent and a
#     guard executor, set ANTHROPIC_API_KEY, then:
python3 L02_three_arm_live.py --real
python3 L03_score_billed.py            # prints the billed headline + bootstrap CI
```

**Safety:** L01 excludes routines touching destructive verbs (send, post, delete,
pay, submit, ...). Still review the manifest. Use draft/sandbox targets. Never bench
a routine that has an irreversible effect.

Arms: A = memoryless agent, B = agent + injected compiled plan, C = deterministic
local replay with deopt-to-A on a guard miss. L03 reports R_billed = A/C and A/B,
dollar savings, success rates, and a 95% bootstrap CI on the saving.

## Provenance
These tests wrap and extend the committed instruments in `../` (measure_overhead,
compile_replay, measure_corpus, certify_ivm, bench_three_arm, measure_overhead_public).
The findings they validate are documented in `../RESULTS.md` and
`~/nocta/activity-frames-breakthrough/REPORT.md`.
