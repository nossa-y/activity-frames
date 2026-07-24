#!/usr/bin/env python3
"""
Build the three representations an agent could receive for a day:
  1. af_context  — activity-frames compact context block (the paper's proposal)
  2. raw_rows    — raw capture rows serialized as JSON (what a search API returns)
  3. (llm_summary is produced by the LLM harness, not here)
Also token-counts each (cl100k_base, matching the paper) and saves to reps/<day>.json.
"""
import subprocess, sqlite3, json, os, sys

DB = os.path.expanduser("~/.nocta/data/db.sqlite")
AF = os.path.expanduser("~/nocta/activity-frames/.venv/bin/aframes")
OUT = os.path.dirname(os.path.abspath(__file__)) + "/reps"
os.makedirs(OUT, exist_ok=True)

try:
    import tiktoken
    ENC = tiktoken.get_encoding("cl100k_base")
    ntok = lambda s: len(ENC.encode(s))
except Exception:
    ntok = lambda s: len(s) // 4  # rough fallback

def af_context(day):
    # What AF actually provides an agent: the deterministic per-app ledger
    # (aframes apps) PLUS the compact chronological context block. Both are
    # measured, model-free outputs; together still ~2k tokens.
    env = {**os.environ, "AFRAMES_DB": DB}
    ledger = subprocess.run([AF, "apps", day], capture_output=True, text=True, env=env, timeout=120).stdout.strip()
    block = subprocess.run([AF, "day", day, "-f", "context"], capture_output=True, text=True, env=env, timeout=120).stdout.strip()
    return f"TIME PER APPLICATION (measured, full day, ranked):\n{ledger}\n\n{block}"

def raw_rows(day):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = """SELECT timestamp, app_name, window_name, browser_url
           FROM frames WHERE date(timestamp,'localtime')=? AND app_name IS NOT NULL
           ORDER BY timestamp"""
    rows = [{"t": ts, "app": a, "window": w, "url": u}
            for ts, a, w, u in con.execute(q, (day,))]
    con.close()
    return json.dumps(rows, ensure_ascii=False)

def build(day):
    ctx, raw = af_context(day), raw_rows(day)
    rep = {"day": day, "af_context": ctx, "raw_rows": raw,
           "tok_af_context": ntok(ctx), "tok_raw_rows": ntok(raw)}
    json.dump(rep, open(f"{OUT}/{day}.json", "w"), ensure_ascii=False)
    return rep

if __name__ == "__main__":
    for day in sys.argv[1:]:
        r = build(day)
        print(f"{day}: af_context={r['tok_af_context']:>6} tok | raw_rows={r['tok_raw_rows']:>7} tok | "
              f"reduction={r['tok_raw_rows']/max(r['tok_af_context'],1):.0f}x")
