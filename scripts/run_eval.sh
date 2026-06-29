#!/usr/bin/env bash
# One-command A/B run → dashboard. Offline, deterministic (simulated backend).
#
#   scripts/run_eval.sh            # k=10, default task suite
#   scripts/run_eval.sh 20         # k=20
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
K="${1:-10}"

python3 scripts/seed_tasks.py >/dev/null
python3 -m lhxeval.cli validate
echo
python3 -m lhxeval.cli run -k "$K" --out runs/latest
echo
echo "Open the dashboard: runs/latest/dashboard.html"
