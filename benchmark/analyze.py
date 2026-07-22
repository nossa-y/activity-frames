#!/usr/bin/env python3
"""
Aggregate one or more results.<model>.json runs into the paper's headline numbers
and a multi-model LaTeX table. Every results.<model>.json produced by harness.py is
picked up automatically, so adding a model is just running the harness again.
"""
import json, os, glob, re, statistics
BASE = os.path.dirname(os.path.abspath(__file__))
REPS = ["af_context", "raw_rows", "llm_summary"]
NICE = {"af_context": "Activity frames", "raw_rows": "Raw rows", "llm_summary": "LLM summary"}
ORDER = ["raw_rows", "llm_summary", "af_context"]  # table row order


def model_label(m):
    m = m.lower()
    if "opus" in m:   return "Opus 4.5"
    if "sonnet" in m: return "Sonnet 4.5"
    if "gpt-4o" in m or "gpt4o" in m: return "GPT-4o"
    if "gemini" in m: return "Gemini"
    return m


def _num(x):
    if x is None: return None
    mt = re.search(r"-?\d+(\.\d+)?", str(x).replace(",", ""))
    return float(mt.group(0)) if mt else None


def analyze_one(R, oracle, questions):
    """Return per-representation metrics for one model's results dict."""
    days = R["days"]
    out = {}
    for rp in REPS:
        oks, cat, days_run = [], {}, 0
        dur_errs = []
        for d, dv in days.items():
            qa = dv["qa"].get(rp)
            if not qa:
                continue
            days_run += 1
            for g in qa["graded"]:
                oks.append(g["ok"])
                cat.setdefault(g["cat"], []).append(g["ok"])
            # duration error on the dominant app's active minutes (the top_min question)
            od = oracle[d]
            true_min = od["app_minutes"][od["top_app"]]
            tm = next((g for g in qa["graded"] if g["id"] == "top_min"), None)
            if tm is not None and true_min:
                got = _num(tm["resp"])
                if got is not None:
                    dur_errs.append(abs(got - true_min) / true_min * 100.0)
        time_cat = cat.get("time", [])
        out[rp] = {
            "days_run": days_run,
            "n": len(oks),
            "acc": round(100 * statistics.mean(oks), 1) if oks else None,
            "time_acc": round(100 * statistics.mean(time_cat), 1) if time_cat else None,
            "halluc_ok": round(100 * statistics.mean(cat.get("halluc", [0])), 1),
            "dur_err": round(statistics.mean(dur_errs), 1) if dur_errs else None,
            "by_cat": {c: round(100 * statistics.mean(v), 1) for c, v in cat.items()},
        }
    # reproducibility signals (model-independent for AF; per-model for the summary)
    out["_meta"] = {
        "af_identical": all(dv["af_bytes_identical"] for dv in days.values()),
        "summary_distinct_mean": round(statistics.mean(
            [dv["summary_distinct_of_R"] for dv in days.values() if dv["summary_distinct_of_R"]]), 2),
        "overflow_days": [d for d, dv in days.items() if dv["raw_overflow"]],
        "n_days": len(days),
    }
    return out


def main():
    oracle = json.load(open(f"{BASE}/oracle.json"))
    questions = json.load(open(f"{BASE}/questions.json"))
    files = sorted(glob.glob(f"{BASE}/results.*.json"))
    if not files:
        # backward-compat: single unnamed run
        files = [f"{BASE}/results.json"] if os.path.exists(f"{BASE}/results.json") else []
    models = {}
    for f in files:
        R = json.load(open(f))
        models[R["model"]] = analyze_one(R, oracle, questions)

    order = sorted(models, key=lambda m: (0 if "sonnet" in m else 1 if "opus" in m else 2))
    print("MODELS:", ", ".join(f"{model_label(m)} ({m})" for m in order), "\n")
    for m in order:
        A = models[m]
        print(f"=== {model_label(m)} [{m}] ===")
        print(f"{'rep':<18}{'days':>6}{'overall':>9}{'time':>7}{'dur.err':>9}{'halluc':>8}")
        for rp in ORDER:
            a = A[rp]
            acc = f"{a['acc']}%" if a['acc'] is not None else "--"
            ta = f"{a['time_acc']}%" if a['time_acc'] is not None else "--"
            de = f"{a['dur_err']}%" if a['dur_err'] is not None else "--"
            print(f"{NICE[rp]:<18}{a['days_run']:>5}/{A['_meta']['n_days']}{acc:>9}{ta:>7}{de:>9}{a['halluc_ok']:>7}%")
        print(f"  AF byte-identical: {A['_meta']['af_identical']} | "
              f"summary distinct/{3}: {A['_meta']['summary_distinct_mean']} | "
              f"overflow days: {A['_meta']['overflow_days'] or 'none'}\n")

    _write_table(models, order)


def _write_table(models, order):
    """Two-model (or N-model) LaTeX table: overall accuracy + duration error per model."""
    ncol = len(order)
    col = "l" + "cc" * ncol
    head1 = " & " + " & ".join(rf"\multicolumn{{2}}{{c}}{{{model_label(m)}}}" for m in order) + r" \\"
    cmid = " ".join(rf"\cmidrule(lr){{{2+2*i}-{3+2*i}}}" for i in range(ncol))
    head2 = "Representation & " + " & ".join(r"Acc. & Dur.\ err." for _ in order) + r" \\"
    lines = [
        r"\begin{table}[t]", r"\centering",
        r"\caption{Downstream QA across two model tiers (8 days, 64 ground-truth "
        r"questions, graded against the independent oracle). ``Acc.'' is overall "
        r"accuracy; ``Dur.\ err.'' is the mean absolute error on the dominant "
        r"application's active minutes. The same agent answers from each "
        r"representation at each tier.}",
        r"\label{tab:qa}", r"\footnotesize", r"\setlength{\tabcolsep}{4pt}",
        rf"\begin{{tabular}}{{{col}}}", r"\toprule",
        head1, cmid, head2, r"\midrule",
    ]
    for rp in ORDER:
        cells = []
        for m in order:
            a = models[m][rp]
            acc = f"{a['acc']}\\%" if a['acc'] is not None else "--"
            de = f"{a['dur_err']}\\%" if a['dur_err'] is not None else "--"
            cells += [acc, de]
        name = rf"\textbf{{{NICE[rp]}}}" if rp == "af_context" else NICE[rp]
        row = name + " & " + " & ".join(cells) + r" \\"
        if rp == "af_context":
            row = "\\midrule\n" + row
        lines.append(row)
    lines += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    open(f"{BASE}/table_qa.tex", "w").write("\n".join(lines))
    print("wrote table_qa.tex")


if __name__ == "__main__":
    main()
