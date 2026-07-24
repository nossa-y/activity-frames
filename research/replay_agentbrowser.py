"""Parametric routine replay executor.

PARAMETRIC ROUTINE REPLAY: re-run the STRUCTURE of a routine the user has done before
(open composer -> click recipient -> type message -> send), filling its variable SLOTS
with new values from the current request (recipient Lisa->Luca, message text -> new text).
We never replay the identical past action; we reuse the "way" and drop in new parameters.
"Deterministic" here refers to how each step is GROUNDED - by accessibility role+name at
~0 LLM tokens, reproducibly - not to the content, which is always new.

REPRODUCES the nocta-execute browser stack and
adds the compiled 0-token fast-path. Does NOT modify the internal nocta-execute skill
(that stays untouched, per Nossa); this is a standalone reproduction of its agent-browser
approach (daemon-reuse, real profile, human pacing, safety) with the one novel piece on
top: deterministic role+name grounding of a compiled plan, so replay costs ~0 LLM tokens.

The split: this file is the compiled fast-path (grounding ladder tier 1 role+name / tier 2
OCR fingerprint, 0 tokens); on a miss with --deopt it hands off to a local LLM step (the
nocta-execute-style interpreter). The daemon-management + pacing reproduce the hard-won
lessons in actions/browser-real-chrome-profile.md + docs/lessons/browser.md (launch bound
--profile ... --headed ONCE and REUSE; never cycle close/open into the headless fallback).

STATUS: parser verified on a real live snapshot (100%); a full live run_plan (real clicks)
is the remaining validation - do it once the browser is free.

  python3 replay_agentbrowser.py plan.json [--profile "Profile 3"] [--dry-run] [--allow-destructive] [--deopt]
  python3 replay_agentbrowser.py --selftest      # offline: validate parse+locate, no browser

plan.json = [{"op":"click|type","target":"Compose message","role":"button",
              "ocr_text":"Compose","value":"hi","guard":{"expect_element":"Compose message"}}, ...]
"""
from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlparse

LLAMA_URL = os.environ.get("LLAMA_URL", "http://localhost:8081") + "/v1/chat/completions"

# Fire only when a destructive verb is the IMPERATIVE HEAD of a control's accessible name
# (a short action button: "Post", "Send", "Send invitation", "Connect") - NOT when the verb
# merely appears inside a safe phrase ("Start a post", "Text editor for creating content").
# Anchored-at-start avoids the false positive that would otherwise block the composer opener.
# KNOWN EDGE (ISSUES): a confirm control phrased "Yes, delete" (verb not at head) slips past;
# acceptable because plans come from recorded non-destructive routines + discard is done out-of-band.
_DESTRUCTIVE_VERBS = ("send", "post", "publish", "share", "connect", "delete", "remove",
                      "pay", "buy", "submit", "confirm", "transfer", "archive", "discard",
                      "checkout", "purchase", "withdraw", "tweet", "invite", "follow", "apply")
DESTRUCTIVE = re.compile(r"^(?:" + "|".join(_DESTRUCTIVE_VERBS) + r")\b", re.I)


def is_destructive(*names):
    """True if any name's imperative head is a destructive verb (see DESTRUCTIVE)."""
    return any(DESTRUCTIVE.match(norm(n)) for n in names if n)

_ROLE_ALIASES = {"axbutton": "button", "axtextfield": "textbox", "axstatictext": "text",
                 "axlink": "link", "textbox": "textbox", "button": "button", "link": "link"}


def norm(s):
    return re.sub(r"\s+", " ", (s or "").replace("‎", "")).strip().lower()


def role_match(a, b):
    if not b:
        return True
    a, b = norm(a), norm(b)
    return a == b or _ROLE_ALIASES.get(a, a) == _ROLE_ALIASES.get(b, b)


def parse_snapshot(text):
    """agent-browser snapshot lines look like: `- button "Compose message" [ref=e36]`
    or `- textbox [ref=e40]`. Extract (role, name, ref) for every element with a ref."""
    items = []
    for line in text.splitlines():
        rm = re.search(r'\bref=(e\d+)\b', line)     # ref can sit anywhere in the bracket
        if not rm:
            continue
        role_m = re.search(r'-\s+([A-Za-z][\w-]*)', line)
        name_m = re.search(r'"([^"]*)"', line)      # first quoted string = accessible name
        items.append({"role": role_m.group(1) if role_m else "",
                      "name": name_m.group(1) if name_m else "",
                      "ref": rm.group(1)})
    return items


def locate(items, target, role="", ocr_text=""):
    """Grounding ladder. Returns (item, tier) or (None, 0)."""
    t = norm(target)
    # tier 1: accessibility (role+name), then name-exact, then name-contains
    if t:
        for it in items:
            if norm(it["name"]) == t and role_match(it["role"], role):
                return it, 1
        for it in items:
            if norm(it["name"]) == t:
                return it, 1
        cand = [it for it in items if t in norm(it["name"])]
        if cand:
            return min(cand, key=lambda it: len(it["name"])), 1
    # tier 2: the recorded OCR/visible-text fingerprint
    o = norm(ocr_text)
    if o:
        for it in items:
            if norm(it["name"]) == o or (o and o in norm(it["name"])):
                return it, 2
    return None, 0


# ---- agent-browser driver (reproduces nocta-execute's operational lessons) ---
def ab(*args, timeout=30):
    return subprocess.run(["agent-browser", *args], capture_output=True, text=True, timeout=timeout)


def snapshot():
    return ab("snapshot", timeout=45).stdout


def pace(lo=1.0, hi=3.0):
    """Human pacing between actions (anti-bot). Real jittered sleep - this is a
    standalone script the operator runs, so time.sleep is fine here."""
    time.sleep(random.uniform(lo, hi))


def ensure_daemon(profile="Profile 3", url="about:blank"):
    """Reproduce the daemon-reuse gotcha (docs/lessons/browser.md): a headed real-Chrome
    daemon must be launched ONCE bound with --profile ... --headed and then REUSED; if it
    died and you `open` again it silently respawns a HEADLESS throwaway. So: if a headed
    real-Chrome agent-browser is already up, reuse it; else launch bound. Verify it is NOT
    the headless fallback before trusting any snapshot. Never pkill blindly (another agent
    may be driving Chrome)."""
    pid = subprocess.run(["pgrep", "-f", "agent-browser-profile|Google Chrome.*remote-debugging"],
                         capture_output=True, text=True).stdout.split()
    if pid:
        cmd = subprocess.run(["ps", "-p", pid[0], "-o", "command="],
                             capture_output=True, text=True).stdout
        if "--headless" not in cmd and "chrome-headless-shell" not in cmd and "playwright" not in cmd:
            return "reused-headed"
        return "WARNING-headless-fallback-detected (relaunch bound manually to avoid clobbering another agent's Chrome)"
    ab("--profile", profile, "--headed", "--args", "--disable-blink-features=AutomationControlled",
       "open", url, timeout=60)
    up = subprocess.run(["pgrep", "-f", "agent-browser-profile"], capture_output=True, text=True).stdout.split()
    if up:
        cmd = subprocess.run(["ps", "-p", up[0], "-o", "command="], capture_output=True, text=True).stdout
        return "launched-headed" if "--headless" not in cmd else "FAILED-launched-headless"
    return "FAILED-no-daemon"


def deopt_resolve(items, target, role):
    """DEOPT: tier 1/2 missed. Hand the current snapshot + the step goal to a LOCAL
    LLM (nocta-execute-style step) to pick the best ref, then resume the deterministic
    plan. Returns a ref or None. Local-first: refuses non-localhost."""
    if (urlparse(LLAMA_URL).hostname or "").lower() not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        return None
    cat = "\n".join(f'[{it["ref"]}] {it["role"]} "{it["name"]}"' for it in items if it["name"])[:4000]
    prompt = (f'Goal: click/type the {role or "element"} for "{target}".\nElements on screen:\n{cat}\n'
              f'Reply with ONLY the single best matching ref id (like e12), or NONE.')
    body = json.dumps({"messages": [{"role": "user", "content": prompt}],
                       "temperature": 0, "max_tokens": 400}).encode()
    try:
        req = urllib.request.Request(LLAMA_URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            content = json.load(r)["choices"][0]["message"].get("content") or ""
        m = re.findall(r"e\d+", content)
        return m[-1] if m else None      # the answer ref (after any reasoning)
    except Exception:
        return None


def run_plan(plan, dry_run=False, allow_destructive=False, deopt=False):
    steps_out = []
    for i, step in enumerate(plan):
        if i and not dry_run:
            pace(1.5, 3.0)          # let the previous action's DOM settle + human pacing
        target = step.get("target") or (step.get("guard") or {}).get("expect_element") or ""
        role = step.get("role") or (step.get("guard") or {}).get("expect_role") or ""
        snap = parse_snapshot(snapshot())
        it, tier = locate(snap, target, role, step.get("ocr_text", ""))
        if not it and deopt:                 # tier 1/2 missed -> LLM deopt step
            dref = deopt_resolve(snap, target, role)
            if dref:
                it, tier = {"ref": dref, "name": target, "role": role}, -1  # -1 = deopt-recovered
        rec = {"i": i, "op": step.get("op"), "target": target, "tier": tier,
               "ref": it["ref"] if it else None, "acted": False,
               "deopt": False, "deopt_recovered": tier == -1, "blocked": False}
        if not it:
            rec["deopt"] = True  # unresolved even after deopt
            steps_out.append(rec)
            continue
        nm = it["name"] or target
        if (DESTRUCTIVE.search(nm) or DESTRUCTIVE.search(target)) and not allow_destructive:
            rec["blocked"] = True
            rec["reason"] = f"destructive verb blocked: {nm}"
            steps_out.append(rec)
            continue
        if not dry_run:
            if step.get("op") == "type":
                ab("click", it["ref"]); pace(1, 2)
                ab("type", it["ref"], step.get("value", ""))
            else:
                ab("click", it["ref"])
            rec["acted"] = True
        steps_out.append(rec)
    summary = {
        "total": len(steps_out),
        "tier1": sum(s["tier"] == 1 for s in steps_out),
        "tier2": sum(s["tier"] == 2 for s in steps_out),
        "deopt_recovered": sum(bool(s.get("deopt_recovered")) for s in steps_out),
        "deopt_unresolved": sum(s["deopt"] for s in steps_out),
        "blocked": sum(s["blocked"] for s in steps_out),
        "acted": sum(s["acted"] for s in steps_out),
    }
    return {"mode": "dry_run" if dry_run else "executed", "summary": summary, "steps": steps_out}


# ---- offline self-test (no browser) -----------------------------------------
SAMPLE_SNAPSHOT = """
- generic [ref=e1]
  - button "Compose message" [ref=e12]
  - textbox "Write a message" [ref=e14]
  - link "Drafts" [ref=e16]
  - button "Save draft now" [ref=e18]
  - button "Send" [ref=e20]
"""
SELFTEST_PLAN = [
    {"op": "click", "target": "Compose message", "role": "button"},   # tier1 (name exact)
    {"op": "type", "target": "Write a message", "role": "textbox", "value": "hi"},  # tier1
    {"op": "click", "target": "Keep draft", "role": "button", "ocr_text": "Save draft now"},  # tier2: name absent, OCR matches
    {"op": "click", "target": "Attach file", "role": "button"},        # deopt (absent)
    {"op": "click", "target": "Send", "role": "button"},               # blocked (destructive)
]


def selftest():
    items = parse_snapshot(SAMPLE_SNAPSHOT)
    assert len(items) == 6, f"parsed {len(items)} refs, expected 6"
    outs = []
    for step in SELFTEST_PLAN:
        it, tier = locate(items, step["target"], step.get("role", ""), step.get("ocr_text", ""))
        nm = it["name"] if it else ""
        blocked = bool(it) and bool(DESTRUCTIVE.search(nm or step["target"]))
        outs.append((step["target"], tier, it["ref"] if it else None, "BLOCKED" if blocked else ""))
    exp = [("Compose message", 1, "e12"), ("Write a message", 1, "e14"),
           ("Keep draft", 2, "e18"), ("Attach file", 0, None), ("Send", 1, "e20")]
    ok = True
    for (tg, tier, ref, note), (etg, etier, eref) in zip(outs, exp):
        good = tier == etier and ref == eref
        ok = ok and good and (note == "BLOCKED" if tg == "Send" else True)
        print(f"  {'PASS' if good else 'FAIL'}  {tg}: tier{tier} ref={ref} {note}")
    print("SELFTEST", "PASS" if ok else "FAIL", "- grounding ladder ports cleanly onto agent-browser snapshots")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    plan = json.load(open(sys.argv[1]))
    print(json.dumps(run_plan(plan, dry_run="--dry-run" in sys.argv,
                              allow_destructive="--allow-destructive" in sys.argv,
                              deopt="--deopt" in sys.argv), indent=2))
