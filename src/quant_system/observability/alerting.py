"""Alerting: phone the moment the engine deviates from expectation.

Supports:
- Telegram (simple HTTP POST, no SDK required)
- Generic webhook (Slack, PagerDuty, etc.)

Alert levels:
- INFO    : routine events (strategy started, daily summary)
- WARNING : unexpected but non-critical (reconnect attempt, slippage spike)
- CRITICAL: immediate action required (circuit breaker tripped, kill switch, large drawdown)

Design: alerts are fire-and-forget async tasks. A failure to deliver an
alert is logged but never raises — the engine's trading loop must not be
blocked by an alerting failure.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertManager:
    """Routes alerts to configured channels.

    Usage:
        am = AlertManager()
        am.add_telegram_channel(bot_token="...", chat_id="...")
        am.add_webhook_channel("https://hooks.slack.com/...")
        await am.alert(AlertLevel.CRITICAL, "Kill switch tripped: daily loss limit")
    """

    def __init__(self) -> None:
        self._channels: list[_AlertChannel] = []

    def add_telegram_channel(self, bot_token: str, chat_id: str) -> None:
        self._channels.append(_TelegramChannel(bot_token, chat_id))

    def add_webhook_channel(self, url: str, headers: dict[str, str] | None = None) -> None:
        self._channels.append(_WebhookChannel(url, headers or {}))

    async def alert(self, level: AlertLevel, message: str, context: dict | None = None) -> None:
        """Fire alerts to all channels. Non-blocking on individual channel failures."""
        full_message = f"[{level.value}] {message}"
        if context:
            context_str = " | ".join(f"{k}={v}" for k, v in context.items())
            full_message += f"\n{context_str}"

        logger.log(
            logging.CRITICAL if level == AlertLevel.CRITICAL else
            logging.WARNING if level == AlertLevel.WARNING else logging.INFO,
            "alert_dispatched",
            extra={"level": level.value, "message": message},
        )

        tasks = [asyncio.create_task(ch.send(full_message)) for ch in self._channels]
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(
                        "alert_channel_failed",
                        extra={"channel": i, "error": str(result)},
                    )

    def alert_sync(self, level: AlertLevel, message: str, context: dict | None = None) -> None:
        """Synchronous wrapper for use outside an asyncio event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self.alert(level, message, context))
            else:
                loop.run_until_complete(self.alert(level, message, context))
        except Exception as exc:
            logger.error("alert_sync_failed", extra={"error": str(exc)})


class _AlertChannel:
    async def send(self, message: str) -> None: ...


class _TelegramChannel(_AlertChannel):
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    async def send(self, message: str) -> None:
        import urllib.request
        import urllib.parse
        import json

        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        data = json.dumps({"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        await asyncio.to_thread(urllib.request.urlopen, req, 10)


class _WebhookChannel(_AlertChannel):
    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self._url = url
        self._headers = headers

    async def send(self, message: str) -> None:
        import urllib.request
        import json

        data = json.dumps({"text": message}).encode()
        req = urllib.request.Request(self._url, data=data, headers={
            "Content-Type": "application/json",
            **self._headers,
        })
        await asyncio.to_thread(urllib.request.urlopen, req, 10)
