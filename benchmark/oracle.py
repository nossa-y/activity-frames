#!/usr/bin/env python3
"""
Ground-truth oracle for the activity-frames benchmark.

Computes measured facts for a local day DIRECTLY from the raw capture DB with a
transparent, documented method — deliberately independent of the activity-frames
compiler, so using it as the reference is not circular. We separately verify that
the AF compiler's numbers agree with this oracle (establishing AF is correct),
then judge every representation (AF block, raw rows, LLM summary) against it.

Dwell method (standard inactivity-timeout, cf. web sessionization): walk the
time-ordered focused frames; credit each frame the gap to the next frame, capped
at CAP_S seconds (a longer gap = the user stepped away, not continuous use).
"""
import sqlite3, sys, json, os
from collections import defaultdict
from urllib.parse import urlparse
from datetime import datetime

DB = os.path.expanduser("~/.nocta/data/db.sqlite")
CAP_S = 60          # inter-frame gap cap for dwell (seconds)
SESSION_GAP_S = 120 # a gap beyond this ends a single-app session

def _rows(day):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # local-day window
    q = """SELECT timestamp, app_name, window_name, browser_url
           FROM frames
           WHERE date(timestamp,'localtime') = ?
             AND app_name IS NOT NULL
           ORDER BY timestamp"""
    out = []
    for ts, app, win, url in con.execute(q, (day,)):
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except Exception:
            continue
        out.append((t, app or "", win or "", url or ""))
    con.close()
    return out

def _host(url):
    if not url:
        return None
    try:
        h = urlparse(url).hostname or None
        return h[4:] if h and h.startswith("www.") else h
    except Exception:
        return None

def compute(day):
    rows = _rows(day)
    if not rows:
        return None
    # dwell per app
    mins = defaultdict(float)
    for i, (t, app, _, _) in enumerate(rows):
        nxt = rows[i + 1][0] if i + 1 < len(rows) else t
        mins[app] += min(max(nxt - t, 0), CAP_S)
    mins = {a: round(s / 60.0, 1) for a, s in mins.items()}
    ranked = sorted(mins.items(), key=lambda kv: -kv[1])

    # domains + linkedin profiles
    domains, li_profiles = set(), set()
    for _, app, _, url in rows:
        h = _host(url)
        if h:
            domains.add(h)
        if url and "linkedin.com/in/" in url:
            li_profiles.add(url.split("linkedin.com/in/")[1].split("/")[0].split("?")[0])

    # longest single-app session (contiguous same-app run, gaps < SESSION_GAP_S)
    longest = 0.0
    run_app, run_start, prev_t = None, None, None
    for t, app, _, _ in rows:
        if app == run_app and prev_t is not None and (t - prev_t) <= SESSION_GAP_S:
            pass
        else:
            if run_app is not None:
                longest = max(longest, prev_t - run_start)
            run_app, run_start = app, t
        prev_t = t
    if run_app is not None and prev_t is not None:
        longest = max(longest, prev_t - run_start)

    return {
        "day": day,
        "frames": len(rows),
        "distinct_apps": len(mins),
        "total_active_min": round(sum(mins.values()), 1),
        "app_minutes": mins,
        "ranked_apps": ranked,
        "top_app": ranked[0][0] if ranked else None,
        "first_activity": datetime.fromtimestamp(rows[0][0]).strftime("%H:%M"),
        "last_activity": datetime.fromtimestamp(rows[-1][0]).strftime("%H:%M"),
        "distinct_domains": sorted(domains),
        "n_domains": len(domains),
        "linkedin_profiles": sorted(li_profiles),
        "n_linkedin_profiles": len(li_profiles),
        "longest_session_min": round(longest / 60.0, 1),
    }

if __name__ == "__main__":
    days = sys.argv[1:] or ["2026-07-03"]
    res = {d: compute(d) for d in days}
    print(json.dumps(res, indent=2, ensure_ascii=False))
