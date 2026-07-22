"""T10 - determinism stress test of the instruments themselves.

VALIDATES: not just that the compiler is byte-reproducible (that is T06/certify_ivm),
but that the ROUTINE MINING and R PRICING are deterministic - same DB, same
routines, same R, run to run. This is what lets the readings be cited as facts.

METHOD: run the full mine + price pipeline twice; hash the sorted (signature, R)
list each time; assert the hashes are identical. Also assert the replay-script
token counts are identical run to run.

EXPECTED: identical hashes. PASS = the instrument is deterministic.
"""
import hashlib
import json
from _lib import Test, conn, action_sessions, frequent_ngrams, toks, SCREEN_PARAMS


def fingerprint():
    c = conn()
    sessions = action_sessions(c)
    routines, _ = frequent_ngrams(sessions, specific_only=True)
    c.close()
    per_step = SCREEN_PARAMS["typical_1512x982"] + 350 + 180
    rows = []
    for sig in sorted(routines, key=lambda g: (len(g), " -> ".join(g))):
        script = "\n".join(
            (f"type {a[5:]} <value>" if a.startswith("type:")
             else f"click {a}" if (a.startswith("[") or "/" not in a)
             else f"navigate {a}") for a in sig)
        den = toks(script)
        r = round(len(sig) * per_step / max(den, 1), 3)
        rows.append({"sig": " -> ".join(sig), "occ": routines[sig], "den": den, "R": r})
    blob = json.dumps(rows, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest(), len(rows)


def main():
    t = Test("t10_determinism_stress", "routine mining + R pricing are deterministic run-to-run")
    h1, n1 = fingerprint()
    h2, n2 = fingerprint()
    t.check("run1 sha256 == run2 sha256", h1 == h2, expect=True)
    t.check("routine count stable", n1 == n2, expect=True)
    return t.finish({
        "sha256_run1": h1, "sha256_run2": h2, "n_routines": n1,
        "identical": h1 == h2,
        "interpretation": "identical fingerprints mean the mining and pricing carry no hidden "
                          "nondeterminism (dict/set order, floating point, time); the readings reproduce.",
    })


if __name__ == "__main__":
    main()
