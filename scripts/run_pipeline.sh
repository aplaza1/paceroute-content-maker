#!/usr/bin/env bash
# scripts/run_pipeline.sh
# Runs the travel blog pipeline and appends output to logs/pipeline.log.
# Designed to be called by cron — uses absolute paths throughout.

set -uo pipefail
# Note: -e (exit on error) is intentionally omitted so the "Finished at" line
# always runs even when Python exits with a non-zero code.

REPO_DIR="/Users/plaza/repositories/make-money-projects/travel-blog-pipeline"
PYTHON="/Users/plaza/repositories/make-money-projects/travel-blog-pipeline/venv/bin/python3"
LOG_FILE="$REPO_DIR/logs/pipeline.log"

mkdir -p "$REPO_DIR/logs"

echo "========================================" >> "$LOG_FILE"
echo "[run_pipeline] Started at $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$LOG_FILE"
echo "========================================" >> "$LOG_FILE"

cd "$REPO_DIR"

# -u disables Python's output buffering so logs appear in real time
"$PYTHON" -u pipeline/run_pipeline.py >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "[run_pipeline] Finished at $(date '+%Y-%m-%d %H:%M:%S %Z') (exit code: $EXIT_CODE)" >> "$LOG_FILE"
echo "" >> "$LOG_FILE"

exit $EXIT_CODE
