"""T03 - desktop routine recurrence h, with a temporal holdout.

VALIDATES: the honest, non-circular recurrence rate. Routines mined on the first
~40 active days predict ~7-8% of action steps on the held-out later days
(out-of-sample), close to the in-sample rate (minimal overfit). This is the
demand-side parameter no agent-cost model measures.
"""
from _lib import Test, run_script


def main():
    t = Test("t03_recurrence_h", "out-of-sample desktop routine recurrence h")
    d = run_script("measure_corpus.py")
    hh = d["temporal_holdout_h"]
    ins = hh["h_in_sample_train"]
    oos = hh["h_out_of_sample_predicted_on_test"]
    t.check("h out-of-sample (held-out days)", oos, lo=0.02, hi=0.30)
    t.check("h in-sample (train days)", ins, lo=0.02, hi=0.30)
    t.check("overfit gap small (in - out < 0.05)", (ins - oos) < 0.05, expect=True)
    t.check("train routine signatures", hh["n_train_routine_signatures"], lo=100)
    return t.finish({"temporal_holdout": hh})


if __name__ == "__main__":
    main()
