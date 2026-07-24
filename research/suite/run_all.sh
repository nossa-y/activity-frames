#!/usr/bin/env bash
# Exhaustive offline validation suite for the Routine Overhead Ratio findings.
# Runs the 11 offline experiments over your capture DB and writes suite_report.md.
# The public replication (t12, needs network) and the live money-demo (live/) are
# run separately - see README.md.
#
# Usage:
#   AFRAMES_CORPUS=/path/to/copy_of_db.sqlite ./run_all.sh
#   ./run_all.sh --with-network      # also run t12 (Mind2Web replication)
#
# ALWAYS point AFRAMES_CORPUS at a COPY of your recorder db.sqlite, never the
# live file. Everything here opens the DB read-only.

set -uo pipefail
cd "$(dirname "$0")"

: "${AFRAMES_CORPUS:=/tmp/corpus_ro.sqlite}"
export AFRAMES_CORPUS AFRAMES_DB="$AFRAMES_CORPUS"

if [ ! -f "$AFRAMES_CORPUS" ]; then
  echo "FATAL: capture DB not found at $AFRAMES_CORPUS"
  echo "Copy your recorder DB first, e.g.:  cp ~/.nocta/data/db.sqlite /tmp/corpus_ro.sqlite"
  exit 1
fi

OFFLINE=(t01_overhead_R t02_replay_compile t03_recurrence_h t04_h_null_baseline \
         t05_entropy_predictability t06_ivm_certify t07_instrument_card \
         t08_bench_modeled t09_R_sensitivity t10_determinism_stress t11_routine_atlas)
[ "${1:-}" = "--with-network" ] && OFFLINE+=(t12_public_replication)

REPORT=suite_report.md
echo "# Suite report" > "$REPORT"
echo "" >> "$REPORT"
echo "DB: \`$AFRAMES_CORPUS\` - run $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$REPORT"
echo "" >> "$REPORT"
echo "| test | verdict | seconds | validates |" >> "$REPORT"
echo "|---|---|---|---|" >> "$REPORT"

echo "=== Routine Overhead Ratio validation suite ==="
pass=0; fail=0
for t in "${OFFLINE[@]}"; do
  echo ""
  echo ">>> $t"
  if python3 "$t.py"; then :; else echo "    (script error)"; fi
  # read verdict from the results json
  v=$(python3 - "$t" <<'PY'
import json,sys,os
name=sys.argv[1]
p=os.path.join("results",name+".json")
try:
    d=json.load(open(p)); print(f"{d['verdict']}|{d['seconds']}|{d['validates']}")
except Exception as e:
    print(f"NO_OUTPUT|0|(script did not produce results/{name}.json)")
PY
)
  verdict="${v%%|*}"; rest="${v#*|}"; secs="${rest%%|*}"; val="${rest#*|}"
  echo "| $t | $verdict | $secs | $val |" >> "$REPORT"
  [ "$verdict" = "PASS" ] && pass=$((pass+1)) || fail=$((fail+1))
done

echo "" >> "$REPORT"
echo "**$pass PASS, $fail not-pass.** Per-test JSON in \`results/\`." >> "$REPORT"
echo "" >> "$REPORT"
echo "Next: the live billed money-demo - see README.md section 'Live'." >> "$REPORT"

echo ""
echo "=== done: $pass PASS, $fail not-pass -> $REPORT ==="
