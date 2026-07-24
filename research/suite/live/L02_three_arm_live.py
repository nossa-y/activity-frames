"""L02 - three-arm cost-per-successful-task harness.

Runs each routine in the manifest under three arms and records real token usage:
  A. memoryless computer-use agent (re-derives every time)
  B. same agent + the compiled plan injected as context
  C. deterministic local replay of the compiled plan (model out of the loop on a
     guard match; deopt to arm A on mismatch)

MODES:
  (default) DRY RUN: no real actuation, no API calls. Arm C is fully executed
    against a mock screen (guards evaluated as matched), arms A/B are simulated
    with the modeled per-step token cost, so the whole pipeline and L03 scoring
    work end to end and you can see the shapes before spending anything.
  --real : YOU wire the two hooks marked `TODO(real)` to (1) your computer-use
    agent and (2) a screen/guard executor, and set ANTHROPIC_API_KEY. This is
    the step reserved for the operator; it is intentionally not automated.

Output: live/results/arm_{A,B,C}.json in the schema L03 scores.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
from _lib import SCREEN_PARAMS, CTX_IN_PER_STEP, REASON_OUT_PER_STEP  # noqa: E402

MANIFEST = os.path.join(HERE, "run_manifest.json")
OUTDIR = os.path.join(HERE, "results")
PER_STEP = SCREEN_PARAMS["typical_1512x982"] + CTX_IN_PER_STEP + REASON_OUT_PER_STEP


def _arm_A_usage(steps):
    return {"tokens_in": steps * (SCREEN_PARAMS["typical_1512x982"] + CTX_IN_PER_STEP),
            "tokens_out": steps * REASON_OUT_PER_STEP, "cache_read": 0, "cache_creation": 0}


def modeled_usage(steps, arm, guard_cov=1.0):
    """Modeled per-arm usage for DRY RUN (replaced by real usage in --real).

    Arm C honestly reflects guard coverage: on the covered fraction the model is
    out of the loop (plan tokens only); on the (1 - guard_cov) fraction the guard
    misses and it deopts to a full arm-A execution. This keeps the dry run
    consistent with the modeled bench (t08), not optimistic."""
    if arm == "A":
        return _arm_A_usage(steps)
    if arm == "B":
        return {"tokens_in": 253 + steps * int(0.15 * SCREEN_PARAMS["typical_1512x982"]),
                "tokens_out": steps * int(0.3 * REASON_OUT_PER_STEP), "cache_read": 0, "cache_creation": 253}
    # C: plan-only on the guard-covered fraction, deopt to A on the rest
    a = _arm_A_usage(steps)
    deopt = max(0.0, 1.0 - guard_cov)
    return {"tokens_in": round(253 + deopt * a["tokens_in"]),
            "tokens_out": round(deopt * a["tokens_out"]),
            "cache_read": 0, "cache_creation": 253}


# --- real hooks (operator wires these) --------------------------------------
def run_arm_A_real(routine):
    raise NotImplementedError(
        "TODO(real): drive a computer-use agent (Claude computer use) to perform this routine "
        "from scratch; return {'success':bool, 'usage':{token fields from the API response}}.")


def run_arm_B_real(routine):
    raise NotImplementedError(
        "TODO(real): same as A but prepend routine['plan'] to the system prompt; return usage.")


def run_arm_C_real(routine):
    raise NotImplementedError(
        "TODO(real): execute routine['plan'] with a parametric routine replay executor; before each action "
        "check its guard against the live screen (accessibility API / screenshot match); on "
        "mismatch, hand off to run_arm_A_real (deopt) and mark deopt=True. Return usage + success.")


def dry_run(routine, arm):
    steps = routine["steps"]
    gc = routine.get("guard_coverage", 0.43)
    usage = modeled_usage(steps, arm, guard_cov=gc)
    return {"routine_id": routine["routine_id"], "steps": steps,
            "occurrences": routine["occurrences"], "success": True,
            "deopt_fraction_modeled": round(1 - gc, 2) if arm == "C" else 0.0,
            "usage": usage, "mode": "DRY_RUN_MODELED"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="operator wires the real hooks")
    args = ap.parse_args()
    if not os.path.exists(MANIFEST):
        sys.exit("[L02] no run_manifest.json - run L01 first.")
    man = json.load(open(MANIFEST))
    routines = man["selected_routines"]
    os.makedirs(OUTDIR, exist_ok=True)

    real = {"A": run_arm_A_real, "B": run_arm_B_real, "C": run_arm_C_real}
    for arm in ("A", "B", "C"):
        results = []
        for r in routines:
            if args.real:
                res = real[arm](r)          # will raise NotImplementedError until wired
                res["routine_id"] = r["routine_id"]
            else:
                res = dry_run(r, arm)
            results.append(res)
        path = os.path.join(OUTDIR, f"arm_{arm}.json")
        json.dump({"arm": arm, "mode": "real" if args.real else "dry_run",
                   "routines": results}, open(path, "w"), indent=2, ensure_ascii=False)
        print(f"[L02] arm {arm}: {len(results)} routines -> {path}")
    print(f"[L02] {'REAL' if args.real else 'DRY RUN'} complete. Score with L03_score_billed.py")
    if not args.real:
        print("[L02] dry run uses modeled usage so L03 works end to end; wire --real for billed numbers.")


if __name__ == "__main__":
    main()
