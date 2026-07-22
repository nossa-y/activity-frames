#!/usr/bin/env python3
"""
Generate the ground-truth question set for each day from the oracle. Every question
has a deterministic answer and a grading rule. Includes 'absent' probes (facts that
are FALSE) to measure hallucination — a system that says "yes, they used Photoshop"
when they didn't is fabricating.
"""
import json, os

BASE = os.path.dirname(os.path.abspath(__file__))
ABSENT_APPS = ["Adobe Photoshop", "Microsoft Excel", "Blender", "Logic Pro"]
ABSENT_DOMAINS = ["netflix.com", "espn.com", "booking.com"]

def q(qid, cat, prompt, answer, grade, param=None):
    return {"id": qid, "cat": cat, "prompt": prompt, "answer": answer, "grade": grade, "param": param}

def for_day(day, o):
    apps = [a for a, _ in o["ranked_apps"]]
    top = o["top_app"]
    qs = []
    qs.append(q("top_app", "rank", f"Which single application did the user spend the most active time in on {day}? Reply with only the application name.", top, "app"))
    qs.append(q("top_min", "time", f"Approximately how many minutes of active time did the user spend in {top} on {day}? Reply with only a number.", o["app_minutes"][top], "num_pct", 0.30))
    qs.append(q("n_apps", "count", f"How many distinct applications did the user use on {day}? Reply with only a number.", o["distinct_apps"], "num_abs", 2))
    if len(apps) >= 3:
        a, b = apps[0], apps[2]
        more = a if o["app_minutes"][a] >= o["app_minutes"][b] else b
        qs.append(q("rank_pair", "rank", f"Did the user spend more active time in '{a}' or '{b}' on {day}? Reply with only one of those two names.", more, "app"))
    # a domain that WAS visited
    if o["distinct_domains"]:
        dom = max(o["distinct_domains"], key=len) if False else o["distinct_domains"][0]
        # prefer a recognizable domain if present
        for cand in ["linkedin.com", "github.com", "instagram.com", "youtube.com", "mail.google.com"]:
            if any(cand in d for d in o["distinct_domains"]):
                dom = cand; break
        qs.append(q("dom_yes", "fact", f"Did the user visit any page on '{dom}' on {day}? Reply yes or no.", "yes", "yesno"))
    # a domain that was NOT visited (hallucination probe)
    absent_dom = next((d for d in ABSENT_DOMAINS if not any(d in x for x in o["distinct_domains"])), None)
    if absent_dom:
        qs.append(q("dom_no", "halluc", f"Did the user visit any page on '{absent_dom}' on {day}? Reply yes or no.", "no", "yesno"))
    qs.append(q("first", "time", f"Around what local time did the user's activity start on {day}? Reply as HH:MM (24h).", o["first_activity"], "time_min", 45))
    # (LinkedIn-profile-count and longest-session are intentionally excluded: the first
    #  is lost to the compact block's token budget for high-count days, the second is
    #  session-gap-definition-dependent — both would compare conventions, not faithfulness.)
    # an app that was NOT used (hallucination probe)
    absent_app = next((a for a in ABSENT_APPS if a not in o["app_minutes"]), None)
    if absent_app:
        qs.append(q("app_no", "halluc", f"Did the user use '{absent_app}' on {day}? Reply yes or no.", "no", "yesno"))
    for x in qs:
        x["day"] = day
    return qs

if __name__ == "__main__":
    oracle = json.load(open(f"{BASE}/oracle.json"))
    allq = {d: for_day(d, o) for d, o in oracle.items() if o}
    json.dump(allq, open(f"{BASE}/questions.json", "w"), ensure_ascii=False, indent=2)
    n = sum(len(v) for v in allq.values())
    print(f"generated {n} questions across {len(allq)} days")
    cats = {}
    for v in allq.values():
        for x in v: cats[x["cat"]] = cats.get(x["cat"], 0) + 1
    print("by category:", cats)
