"""Alert manager — sends important notifications only.

Alerts sent when:
1. ETF buy executed
2. ETF moves > threshold (big move)
3. Monthly budget exhausted
4. Sell opportunity detected
5. ETF near 52-week low (rare buy opportunity)
6. Bot starts up / crashes / exits unexpectedly (NEW — the original had no
   way to tell you the bot process itself had died; it only alerted on
   trading events, so a crashed bot fails silently)

Setup Telegram (optional but recommended):
1. Open Telegram
2. Search @BotFather
3. /newbot → get token
4. Search @userinfobot → get your chat_id
5. Add to .env:
   TELEGRAM_TOKEN=your_token
   TELEGRAM_CHAT_ID=your_chat_id
"""

import requests
from loguru import logger


class AlertManager:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token   = token
        self.chat_id = chat_id
        self.enabled = bool(token and chat_id)

        if self.enabled:
            logger.info("📱 Telegram alerts enabled")
        else:
            logger.info("📱 Telegram not configured — alerts will only show in logs")

    def send(self, message: str):
        """Send alert — Telegram if configured, always logs."""
        logger.info(f"🔔 ALERT: {message}")

        if not self.enabled:
            return

        try:
            url  = f"https://api.telegram.org/bot{self.token}/sendMessage"
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            resp = requests.post(url, data=data, timeout=5)
            if resp.status_code == 200:
                logger.info("📱 Telegram alert sent")
            else:
                logger.warning(f"Telegram failed: {resp.text}")
        except Exception as e:
            logger.warning(f"Alert error: {e}")

    def sell_suggestion(self, symbol, current_price, buy_price, units):
        gain_pct = ((current_price - buy_price) / buy_price) * 100
        gain_amt = (current_price - buy_price) * units
        msg = (
            f"💰 SELL SUGGESTION: {symbol}\n"
            f"📈 Gain: +{gain_pct:.1f}% (₹{gain_amt:.2f})\n"
            f"Buy price: ₹{buy_price:.2f} → Now: ₹{current_price:.2f}\n"
            f"Units: {units}\n"
            f"Consider selling {units // 2} units to book partial profit!"
        )
        self.send(msg)

    def low_opportunity(self, symbol, current_price, week52_low):
        diff_pct = ((current_price - week52_low) / week52_low) * 100
        msg = (
            f"🎯 RARE OPPORTUNITY: {symbol}\n"
            f"Near 52-week low!\n"
            f"Current: ₹{current_price:.2f}\n"
            f"52W Low: ₹{week52_low:.2f} ({diff_pct:+.1f}%)\n"
            f"Consider extra buying!"
        )
        self.send(msg)

    def bot_started(self, dry_run: bool):
        mode = "DRY RUN (no real orders)" if dry_run else "LIVE (real money)"
        self.send(f"🚀 Bot started — mode: {mode}")

    def bot_stopped(self, reason: str = "manual stop"):
        self.send(f"⛔ Bot stopped — {reason}")

    def bot_crashed(self, error: str):
        self.send(f"💥 Bot CRASHED — needs attention!\nError: {error}")
