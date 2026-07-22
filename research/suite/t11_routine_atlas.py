"""T11 - the routine atlas: WHERE the automatable work concentrates.

VALIDATES / EXTENDS the findings by mapping the recurring routines back to apps,
sites, and hours of the day. This is the actionable output: it tells you which
apps and which times of day carry the routines worth compiling first, and it is
what a live bench (T-live) should draw its target routines from.

METHOD: mine recurring routines carrying their concrete captured rows; attribute
each routine to the app / site / hour of a representative occurrence; aggregate.

REPORTS: top apps by number of distinct recurring routines and by total
occurrences; top sites; an hour-of-day histogram of routine occurrences; and the
single highest-yield routines (occurrences x steps).
"""
from collections import defaultdict
from urllib.parse import urlsplit
from _lib import Test, conn, action_sessions, ts, MIN_FREQUENCY, MIN_STEPS, MAX_STEPS, is_specific


def url_t(u):
    try:
        p = urlsplit(u or "")
    except ValueError:
        return ""
    parts = [x for x in p.path.split("/") if x]
    h = p.hostname or ""
    return f"{h}/{parts[0]}/*" if parts else h


def main():
    t = Test("t11_routine_atlas", "map of where recurring routines concentrate (apps/sites/hours)")
    c = conn()
    sessions = action_sessions(c, with_rows=True)  # each step = (canon, row)

    # mine frequent specific n-grams over canonical steps, but keep rows for attribution
    counts = defaultdict(int)
    for sess in sessions:
        steps = [s for s, _ in sess]
        L = len(steps)
        for n in range(MIN_STEPS, min(MAX_STEPS, L) + 1):
            for i in range(L - n + 1):
                counts[tuple(steps[i:i + n])] += 1
    freq = {g: n for g, n in counts.items()
            if n >= MIN_FREQUENCY and sum(is_specific(s) for s in g) >= 2}
    # maximal
    kept = []
    for g in sorted(freq, key=lambda g: -len(g)):
        gs = " -> ".join(g)
        if any(gs in " -> ".join(k) and freq[g] == freq[k] for k in kept):
            continue
        kept.append(g)

    by_app_routines = defaultdict(int)
    by_app_occ = defaultdict(int)
    by_site = defaultdict(int)
    by_hour = defaultdict(int)
    top = []
    for g in kept:
        n = len(g)
        # find a representative occurrence and read its app/site/hour
        app = site = None
        hour = None
        for sess in sessions:
            steps = [s for s, _ in sess]
            for i in range(len(steps) - n + 1):
                if tuple(steps[i:i + n]) == g:
                    row = sess[i][1]
                    app = row["app_name"] or "?"
                    site = url_t(row["browser_url"]) if row["browser_url"] else None
                    tt = ts(row["timestamp"])
                    hour = tt.hour if tt else None
                    break
            if app:
                break
        occ = freq[g]
        by_app_routines[app] += 1
        by_app_occ[app] += occ
        if site:
            by_site[site] += occ
        if hour is not None:
            by_hour[hour] += occ
        top.append({"signature": " -> ".join(g)[:80], "app": app, "site": site,
                    "steps": n, "occurrences": occ, "yield": occ * n})
    c.close()

    top.sort(key=lambda x: -x["yield"])
    topapps = sorted(by_app_routines.items(), key=lambda kv: -kv[1])[:10]
    t.check("routines found", len(kept), lo=1)
    t.check("distinct apps hosting routines", len(by_app_routines), lo=1)
    print("      top apps by #routines:", ", ".join(f"{a}({n})" for a, n in topapps[:6]))
    return t.finish({
        "n_routines": len(kept),
        "top_apps_by_routine_count": [{"app": a, "routines": n} for a, n in topapps],
        "top_apps_by_occurrences": sorted(
            [{"app": a, "occurrences": n} for a, n in by_app_occ.items()],
            key=lambda x: -x["occurrences"])[:10],
        "top_sites_by_occurrences": sorted(
            [{"site": s, "occurrences": n} for s, n in by_site.items()],
            key=lambda x: -x["occurrences"])[:10],
        "hour_of_day_histogram": {str(h): by_hour.get(h, 0) for h in range(24)},
        "highest_yield_routines": top[:15],
        "interpretation": "compile the top-yield routines first; draw live-bench targets from here.",
    })


if __name__ == "__main__":
    main()
