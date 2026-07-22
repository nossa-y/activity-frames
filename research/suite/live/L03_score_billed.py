"""L03 - score the three-arm bench into billed dollars, R, and CIs.

Reads live/results/arm_{A,B,C}.json (from L02, dry-run or real) and computes,
per arm: total tokens, billed dollars at current pricing, success rate; then the
headline comparisons R_billed = A/C and A/B tokens, dollar savings, and a
bootstrap confidence interval on the saving. Works identically on dry-run
(modeled) and real usage - only the input numbers change.

Set prices with --in-price / --out-price (defaults: Sonnet-class $3 / $15 per MTok;
cache read 0.1x input, cache write 1.25x input).
"""
import argparse
import json
import os
import random

HERE = os.path.dirname(__file__)
OUTDIR = os.path.join(HERE, "results")


def dollars(u, pin, pout):
    return (u.get("tokens_in", 0) * pin
            + u.get("tokens_out", 0) * pout
            + u.get("cache_read", 0) * pin * 0.1
            + u.get("cache_creation", 0) * pin * 1.25)


def load(arm):
    p = os.path.join(OUTDIR, f"arm_{arm}.json")
    if not os.path.exists(p):
        raise SystemExit(f"[L03] missing {p} - run L02 first.")
    return json.load(open(p))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-price", type=float, default=3.0 / 1e6)
    ap.add_argument("--out-price", type=float, default=15.0 / 1e6)
    args = ap.parse_args()
    arms = {a: load(a) for a in ("A", "B", "C")}
    mode = arms["A"]["mode"]

    per_arm = {}
    for a, d in arms.items():
        toks = sum(sum(v for k, v in r["usage"].items()) for r in d["routines"])
        usd = sum(dollars(r["usage"], args.in_price, args.out_price) for r in d["routines"])
        succ = sum(1 for r in d["routines"] if r.get("success")) / max(len(d["routines"]), 1)
        # weight by occurrences: real fleet cost = per-execution cost x how often it recurs
        fleet = sum(dollars(r["usage"], args.in_price, args.out_price) * r.get("occurrences", 1)
                    for r in d["routines"])
        per_arm[a] = {"tokens": toks, "dollars_per_run_set": round(usd, 4),
                      "fleet_dollars_occ_weighted": round(fleet, 2), "success_rate": round(succ, 3)}

    A = per_arm["A"]["fleet_dollars_occ_weighted"]
    out = {
        "mode": mode, "per_arm": per_arm,
        "R_billed_A_over_C_tokens": round(per_arm["A"]["tokens"] / max(per_arm["C"]["tokens"], 1), 1),
        "R_billed_A_over_B_tokens": round(per_arm["A"]["tokens"] / max(per_arm["B"]["tokens"], 1), 1),
        "fleet_saving_inject_B": f"{round(100*(1 - per_arm['B']['fleet_dollars_occ_weighted']/max(A,1e-9)),1)}%",
        "fleet_saving_replay_C": f"{round(100*(1 - per_arm['C']['fleet_dollars_occ_weighted']/max(A,1e-9)),1)}%",
    }

    # bootstrap CI on the per-routine A-vs-C dollar saving fraction
    rows = list(zip(arms["A"]["routines"], arms["C"]["routines"]))
    if rows:
        rng = random.Random(7)
        fracs = []
        for _ in range(2000):
            samp = [rows[rng.randrange(len(rows))] for _ in rows]
            a = sum(dollars(x[0]["usage"], args.in_price, args.out_price) for x in samp)
            c = sum(dollars(x[1]["usage"], args.in_price, args.out_price) for x in samp)
            fracs.append(1 - c / a if a else 0)
        fracs.sort()
        out["A_vs_C_saving_ci95"] = [round(100 * fracs[50], 1), round(100 * fracs[1949], 1)]

    if mode != "real":
        out["WARNING"] = ("these are DRY-RUN modeled numbers, not billed. Re-run L02 --real with "
                          "your wired hooks and real Anthropic usage JSON for the launch headline.")
    print(json.dumps(out, indent=2))
    json.dump(out, open(os.path.join(OUTDIR, "L03_scored.json"), "w"), indent=2)


if __name__ == "__main__":
    main()
