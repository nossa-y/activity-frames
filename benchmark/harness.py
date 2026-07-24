#!/usr/bin/env python3
"""
LLM harness for the activity-frames benchmark. Uses the authenticated claude CLI as
the frontier LLM (swap MODEL / the call for GPT with an OpenAI key).

For each day it: (1) generates R LLM summaries of the raw rows, (2) has the SAME
agent answer the day's ground-truth questions from each representation
{af_context, raw_rows, llm_summary}, (3) grades answers vs the oracle and flags
hallucinations. Also re-generates the AF block R times to show byte-identity.
"""
import subprocess, json, os, re, sys, statistics, hashlib

BASE = os.path.dirname(os.path.abspath(__file__))
# Model is configurable so the SAME harness benchmarks any model the `claude` CLI
# exposes (e.g. BENCH_MODEL=claude-opus-4-5). Results are written per-model to
# results.<model>.json, so runs never clobber each other.
MODEL = os.environ.get("BENCH_MODEL", "claude-sonnet-4-5")
DEFAULT_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "240"))  # larger inputs / slower models: raise it
MAX_CTX_TOK = 190000   # representations bigger than this overflow the model's window
R = 3                  # repetitions for reproducibility

def claude(prompt, timeout=DEFAULT_TIMEOUT):
    """One non-interactive, tool-free LLM call. Returns stdout text."""
    try:
        p = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--allowed-tools", ""],
            input=prompt, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "CLAUDE_CODE_NO_FLICKER": "1"})
        return p.stdout.strip()
    except subprocess.TimeoutExpired:
        return "__TIMEOUT__"

def summarize(raw_rows):
    prompt = ("Summarize the following raw screen-activity capture for ONE day into a concise "
              "natural-language summary a personal assistant could rely on. Capture the key facts: "
              "which applications were used and roughly how many minutes each, the main websites and "
              "entities visited, when the day started/ended, and how it was structured. Be accurate and "
              "specific with numbers. Do not use any tools.\n\nDATA (raw rows):\n" + raw_rows)
    return claude(prompt)

def answer(rep_text, questions):
    qlines = "\n".join(f'{q["id"]}: {q["prompt"]}' for q in questions)
    prompt = ("You are given a record of a user's computer activity for one day, in the DATA block. "
              "Answer every question using ONLY the DATA block — do not use tools, do not guess beyond the data. "
              "If the data genuinely does not support an answer, use null.\n"
              "Output ONLY a JSON object mapping each question id to a short answer (a number, a name, or yes/no). "
              "No prose, no code fences.\n\nDATA:\n" + rep_text + "\n\nQUESTIONS:\n" + qlines +
              '\n\nJSON (ids: ' + ", ".join(q["id"] for q in questions) + "):")
    out = claude(prompt)
    m = re.search(r"\{.*\}", out, re.S)
    if not m:
        return {}, out
    try:
        return json.loads(m.group(0)), out
    except Exception:
        return {}, out

# ---- grading ----
def _num(x):
    if x is None: return None
    m = re.search(r"-?\d+(\.\d+)?", str(x).replace(",", ""))
    return float(m.group(0)) if m else None

def _yesno(x):
    s = str(x).strip().lower()
    if s.startswith("y") or "true" in s: return "yes"
    if s.startswith("n") or "false" in s: return "no"
    return s

def grade(q, resp):
    g, ans, param = q["grade"], q["answer"], q["param"]
    if resp is None or resp == "":
        return False
    if g == "app":
        a, r = str(ans).lower(), str(resp).lower()
        return a in r or r in a or a.split()[-1] in r  # "Google Chrome" ~ "Chrome"
    if g == "yesno":
        return _yesno(resp) == _yesno(ans)
    if g == "time_min":
        try:
            rh, rm = map(int, re.findall(r"\d+", str(resp))[:2]); ah, am = map(int, ans.split(":"))
            return abs((rh * 60 + rm) - (ah * 60 + am)) <= param
        except Exception:
            return False
    rn = _num(resp)
    if rn is None: return False
    an = float(ans)
    if g == "num_abs":
        return abs(rn - an) <= param
    if g == "num_pct":
        return abs(rn - an) <= max(param * an, 1)
    if g == "num_pct_or_abs":
        pct, ab = param
        return abs(rn - an) <= pct * an or abs(rn - an) <= ab
    return False

def run():
    oracle = json.load(open(f"{BASE}/oracle.json"))
    questions = json.load(open(f"{BASE}/questions.json"))
    days = [d for d in oracle if oracle[d]]
    results = {
        "model": MODEL,
        "method": {
            "grading_tolerance": {"numeric_pct": 0.30, "time_minutes": 45, "count_abs": 2},
            "representation_reps": R,
            "max_context_tokens": MAX_CTX_TOK,
            "sampling": "claude CLI defaults; temperature/seed not pinned",
            "accuracy_note": ("one answering pass per representation; the R "
                              "repetitions measure representation reproducibility, "
                              "not accuracy variance"),
            "day_selection": ("7 most recent consecutive active days + the nearest "
                              "prior day whose raw serialization overflows the "
                              "context window"),
            "excluded_question_types": ["longest_session (session-gap convention)",
                                        "profile_recall (exceeds compact-block budget)"],
        },
        "days": {},
    }

    for day in days:
        rep = json.load(open(f"{BASE}/reps/{day}.json"))
        qs = questions[day]
        print(f"\n=== {day} ===", flush=True)

        # (a) reproducibility of the representation itself
        af_hashes = {hashlib.sha256(subprocess.run(
            [os.path.expanduser("~/nocta/activity-frames/.venv/bin/aframes"), "day", day, "-f", "context"],
            capture_output=True, text=True, env={**os.environ, "AFRAMES_DB": os.path.expanduser("~/.nocta/data/db.sqlite")}
        ).stdout.encode()).hexdigest() for _ in range(R)}
        # If the raw rows exceed the model's context window, BOTH raw-QA and
        # LLM-summarization are infeasible — a result in itself (AF's ~2k block is not).
        raw_overflow = rep["tok_raw_rows"] > MAX_CTX_TOK
        summaries = []
        if not raw_overflow:
            for i in range(R):
                print(f"  summarizing raw rows (run {i+1}/{R})…", flush=True)
                summaries.append(summarize(rep["raw_rows"]))
        sum_hashes = {hashlib.sha256(s.encode()).hexdigest() for s in summaries}

        # (b) QA per representation
        reps = {"af_context": rep["af_context"]}
        if not raw_overflow:
            reps["raw_rows"] = rep["raw_rows"]
            reps["llm_summary"] = summaries[0]
        qa = {}
        for name, text in reps.items():
            print(f"  answering from {name}…", flush=True)
            ans, _ = answer(text, qs)
            graded = [{"id": q["id"], "cat": q["cat"], "ok": grade(q, ans.get(q["id"])),
                       "halluc": q["cat"] == "halluc" and _yesno(ans.get(q["id"], "")) == "yes",
                       "resp": ans.get(q["id"])} for q in qs]
            qa[name] = {"acc": round(sum(g["ok"] for g in graded) / len(graded), 3),
                        "halluc": sum(g["halluc"] for g in graded),
                        "graded": graded}

        results["days"][day] = {
            "af_bytes_identical": len(af_hashes) == 1,
            "summary_distinct_of_R": len(sum_hashes), "R": R,
            "raw_overflow": raw_overflow,
            "summaries": summaries, "qa": qa,
        }
        for name, r in qa.items():
            print(f"    {name:>12}: acc={r['acc']:.0%}  halluc={r['halluc']}", flush=True)
        print(f"    AF byte-identical x{R}: {len(af_hashes)==1} | summary distinct: {len(sum_hashes)}/{R}", flush=True)
        json.dump(results, open(f"{BASE}/results.{MODEL}.json", "w"), ensure_ascii=False, indent=2)
    print(f"\nwrote results.{MODEL}.json")

if __name__ == "__main__":
    if sys.argv[1:] == ["test"]:
        # single-day smoke test
        oracle = json.load(open(f"{BASE}/oracle.json"))
        questions = json.load(open(f"{BASE}/questions.json"))
        day = "2026-07-05"
        rep = json.load(open(f"{BASE}/reps/{day}.json"))
        ans, raw = answer(rep["af_context"], questions[day])
        print("AF answers:", json.dumps(ans, indent=2)[:500])
        graded = [(q["id"], grade(q, ans.get(q["id"])), ans.get(q["id"]), q["answer"]) for q in questions[day]]
        for gid, ok, r, a in graded: print(f"  {gid:>12}: {'OK ' if ok else 'XX '} got={r} want={a}")
        print("acc:", sum(g[1] for g in graded) / len(graded))
    else:
        run()
