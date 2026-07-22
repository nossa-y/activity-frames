"""Three-arm cost-per-successful-task bench harness.

Arms, on a set of recurring routines:
  A. memoryless frontier computer-use agent  (re-derives every time)
  B. same agent + injected compiled routine plan  (recognizes + follows)
  C. deterministic local replay of the compiled plan  (LLM out of the loop
     on a guard match; deopts to arm A on mismatch)

This file computes the MODELED cost breakdown from the measured artifacts
(research/results_replay.json) and the published per-token prices, and exposes
a `--live` hook that is intentionally NOT implemented here: live actuation on
real authenticated apps needs the operator's machine, accounts, and eyes, and
is the one step reserved out of the autonomous build. Running `--live` prints
exactly what an operator must wire (a computer-use loop + real usage JSON
capture) so the modeled numbers can be replaced with billed ones.

Read-only. Modeled numbers cite Anthropic pricing (Sonnet-class $3/$15 per MTok).
"""
from __future__ import annotations

import argparse
import json
import os

REPLAY = os.path.join(os.path.dirname(__file__), "results_replay.json")
IN_PRICE = 3.0 / 1e6     # $/token, Sonnet-class input
OUT_PRICE = 15.0 / 1e6   # $/token, Sonnet-class output
OUT_FRACTION = 0.07      # ~180 out of 2509 tokens/step are output
PER_STEP = 2509          # measured re-derivation tokens/step (typical screenshot)


def dollars(tokens):
    return tokens * ((1 - OUT_FRACTION) * IN_PRICE + OUT_FRACTION * OUT_PRICE)


def model():
    data = json.load(open(REPLAY))
    plans = data["plans"]
    guard_cov = data["median_guard_coverage"]
    rows = []
    for p in plans:
        k = p["steps"]
        occ = p["occurrences"]
        num = k * PER_STEP                       # arm A per execution
        inject = p["plan_tokens"] + k * PER_STEP * 0.15  # arm B: plan + partial perception
        # arm C: local replay - on guard match LLM sends ~0; deopt fraction pays arm A
        deopt = max(0.0, 1 - guard_cov)
        replay = (1 - deopt) * (p["plan_tokens"]) + deopt * num
        rows.append({
            "routine": p["signature"][:60], "steps": k, "occurrences": occ,
            "A_rederive_tok": num, "B_inject_tok": round(inject),
            "C_replay_tok": round(replay),
            "A_over_C": round(num / max(replay, 1), 1),
            "A_over_B": round(num / max(inject, 1), 1),
        })
    # fleet totals weighted by occurrences (the routines actually repeated)
    def tot(key):
        return sum(r[key] * r["occurrences"] for r in rows)
    A, B, C = tot("A_rederive_tok"), tot("B_inject_tok"), tot("C_replay_tok")
    return {
        "arms": "A=memoryless agent, B=agent+injected plan, C=deterministic local replay",
        "pricing": "Sonnet-class $3/$15 per MTok; output fraction 0.07",
        "guard_coverage_median": guard_cov,
        "n_routines": len(rows),
        "fleet_tokens": {"A_rederive": A, "B_inject": B, "C_replay": C},
        "fleet_dollars": {"A": round(dollars(A), 2), "B": round(dollars(B), 2), "C": round(dollars(C), 2)},
        "fleet_savings_vs_A": {
            "B_inject": f"{round(100*(1-B/A),1)}%",
            "C_replay": f"{round(100*(1-C/A),1)}%",
        },
        "caveat": "MODELED from measured artifacts, not billed. Arm C credits the guard-match "
                  "fraction only; the deopt fraction pays full arm-A cost. Replace with --live "
                  "billed numbers before any launch claim of dollars saved.",
        "rows": sorted(rows, key=lambda r: -r["occurrences"])[:12],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    args = ap.parse_args()
    if args.live:
        print(json.dumps({
            "status": "reserved_for_operator",
            "why": "live actuation needs a real machine, authenticated accounts, and human "
                   "oversight; it is deliberately not run autonomously.",
            "to_wire": [
                "1. pick 6-10 replay-safe routines from results_replay.json (avoid anything "
                "irreversible: sends, posts, payments) - dry-run / sandbox targets only",
                "2. arm A: run a computer-use agent (Claude computer use) on each, capture the "
                "real Anthropic usage JSON (input/output/cache_creation/cache_read tokens)",
                "3. arm B: prepend the compiled plan (results_replay.json) to the agent context, re-run",
                "4. arm C: execute the plan with a deterministic executor honoring each step's "
                "guard; on mismatch hand back to arm A; log deopt rate",
                "5. replace model() dollars with billed dollars; report success rate + $/success + CIs",
            ],
        }, indent=2))
        return
    print(json.dumps(model(), indent=2))


if __name__ == "__main__":
    main()
