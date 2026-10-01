"""
Alert manager — sends important notifications via Telegram and loguru.

Security note: the Telegram bot token is never logged or included in any
log message.  Only the masked form (first 8 chars + "…") appears in logs.

Setup:
  1. Open Telegram, search @BotFather, /newbot → get token
  2. Search @userinfobot → get your chat_id
  3. Add to .env:
       TELEGRAM_TOKEN=<token>
       TELEGRAM_CHAT_ID=<chat_id>
"""

import requests
from loguru import logger

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _mask_token(token: str) -> str:
    """Return a safe loggable version: first 8 chars + '…'"""
    if not token:
        return "(not set)"
    return token[:8] + "…"


class AlertManager:
    def __init__(self, token: str = "", chat_id: str = ""):
        self._token   = token        # never log this directly
        self.chat_id  = chat_id
        self.enabled  = bool(token and chat_id)

        if self.enabled:
            logger.info(
                f"📱 Telegram alerts enabled (token: {_mask_token(token)})"
            )
        else:
            logger.info("📱 Telegram not configured — alerts will only appear in logs")

    # ── Core send ─────────────────────────────────────────────────────────

    def send(self, message: str) -> None:
        """Send alert to Telegram (if configured) and always log it."""
        logger.info(f"🔔 ALERT: {message}")

        if not self.enabled:
            return

        try:
            url  = _TELEGRAM_API.format(token=self._token)
            data = {"chat_id": self.chat_id, "text": message, "parse_mode": "HTML"}
            resp = requests.post(url, data=data, timeout=5)
            if resp.status_code == 200:
                logger.debug("📱 Telegram alert delivered")
            else:
                # Log status and body but NOT the URL (contains token)
                logger.warning(
                    f"⚠️ Telegram delivery failed: HTTP {resp.status_code} — "
                    f"{resp.text[:120]}"
                )
        except Exception as e:
            # Log exception type/message but NOT the URL
            logger.warning(f"⚠️ Telegram send error: {type(e).__name__}: {e}")

    # ── Convenience methods ───────────────────────────────────────────────

    def order_rejected(self, symbol: str, order_id: str, reason: str = "") -> None:
        self.send(
            f"❌ Order REJECTED — {symbol}\n"
            f"Order ID: {order_id}\n"
            f"Reason: {reason or 'unknown'}\n"
            f"Budget NOT charged."
        )

    def order_unfilled(self, symbol: str, order_id: str,
                       status: str = "timeout") -> None:
        self.send(
            f"⚠️ Order UNFILLED — {symbol}\n"
            f"Order ID: {order_id} | Status: {status}\n"
            f"Budget NOT charged. Will retry next trigger."
        )

    def bot_started(self, dry_run: bool) -> None:
        mode = "🧪 DRY RUN (no real orders)" if dry_run else "💰 LIVE (real money)"
        self.send(f"🚀 Bot started — {mode}")

    def bot_stopped(self, reason: str = "manual stop") -> None:
        self.send(f"⛔ Bot stopped — {reason}")

    def bot_crashed(self, error: str) -> None:
        self.send(f"💥 Bot CRASHED — needs attention!\nError: {error}")

    def sell_suggestion(self, symbol: str, current_price: float,
                        buy_price: float, units: int) -> None:
        gain_pct = ((current_price - buy_price) / buy_price) * 100
        gain_amt = (current_price - buy_price) * units
        self.send(
            f"💰 SELL SUGGESTION: {symbol}\n"
            f"📈 Gain: +{gain_pct:.1f}% (₹{gain_amt:.2f})\n"
            f"Buy: ₹{buy_price:.2f} → Now: ₹{current_price:.2f}\n"
            f"Consider selling {units // 2} units for partial profit."
        )

    def low_opportunity(self, symbol: str, current_price: float,
                        week52_low: float) -> None:
        diff_pct = ((current_price - week52_low) / week52_low) * 100
        self.send(
            f"🎯 NEAR 52-WEEK LOW: {symbol}\n"
            f"Current: ₹{current_price:.2f} | 52W Low: ₹{week52_low:.2f} "
            f"({diff_pct:+.1f}%)"
        )
