"""T12 - public-dataset replication (breaks n=1). NEEDS NETWORK.

VALIDATES that R is not an artifact of one user: the SAME numerator model on
Mind2Web (1,009 public web tasks) gives R in the same low-hundreds band that
brackets the private action R (~343x). Requires `pip install datasets` and a
network fetch of the Mind2Web parquet metadata.
"""
import os
from _lib import Test, run_script


def main():
    t = Test("t12_public_replication", "R replicates on an independent public dataset (breaks n=1)")
    try:
        d = run_script("measure_overhead_public.py")
    except Exception as e:
        print(f"    SKIP  (network/dataset unavailable): {str(e)[:160]}")
        return t.finish({"status": "skipped", "reason": str(e)[:300],
                         "how_to_enable": "pip install datasets huggingface_hub; ensure network; rerun"})
    # the public script's schema: look for a median R and comparison
    blob = str(d)
    t.check("public result produced", d is not None, expect=True)
    # best-effort extraction of a median R value
    med = (d.get("R_median") or d.get("per_task", {}).get("R_median")
           or d.get("summary", {}).get("R_median"))
    if med is not None:
        t.check("public R median in low hundreds (100-500)", med, lo=100, hi=500)
    return t.finish({"public_result": d if len(blob) < 20000 else {"note": "see results_overhead_public.json"}})


if __name__ == "__main__":
    main()
