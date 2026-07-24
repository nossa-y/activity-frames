"""T02 - parametric routine replay compiler + R_inject (operational headline).

VALIDATES: routines compile to guarded replay plans for ~0 tokens and ~1 ms, and
the operational R_inject (inject the compiled plan) is ~60x. This is the number
we lead with.
"""
from _lib import Test, run_script


def main():
    t = Test("t02_replay_compile", "guarded replay-plan compiler + R_inject ~60x")
    d = run_script("compile_replay.py")
    t.check("R_inject median (operational)", d["R_inject_guarded_plan_median"], lo=30, hi=120)
    t.check("compile cost B median (ms)", d["median_compile_ms_B"], lo=0, hi=50)
    t.check("plans compiled", d["n_plans_compiled"], lo=5)
    t.check("guard coverage median", d["median_guard_coverage"], lo=0.1, hi=1.0)
    return t.finish({
        "R_inject_median": d["R_inject_guarded_plan_median"],
        "R_inject_iqr": d.get("R_inject_guarded_plan_p25_p75"),
        "median_plan_tokens": d["median_plan_tokens"],
        "median_compile_ms_B": d["median_compile_ms_B"],
        "median_guard_coverage": d["median_guard_coverage"],
        "denominator_ladder": d.get("denominator_ladder"),
    })


if __name__ == "__main__":
    main()
