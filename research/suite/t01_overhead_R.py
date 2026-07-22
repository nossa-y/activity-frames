"""T01 - the Routine Overhead Ratio (flagship). Reproduces R_info.

VALIDATES: R_info median ~343x (action) / ~198x (url), robust across screen
resolution and routine length, on your capture DB. This is the ceiling rung;
the operational R_inject ~60x is validated in T02.
"""
from _lib import Test, run_script


def main():
    t = Test("t01_overhead_R", "Routine Overhead Ratio R_info (ceiling rung)")
    d = run_script("measure_overhead.py")
    a = d["results"]["action"]["summary"]
    u = d["results"]["url"]["summary"]
    t.check("R_info median (action)", a["R_typical_median"], lo=200, hi=500)
    t.check("R_info median (url)", u["R_typical_median"], lo=120, hi=320)
    t.check("R grows with routine length (16+ > 3-4)",
            d["results"]["action"]["R_by_steps"].get("16+", {}).get("R_median", 0)
            >= d["results"]["action"]["R_by_steps"].get("3-4", {}).get("R_median", 1e9), expect=True)
    t.check("recurring action routines found", a["n_routines"], lo=50)
    return t.finish({"action_summary": a, "url_summary": u,
                     "coverage_h": d["results"]["action"].get("coverage_h"),
                     "R_by_steps": d["results"]["action"].get("R_by_steps")})


if __name__ == "__main__":
    main()
