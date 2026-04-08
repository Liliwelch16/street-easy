#!/bin/bash
# Apartment Scout — Headless runner for cron
#
# Schedule with: crontab -e
#   0 9 * * * /home/user/street-easy/run.sh
#
# Requires:
#   - Claude Code CLI installed
#   - ANTHROPIC_API_KEY set (in ~/.bashrc or below)
#   - Gmail + Notion MCP servers configured in Claude Code settings

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$SCRIPT_DIR/logs"
LOG_FILE="$LOG_DIR/$(date +%Y-%m-%d_%H%M%S).log"

mkdir -p "$LOG_DIR"

cd "$SCRIPT_DIR"

echo "=== Apartment Scout: $(date) ===" >> "$LOG_FILE"

claude -p "check apartments" \
  --allowedTools "mcp__gmail__*,mcp__notion__*,Read" \
  --max-turns 10 \
  >> "$LOG_FILE" 2>&1

echo "=== Done: $(date) ===" >> "$LOG_FILE"
