"""T05 - entropy rate and Fano predictability ceiling of desktop activity.

VALIDATES: the compressibility / predictability of the user's work, reported
HONESTLY as a resolution-indexed surface (never one "desktop ceiling" number).
This is the survivor of the killed "compressibility ceiling" candidate: an
instrument reading, not a law.

METHOD:
  * Entropy rate S via the Lempel-Ziv / Kontoyiannis estimator (the Song-Barabasi
    method): S = (N log2 N) / sum(Lambda_i), Lambda_i = 1 + longest match at i
    seen earlier. Computed on event-clock sequences (self-transitions collapsed).
  * Fano upper bound on predictability Pi_max: solve
    S = H(Pi) + (1-Pi) log2(m-1) for Pi, m = alphabet size.
  * CONTROLS (per arXiv:2607.03443): report alphabet size m and length N next to
    every number, and a subsampled convergence curve (S at growing prefix lengths)
    so the reader can see the estimate stabilize rather than trust one value.
  * Three granularities: app, site (url-template), action.

HONEST FRAMING: Pi_max is an UPPER bound on next-symbol predictability and is
dialable by granularity and clock - so we report the surface. It is NOT an
achievable automation rate and NOT compared to Song 2010's 93% mobility figure
as a prior.

EXPECTED: app-level Pi_max high (small alphabet, stationary); action-level lower;
all with visible convergence. No PASS/FAIL threshold - this is a measurement.
"""
import math
from urllib.parse import urlsplit
from _lib import Test, conn, action_sessions

CAP = 60          # max match length considered (matches beyond this are rare)
MAX_N = 12000     # cap sequence length for the O(N log N * find) estimator


def encode(seq):
    syms, out = {}, []
    for s in seq:
        if s not in syms:
            cp = 0x4E00 + len(syms)
            if 0xD800 <= cp <= 0xDFFF:
                cp += 0x800
            syms[s] = chr(cp)
        out.append(syms[s])
    return "".join(out), len(syms)


def entropy_rate_lz(seq):
    s, m = encode(seq)
    N = len(s)
    if N < 50:
        return None, m, N
    total = 0
    for i in range(N):
        lo, hi, best = 1, min(CAP, N - i), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            # a prior occurrence must fit in s[:i+mid-1], so it starts < i
            if s.find(s[i:i + mid], 0, i + mid - 1) != -1:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        total += best + 1
    S = (N * math.log2(N)) / total
    return S, m, N


def fano_pi_max(S, m):
    if m <= 1 or S is None:
        return None
    def H(p):
        if p <= 0 or p >= 1:
            return 0.0
        return -p * math.log2(p) - (1 - p) * math.log2(1 - p)
    def F(p):
        return H(p) + (1 - p) * math.log2(m - 1) if m > 1 else H(p)
    # F is decreasing on [1/m, 1]; F(1)=0, F(1/m)=log2(m). Bisect for F(p)=S.
    if S >= math.log2(m):
        return 1.0 / m
    lo, hi = 1.0 / m, 1.0 - 1e-9
    for _ in range(60):
        mid = (lo + hi) / 2
        if F(mid) > S:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def collapse(seq):
    out = []
    for x in seq:
        if not out or out[-1] != x:
            out.append(x)
    return out


def app_sequence(c):
    rows = c.execute(
        "SELECT app_name FROM frames WHERE focused=1 AND app_name IS NOT NULL "
        "AND app_name!='' ORDER BY timestamp ASC").fetchall()
    return collapse([r["app_name"] for r in rows])


def site_sequence(c):
    rows = c.execute(
        "SELECT browser_url FROM frames WHERE focused=1 AND browser_url IS NOT NULL "
        "AND browser_url!='' ORDER BY timestamp ASC").fetchall()
    def tmpl(u):
        try:
            p = urlsplit(u)
        except ValueError:
            return ""
        parts = [x for x in p.path.split("/") if x]
        h = p.hostname or ""
        return f"{h}/{parts[0]}/*" if parts else h
    return collapse([tmpl(r["browser_url"]) for r in rows if r["browser_url"]])


def measure_seq(seq):
    seq = seq[:MAX_N]
    curve = []
    for L in (1000, 2000, 4000, 8000, len(seq)):
        if L <= len(seq) and L >= 200:
            S, m, N = entropy_rate_lz(seq[:L])
            curve.append({"prefix_len": N, "alphabet": m, "entropy_rate_bits": round(S, 3) if S else None,
                          "pi_max": round(fano_pi_max(S, m), 3) if S else None})
    S, m, N = entropy_rate_lz(seq)
    return {
        "length": N, "alphabet": m,
        "entropy_rate_bits_per_symbol": round(S, 3) if S else None,
        "pi_max_fano_upper_bound": round(fano_pi_max(S, m), 3) if S else None,
        "convergence_curve": curve,
    }


def main():
    t = Test("t05_entropy_predictability",
             "desktop predictability as a resolution-indexed surface (instrument reading)")
    c = conn()
    app = app_sequence(c)
    site = site_sequence(c)
    action = [s for sess in action_sessions(c) for s in sess]
    action = collapse(action)
    c.close()

    res = {"app": measure_seq(app), "site": measure_seq(site), "action": measure_seq(action)}
    for g in ("app", "site", "action"):
        r = res[g]
        t.check(f"{g}: entropy_rate_bits", r["entropy_rate_bits_per_symbol"], lo=0.0)
        t.check(f"{g}: pi_max (upper bound)", r["pi_max_fano_upper_bound"], lo=0.0, hi=1.0)
        print(f"      {g}: N={r['length']} alphabet={r['alphabet']} "
              f"S={r['entropy_rate_bits_per_symbol']} bits  Pi_max<={r['pi_max_fano_upper_bound']}")
    return t.finish({
        "granularities": res,
        "note": "Pi_max is an UPPER bound on next-symbol predictability, dialable by "
                "granularity/clock; report the surface, not one number. Not an achievable "
                "automation rate. Song 2010 (93% mobility) cited only as an analogue, not a prior.",
        "controls": "alphabet size and length reported per estimate; convergence curve shows "
                    "the estimator stabilizing (per arXiv:2607.03443 length/alphabet confounds).",
    })


if __name__ == "__main__":
    main()
