#!/usr/bin/env bash
# Starts the bot in LIVE mode — places REAL orders with REAL money.
set -e
cd "$(dirname "$0")"
echo "=========================================="
echo "  ETF TRADING BOT — LIVE"
echo "  *** THIS WILL PLACE REAL ORDERS ***"
echo "=========================================="
read -p "Type YES to continue: " confirm
if [ "$confirm" != "YES" ]; then
    echo "Cancelled."
    exit 1
fi
python3 bot.py
