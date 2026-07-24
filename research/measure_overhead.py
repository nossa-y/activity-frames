"""Routine Overhead Ratio (R) - the flagship instrument.

R = (tokens a screenshot-driven computer-use agent spends RE-DERIVING a
recurring routine, with no memory) / (tokens of the parametric routine replay
script that activity-frames compiles that routine into).

Both ends are operationally defined:

  * DENOMINATOR (measured): the replay script is the ordered list of
    resolved atomic actions of the routine (click <target>, type <field>,
    navigate <url-template>), serialized compactly and tokenized with the
    same tokenizer the paper uses (tiktoken cl100k_base). This is the
    routine's information content - what a replay would SEND instead of
    re-deriving. Nothing modeled here; it is bytes of a real captured
    routine.

  * NUMERATOR (modeled, and labeled as such): a k-step routine forces a
    memoryless agent through k perception->reason->act cycles. Each cycle
    pays one screenshot (Anthropic's published image tokenization,
    tokens = width*height/750), a fixed tool/context overhead, and
    reasoning output. k is MEASURED from the real routine; only the
    per-step token costs are cited parameters, and we sweep them.

We report R as a DISTRIBUTION across all recurring routines, at two
granularities, with a parameter sensitivity band - never a single number.
We never stack R with the 86x compression or any cache discount; they
concern different token populations (see paper Sec. Limits).

Read-only. Point AFRAMES_CORPUS at a COPY of a capture DB.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DB = os.environ.get("AFRAMES_CORPUS", "/tmp/corpus_ro.sqlite")
MIN_FREQUENCY = 3          # a routine must recur >=3x (matches patterns.py)
SESSION_GAP_S = 90         # >90s idle between actions => routine boundary
MIN_STEPS = 3              # a routine is >=3 atomic actions
MAX_STEPS = 60             # cap runaway "sessions"; longer => split, reported

# ---- tokenizer (same as paper measure_v2.py) --------------------------------
try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def toks(s: str) -> int: return len(_ENC.encode(s))
    TOKENIZER = "cl100k_base"
except Exception:                                        # pragma: no cover
    def toks(s: str) -> int: return max(1, len(s) // 4)
    TOKENIZER = "chars/4-fallback"

# ---- re-derivation cost model (cited parameters, swept) ---------------------
# Anthropic image tokens = (w*h)/750  [docs.claude.com vision]. Common macOS
# logical screenshot sizes; we sweep to show R's floor is resolution-robust.
SCREEN_PARAMS = {
    "conservative_1280x800":  1280 * 800 // 750,   # 1365 tok/screenshot
    "typical_1512x982":       1512 * 982 // 750,   # 1979 tok/screenshot
    "retina_1728x1117":       1728 * 1117 // 750,  # 2573 tok/screenshot
}
# per-step non-image overhead. Conservative = tool defs cached, minimal history.
CTX_IN_PER_STEP = 350      # tool schema + instruction + last-action text (cached-friendly)
REASON_OUT_PER_STEP = 180  # output tokens/step, low end of computer-use traces
# output is billed ~5x input (Sonnet $3/$15); we report TOKENS (input-equiv) and
# also a $-band. To keep R a pure token ratio we count raw tokens; the $ model
# lives in the economics section.


@dataclass
class Routine:
    sig: str                 # canonical action signature
    granularity: str
    steps: int
    occurrences: int
    example_actions: list = field(default_factory=list)
    replay_script: str = ""


def _conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _url_template(url: str) -> str:
    try:
        u = urlsplit(url)
    except ValueError:
        return ""
    host = u.hostname or ""
    parts = [p for p in u.path.split("/") if p]
    if len(parts) >= 2:
        return f"{host}/{parts[0]}/{parts[1]}/*"
    if len(parts) == 1:
        return f"{host}/{parts[0]}/*"
    return host


def _canon_click(name, role):
    name = (name or "").strip()
    generic = {"scroll area", "group", "cell", "text", "text field", ""}
    return f"[{(role or 'el').strip()}]" if name.lower() in generic else name[:40]


def is_specific(step: str) -> bool:
    """A step names a concrete UI target (button label, specific field, or
    URL template) rather than a generic container / anonymous typed field.
    Routines must contain real targets to count as delegable - this filters
    generic 'type into a field, tab to a group' input micro-structure that
    otherwise dominates any recurring-n-gram count (the granularity trap)."""
    if step.startswith("["):            # [AXGroup], [el] - generic container
        return False
    if step == "type:field":            # typed into an unnamed field
        return False
    return True


def mine_routines(conn, granularity="action"):
    """Segment ui_events into gap-bounded sessions; canonicalize each into an
    action signature; return signatures that recur >= MIN_FREQUENCY.

    granularity:
      'action' - sequence of clicked-element signatures + typed-field markers
      'url'    - sequence of url-templates visited (browser routines)
    """
    if granularity == "action":
        rows = conn.execute(
            """
            SELECT timestamp, event_type, element_name, element_role, app_name, browser_url
            FROM ui_events
            WHERE event_type IN ('click','text')
              AND (element_name IS NOT NULL OR event_type='text')
            ORDER BY timestamp ASC
            """
        ).fetchall()
        step_of = lambda r: (
            "type:" + (r["element_name"] or "field")[:24]
            if r["event_type"] == "text"
            else _canon_click(r["element_name"], r["element_role"])
        )
    else:  # url
        rows = conn.execute(
            """
            SELECT timestamp, browser_url, app_name
            FROM frames
            WHERE focused=1 AND browser_url IS NOT NULL AND browser_url!=''
            ORDER BY timestamp ASC
            """
        ).fetchall()
        step_of = lambda r: _url_template(r["browser_url"])

    # gap-segment into sessions
    from datetime import datetime
    def ts(s):
        s = (s or "").replace("Z", "").replace("T", " ")[:19]
        try: return datetime.fromisoformat(s)
        except Exception: return None

    sessions, cur, prev = [], [], None
    for r in rows:
        t = ts(r["timestamp"])
        if t is None:
            continue
        if prev is not None and (t - prev).total_seconds() > SESSION_GAP_S:
            if cur: sessions.append(cur)
            cur = []
        step = step_of(r)
        if step:
            # collapse immediate self-repeats (scroll/typing bursts)
            if not cur or cur[-1] != step:
                cur.append(step)
        prev = t
    if cur:
        sessions.append(cur)

    # Frequent-episode mining (Mannila WINEPI-style): count all contiguous
    # n-grams for n in [MIN_STEPS..MAX_STEPS] across sessions; keep freq>=3.
    # Then keep MAXIMAL routines: drop an n-gram if it is a subsequence of a
    # longer kept n-gram with the same occurrence count (avoids double-count).
    from collections import defaultdict
    counts = defaultdict(int)
    total_steps = 0
    for sess in sessions:
        total_steps += len(sess)
        L = len(sess)
        for n in range(MIN_STEPS, min(MAX_STEPS, L) + 1):
            for i in range(L - n + 1):
                counts[tuple(sess[i:i + n])] += 1
    frequent = {g: c for g, c in counts.items() if c >= MIN_FREQUENCY}
    # A DELEGABLE routine must carry >=2 specific targets (pre-declared rule).
    def specific_enough(g):
        return sum(is_specific(s) for s in g) >= 2
    frequent_specific = {g: c for g, c in frequent.items() if specific_enough(g)}

    # maximal filter over the specific set
    by_len = sorted(frequent_specific, key=lambda g: -len(g))
    kept = []
    for g in by_len:
        gs = " -> ".join(g)
        if any(gs in " -> ".join(k) and frequent_specific[g] == frequent_specific[k]
               for k in kept):
            continue
        kept.append(g)

    routines = [Routine(sig=" -> ".join(g), granularity=granularity, steps=len(g),
                        occurrences=frequent_specific[g], example_actions=list(g))
                for g in kept]

    # Coverage = fraction of ALL action steps inside >=1 recurring routine
    # (UNION of covered positions, counted once). Report TWO levels:
    #   h_raw      - any recurring n-gram (incl. generic input micro-structure)
    #   h_specific - only routines with >=2 named targets (delegable tasks)
    # The gap between them is generic typing/navigation noise, made explicit.
    def coverage(freqset):
        cov = 0
        for sess in sessions:
            L = len(sess); mark = [False] * L
            for i in range(L):
                for n in range(MIN_STEPS, min(MAX_STEPS, L - i) + 1):
                    if tuple(sess[i:i + n]) in freqset:
                        for j in range(i, i + n):
                            mark[j] = True
            cov += sum(mark)
        return cov
    cov_raw = coverage(frequent)
    cov_spec = coverage(frequent_specific)
    mine_routines.last_coverage = {
        "total_action_steps": total_steps,
        "n_sessions": len(sessions),
        "h_raw_any_recurring": round(cov_raw / max(total_steps, 1), 4),
        "h_specific_delegable": round(cov_spec / max(total_steps, 1), 4),
        "steps_inside_specific_routines": cov_spec,
        "n_frequent_ngrams": len(frequent),
        "n_specific_ngrams": len(frequent_specific),
    }
    return routines


def replay_script(routine: Routine, conn) -> str:
    """Parametric routine replay artifact: the minimal instruction a replay engine
    needs. This is the DENOMINATOR - the routine's compiled information
    content. One line per resolved action."""
    lines = [f"# routine ({routine.granularity}), {routine.occurrences}x, {routine.steps} steps"]
    for a in routine.example_actions:
        if a.startswith("type:"):
            lines.append(f'type {a[5:]} <value>')
        elif a.startswith("[") or "/" not in a:
            lines.append(f'click {a}')
        else:
            lines.append(f'navigate {a}')
    return "\n".join(lines)


def rederivation_tokens(routine: Routine, screenshot_tok: int) -> int:
    """NUMERATOR (modeled). k perception-reason-act cycles."""
    k = routine.steps
    return k * (screenshot_tok + CTX_IN_PER_STEP + REASON_OUT_PER_STEP)


def measure(granularity="action"):
    conn = _conn()
    routines = mine_routines(conn, granularity)
    recs = []
    for rt in routines:
        rt.replay_script = replay_script(rt, conn)
        den = toks(rt.replay_script)
        row = {
            "granularity": granularity,
            "steps": rt.steps,
            "occurrences": rt.occurrences,
            "replay_tokens_den": den,
            "R_by_screen": {},
        }
        for name, stok in SCREEN_PARAMS.items():
            num = rederivation_tokens(rt, stok)
            row["R_by_screen"][name] = round(num / max(den, 1), 1)
        row["R_typical"] = row["R_by_screen"]["typical_1512x982"]
        row["sig_preview"] = rt.sig[:120]
        recs.append(row)
    conn.close()
    return recs


def summarize(recs, granularity):
    if not recs:
        return {"granularity": granularity, "n_routines": 0}
    Rs = sorted(r["R_typical"] for r in recs)
    dens = [r["replay_tokens_den"] for r in recs]
    steps = [r["steps"] for r in recs]
    occ = [r["occurrences"] for r in recs]
    def pct(x, p):
        i = min(len(x) - 1, max(0, int(round((p / 100) * (len(x) - 1)))))
        return sorted(x)[i]
    # weighted-by-recurrence R (a routine that repeats 50x matters more)
    tot_occ = sum(occ)
    Rw = sum(r["R_typical"] * r["occurrences"] for r in recs) / max(tot_occ, 1)
    return {
        "granularity": granularity,
        "tokenizer": TOKENIZER,
        "n_routines": len(recs),
        "total_routine_instances": tot_occ,
        "R_typical_median": statistics.median(Rs),
        "R_typical_p25": pct(Rs, 25),
        "R_typical_p75": pct(Rs, 75),
        "R_typical_min": Rs[0],
        "R_typical_max": Rs[-1],
        "R_recurrence_weighted_mean": round(Rw, 1),
        "R_range_over_screen_params": {
            "conservative_1280x800_median": statistics.median(
                sorted(r["R_by_screen"]["conservative_1280x800"] for r in recs)),
            "retina_1728x1117_median": statistics.median(
                sorted(r["R_by_screen"]["retina_1728x1117"] for r in recs)),
        },
        "replay_tokens_median": statistics.median(dens),
        "steps_median": statistics.median(steps),
        "occurrences_median": statistics.median(occ),
        "occurrences_max": max(occ),
    }


def r_by_steps(recs):
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in recs:
        band = ("3-4" if r["steps"] <= 4 else "5-8" if r["steps"] <= 8 else
                "9-15" if r["steps"] <= 15 else "16+")
        buckets[band].append(r["R_typical"])
    return {b: {"n": len(v), "R_median": round(statistics.median(v), 1)}
            for b, v in sorted(buckets.items())}


if __name__ == "__main__":
    out = {"db": DB, "tokenizer": TOKENIZER, "params": {
        "MIN_FREQUENCY": MIN_FREQUENCY, "SESSION_GAP_S": SESSION_GAP_S,
        "MIN_STEPS": MIN_STEPS, "MAX_STEPS": MAX_STEPS,
        "CTX_IN_PER_STEP": CTX_IN_PER_STEP, "REASON_OUT_PER_STEP": REASON_OUT_PER_STEP,
        "screen_params_tok": SCREEN_PARAMS,
        "numerator": "MODELED (k*(screenshot+ctx+reason), Anthropic w*h/750 image tokens)",
        "denominator": "MEASURED (tiktoken of parametric routine replay script)",
    }, "results": {}}
    for g in ("action", "url"):
        recs = measure(g)
        out["results"][g] = {
            "summary": summarize(recs, g),
            "coverage_h": getattr(mine_routines, "last_coverage", {}),
            "R_by_steps": r_by_steps(recs),
            "top_routines": sorted(recs, key=lambda r: -r["occurrences"] * r["steps"])[:12],
        }
    print(json.dumps(out, indent=2))
