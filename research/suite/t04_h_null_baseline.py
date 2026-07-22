"""T04 - recurrence h against a frequency-preserving null.

VALIDATES: that the measured routine-recurrence h reflects real SEQUENTIAL
structure, not just the marginal frequency skew of a few common actions (the
"coarse h is just Zipfian marginals" objection from the kill-test).

METHOD: measure observed h (coverage by recurring specific action n-grams).
Then build N surrogate streams that SHUFFLE the step order WITHIN each session -
this preserves every session's multiset of actions (so all marginal frequencies
are intact) but destroys sequential order. Recompute h on each surrogate. If the
observed h sits far above the surrogate distribution, the recurrence is genuine
repeated PROCEDURE, not an artifact of common symbols.

REPORTS: h_observed, h_null mean/std over surrogates, h_excess, z-score, and the
percentile of h_observed in the null distribution.

EXPECTED (reference, this corpus): h_observed ~0.09, h_null << h_observed,
h_excess strongly positive, z > 3. A PASS means the recurrence is real.
"""
import random
from _lib import Test, conn, action_sessions, frequent_ngrams, coverage

N_SURROGATES = 20
SEED = 1729


def main():
    t = Test("t04_h_null_baseline", "h reflects real sequential structure, not marginal skew")
    c = conn()
    sessions = action_sessions(c)
    c.close()

    sigset, _ = frequent_ngrams(sessions, specific_only=True)
    cov, tot = coverage(sessions, set(sigset))
    h_obs = cov / max(tot, 1)

    rng = random.Random(SEED)
    nulls = []
    for _ in range(N_SURROGATES):
        surro = []
        for sess in sessions:
            s = list(sess)
            rng.shuffle(s)
            surro.append(s)
        ss, _ = frequent_ngrams(surro, specific_only=True)
        cn, tn = coverage(surro, set(ss))
        nulls.append(cn / max(tn, 1))

    mean = sum(nulls) / len(nulls)
    var = sum((x - mean) ** 2 for x in nulls) / len(nulls)
    std = var ** 0.5
    z = (h_obs - mean) / std if std > 0 else float("inf")
    pct_above = 100.0 * sum(1 for x in nulls if x >= h_obs) / len(nulls)

    t.check("h_observed", round(h_obs, 4), lo=0.02)
    t.check("h_excess (obs - null_mean)", round(h_obs - mean, 4), lo=0.005)
    t.check("z_score", round(z, 2) if z != float("inf") else 999, lo=2.0)
    t.check("surrogates with h >= observed (%)", round(pct_above, 1), hi=10.0)
    return t.finish({
        "h_observed": round(h_obs, 4),
        "h_null_mean": round(mean, 4), "h_null_std": round(std, 4),
        "h_null_min": round(min(nulls), 4), "h_null_max": round(max(nulls), 4),
        "h_excess": round(h_obs - mean, 4), "z_score": round(z, 2) if z != float("inf") else None,
        "n_surrogates": N_SURROGATES, "seed": SEED,
        "interpretation": "h_observed far above the within-session-shuffled null means the "
                          "recurring routines are real repeated procedures, not common-symbol artifacts.",
    })


if __name__ == "__main__":
    main()
