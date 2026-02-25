#!/bin/bash
# Weekly meal planner runner — called by launchd every Monday at 6:00 AM
# Logs to ~/Library/Logs/meal-planner.log

set -euo pipefail

REPO="/Users/nathanfitzgerald/.claude-worktrees/performance-meal-planner/goofy-chaum"
PYTHON="/opt/anaconda3/bin/python"
LOG="$HOME/Library/Logs/meal-planner.log"

echo "" >> "$LOG"
echo "======================================" >> "$LOG"
echo "Run started: $(date)" >> "$LOG"
echo "======================================" >> "$LOG"

cd "$REPO"

# Load .env into environment — strip inline comments and blank lines
if [ -f "$REPO/.env" ]; then
    while IFS= read -r line; do
        # Skip blank lines and comment-only lines
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Strip inline comments, then export
        line="${line%%#*}"
        line="${line%"${line##*[![:space:]]}"}"  # trim trailing whitespace
        [[ -z "$line" ]] && continue
        export "$line"
    done < "$REPO/.env"
fi

# Run pipeline with live recipes and email send
"$PYTHON" src/run_weekly.py --demo --send >> "$LOG" 2>&1

echo "Run finished: $(date)" >> "$LOG"
