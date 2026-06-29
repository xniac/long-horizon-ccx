#!/usr/bin/env bash
# Install the long-horizon module into a target project so `claude` loads it.
#
#   scripts/install.sh /path/to/target-project
#
# Copies the drop-in .claude/ config (hooks wiring + CLAUDE.md) and the
# fresh-context evaluator subagent into the target. The hooks call
# `python -m lhx.hooks.*`, so `pip install -e .` (this repo) must be importable
# in the environment `claude` runs in.
set -euo pipefail

TARGET="${1:?usage: install.sh /path/to/target-project}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

mkdir -p "$TARGET/.claude/agents"
cp "$HERE/claude_config/settings.json" "$TARGET/.claude/settings.json"
cp "$HERE/claude_config/CLAUDE.md"     "$TARGET/.claude/CLAUDE.md"
cp "$HERE/agents/evaluator.md"         "$TARGET/.claude/agents/evaluator.md"

echo "Installed long-horizon module into $TARGET/.claude/"
echo "Make sure 'pip install -e $HERE' is active in the environment claude uses."
echo "Toggle the module off (A/B OFF arm) with: export LHX_ENABLED=false"
