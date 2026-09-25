#!/usr/bin/env bash
# Starts the bot in dry-run mode (no real orders). Run from the project root.
set -e
cd "$(dirname "$0")"
echo "=========================================="
echo "  ETF TRADING BOT — DRY RUN"
echo "  (no real orders will be placed)"
echo "=========================================="
python3 bot.py --dry-run
