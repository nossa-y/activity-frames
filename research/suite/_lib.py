"""Shared helpers for the exhaustive validation suite.

Every test imports from here so the mining/tokenizer logic is identical to the
committed instruments (research/measure_overhead.py). Read-only over a COPY of
a screenpipe capture DB; point AFRAMES_CORPUS at it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime

# reuse the validated instrument logic
_HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(_HERE, ".."))
from measure_overhead import (  # noqa: E402
    _canon_click, is_specific, toks, TOKENIZER,
    MIN_FREQUENCY, MIN_STEPS, MAX_STEPS, SESSION_GAP_S, SCREEN_PARAMS,
    CTX_IN_PER_STEP, REASON_OUT_PER_STEP,
)

DB = os.environ.get("AFRAMES_CORPUS", "/tmp/corpus_ro.sqlite")
RESULTS_DIR = os.path.join(_HERE, "results")
PER_STEP_TYPICAL = SCREEN_PARAMS["typical_1512x982"] + CTX_IN_PER_STEP + REASON_OUT_PER_STEP


def conn():
    if not os.path.exists(DB):
        sys.exit(f"[FATAL] capture DB not found at {DB}. Set AFRAMES_CORPUS to a COPY "
                 f"of your screenpipe db.sqlite (never the live file).")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def ts(s):
    s = (s or "").replace("Z", "").replace("T", " ")[:19]
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def action_sessions(c, with_rows=False, day_lo=None, day_hi=None):
    """Gap-bounded sessions of canonical click/text steps. If with_rows, each
    step is (canon, row); else just canon. Optional inclusive day filter."""
    where = "event_type IN ('click','text')"
    params = ()
    if day_lo and day_hi:
        where += " AND date(timestamp) BETWEEN ? AND ?"
        params = (day_lo, day_hi)
    rows = c.execute(
        f"""SELECT timestamp, event_type, element_name, element_role, app_name, browser_url
            FROM ui_events WHERE {where} ORDER BY timestamp ASC""", params).fetchall()
    sessions, cur, prev = [], [], None
    for r in rows:
        t = ts(r["timestamp"])
        if t is None:
            continue
        if prev is not None and (t - prev).total_seconds() > SESSION_GAP_S:
            if cur:
                sessions.append(cur)
            cur = []
        step = ("type:" + (r["element_name"] or "field")[:24] if r["event_type"] == "text"
                else _canon_click(r["element_name"], r["element_role"]))
        if step:
            last = cur[-1][0] if (with_rows and cur) else (cur[-1] if cur else None)
            if step != last:
                cur.append((step, r) if with_rows else step)
        prev = t
    if cur:
        sessions.append(cur)
    return sessions


def frequent_ngrams(sessions, specific_only=True, min_freq=MIN_FREQUENCY):
    """Maximal frequent action n-grams. sessions = list of [step,...]."""
    from collections import defaultdict
    counts = defaultdict(int)
    for sess in sessions:
        L = len(sess)
        for n in range(MIN_STEPS, min(MAX_STEPS, L) + 1):
            for i in range(L - n + 1):
                counts[tuple(sess[i:i + n])] += 1
    freq = {g: cnt for g, cnt in counts.items() if cnt >= min_freq}
    if specific_only:
        freq = {g: cnt for g, cnt in freq.items() if sum(is_specific(s) for s in g) >= 2}
    by_len = sorted(freq, key=lambda g: -len(g))
    kept = {}
    keptlist = []
    for g in by_len:
        gs = " -> ".join(g)
        if any(gs in " -> ".join(k) and freq[g] == freq[k] for k in keptlist):
            continue
        keptlist.append(g)
        kept[g] = freq[g]
    return kept, freq


def coverage(sessions, sigset):
    """UNION fraction of steps inside any n-gram in sigset."""
    tot = cov = 0
    for sess in sessions:
        L = len(sess)
        tot += L
        mark = [False] * L
        for i in range(L):
            for n in range(MIN_STEPS, min(MAX_STEPS, L - i) + 1):
                if tuple(sess[i:i + n]) in sigset:
                    for j in range(i, i + n):
                        mark[j] = True
        cov += sum(mark)
    return cov, tot


def median(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else None


def pctile(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, max(0, int(round(p / 100 * (len(xs) - 1)))))]


def run_script(rel_path, args=()):
    """Run a research script (which prints JSON to stdout) and return the parsed
    JSON. Passes AFRAMES_CORPUS/AFRAMES_DB through so it reads the same DB."""
    import subprocess
    path = os.path.join(_HERE, "..", rel_path)
    env = dict(os.environ, AFRAMES_CORPUS=DB, AFRAMES_DB=DB)
    out = subprocess.run([sys.executable, path, *args], capture_output=True,
                         text=True, env=env, timeout=1800)
    if out.returncode != 0:
        raise RuntimeError(f"{rel_path} failed: {out.stderr[-500:]}")
    return json.loads(out.stdout)


class Test:
    """Standard test wrapper: writes results/<name>.json and prints PASS/FAIL
    against an expected reference band."""
    def __init__(self, name, validates):
        self.name = name
        self.validates = validates
        self.t0 = time.perf_counter()
        self.checks = []

    def check(self, label, value, lo=None, hi=None, expect=None):
        ok = True
        if lo is not None and (value is None or value < lo):
            ok = False
        if hi is not None and (value is None or value > hi):
            ok = False
        if expect is not None:
            ok = (value == expect)
        self.checks.append({"label": label, "value": value, "lo": lo, "hi": hi,
                            "expect": expect, "pass": ok})
        band = (f" [expect {expect}]" if expect is not None
                else f" [{lo}..{hi}]" if (lo is not None or hi is not None) else "")
        print(f"    {'PASS' if ok else 'FAIL'}  {label} = {value}{band}")
        return ok

    def finish(self, payload):
        dt = round(time.perf_counter() - self.t0, 1)
        allpass = all(ch["pass"] for ch in self.checks)
        out = {"test": self.name, "validates": self.validates, "seconds": dt,
               "verdict": "PASS" if allpass else "FAIL", "checks": self.checks,
               "tokenizer": TOKENIZER, "db": DB, **payload}
        os.makedirs(RESULTS_DIR, exist_ok=True)
        with open(os.path.join(RESULTS_DIR, self.name + ".json"), "w") as f:
            json.dump(out, f, indent=2)
        print(f"  {out['verdict']}  ({dt}s)  -> results/{self.name}.json")
        return out
