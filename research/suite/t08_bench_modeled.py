"""T08 - three-arm bench (MODELED) on the recurring routines.

VALIDATES the end-to-end token/dollar story from measured artifacts (not billed):
arm A memoryless agent, arm B agent + injected compiled plan, arm C deterministic
local replay. Reports the modeled savings and the honest reason C < B today
(guard coverage 0.43 -> the deopt fraction pays full price). The billed version
is the reserved live step (see live/).
"""
from _lib import Test, run_script


def main():
    t = Test("t08_bench_modeled", "modeled three-arm token/dollar saving on repeated routines")
    d = run_script("bench_three_arm.py")
    sav = d["fleet_savings_vs_A"]
    b = float(sav["B_inject"].rstrip("%"))
    c = float(sav["C_replay"].rstrip("%"))
    t.check("inject (arm B) saving % vs memoryless", b, lo=50, hi=99)
    t.check("local replay (arm C) saving % vs memoryless", c, lo=10, hi=99)
    t.check("guard coverage (why C < B today)", d["guard_coverage_median"], lo=0.1, hi=1.0)
    return t.finish({"fleet_dollars": d["fleet_dollars"], "fleet_savings_vs_A": sav,
                     "guard_coverage_median": d["guard_coverage_median"],
                     "caveat": d.get("caveat")})


if __name__ == "__main__":
    main()
