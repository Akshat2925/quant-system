"""
Sync scheduler — Stage 8.

Manages periodic sync of holdings and funds from both brokers.
Runs inside the bot's main loop — never blocks the trading cycle.

Schedule:
  - At startup
  - Before market open (once)
  - Every GROWW_SYNC_MINUTES during market hours (default 30)
  - Every ANGEL_SYNC_MINUTES during market hours (default 30)

Failure behavior:
  - If a broker sync fails → use cache, label numbers "stale (age)"
  - ONE Telegram warning per day per broker on failure
  - No broker failure crashes the bot or blocks the trading loop
  - Cache is always updated after a successful sync
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

# Default sync intervals (minutes)
DEFAULT_GROWW_SYNC_MINUTES  = 30
DEFAULT_ANGEL_SYNC_MINUTES  = 30


class SyncScheduler:
    """
    Tracks when each broker was last synced and whether a sync is due.
    Executes syncs without blocking the trading loop.
    """

    def __init__(self,
                 groww_connector=None,
                 angel_connector=None,
                 notifier=None,
                 groww_sync_minutes: int = DEFAULT_GROWW_SYNC_MINUTES,
                 angel_sync_minutes: int = DEFAULT_ANGEL_SYNC_MINUTES):

        self._groww   = groww_connector
        self._angel   = angel_connector
        self._notifier = notifier

        self._groww_interval = groww_sync_minutes * 60   # seconds
        self._angel_interval = angel_sync_minutes * 60

        self._last_groww_sync: float = 0.0   # monotonic time
        self._last_angel_sync: float = 0.0

        self._groww_warned_today = False
        self._angel_warned_today = False
        self._last_warning_date  = ""

        # Latest synced data
        self.groww_holdings: list[dict] = []
        self.angel_holdings: list[dict] = []
        self.angel_funds:    float | None = None

    # ── Public API ────────────────────────────────────────────────────────

    def tick(self) -> None:
        """
        Call this once per bot cycle.
        Runs any overdue syncs. Never raises.
        """
        self._reset_daily_flags()
        now = time.monotonic()

        if self._groww and (now - self._last_groww_sync) >= self._groww_interval:
            self._sync_groww()

        if self._angel and (now - self._last_angel_sync) >= self._angel_interval:
            self._sync_angel()

    def sync_all_now(self) -> None:
        """Force an immediate sync of all brokers (e.g. at startup)."""
        if self._groww:
            self._sync_groww()
        if self._angel:
            self._sync_angel()

    def groww_age_str(self) -> str:
        return self._age_str(self._last_groww_sync)

    def angel_age_str(self) -> str:
        return self._age_str(self._last_angel_sync)

    # ── Internal syncs ────────────────────────────────────────────────────

    def _sync_groww(self) -> None:
        try:
            holdings = self._groww.get_holdings()
            if holdings is not None:
                self.groww_holdings   = holdings
                self._last_groww_sync = time.monotonic()
                self._groww_warned_today = False
                logger.info(f"✅ Groww sync: {len(holdings)} holdings")
            else:
                self._handle_groww_failure("get_holdings returned None")
        except Exception as e:
            self._handle_groww_failure(str(e))

    def _sync_angel(self) -> None:
        try:
            holdings = self._angel.get_holdings()
            funds    = self._angel.get_funds()
            self.angel_holdings   = holdings or []
            self.angel_funds      = funds
            self._last_angel_sync = time.monotonic()
            self._angel_warned_today = False
            logger.info(
                f"✅ Angel sync: {len(self.angel_holdings)} holdings, "
                f"funds: {'Rs.' + str(round(funds, 2)) if funds is not None else 'N/A'}"
            )
        except Exception as e:
            self._handle_angel_failure(str(e))

    def _handle_groww_failure(self, reason: str) -> None:
        age = self.groww_age_str()
        logger.warning(f"⚠️ Groww sync failed ({reason}) — using cache ({age} old)")
        if not self._groww_warned_today and self._notifier:
            try:
                self._notifier.notify_error(
                    "GROWW_SYNC_FAILED",
                    f"Groww sync failed: {reason}\nUsing cached holdings ({age} old)."
                )
            except Exception:
                pass
            self._groww_warned_today = True

    def _handle_angel_failure(self, reason: str) -> None:
        age = self.angel_age_str()
        logger.warning(f"⚠️ Angel sync failed ({reason}) — using cache ({age} old)")
        if not self._angel_warned_today and self._notifier:
            try:
                self._notifier.notify_error(
                    "ANGEL_SYNC_FAILED",
                    f"Angel sync failed: {reason}\nUsing cached data ({age} old)."
                )
            except Exception:
                pass
            self._angel_warned_today = True

    def _reset_daily_flags(self) -> None:
        today = datetime.now(IST).strftime("%Y-%m-%d")
        if today != self._last_warning_date:
            self._groww_warned_today = False
            self._angel_warned_today = False
            self._last_warning_date  = today

    @staticmethod
    def _age_str(last_sync: float) -> str:
        if last_sync == 0.0:
            return "never synced"
        age_secs = time.monotonic() - last_sync
        if age_secs < 60:
            return f"{int(age_secs)}s"
        if age_secs < 3600:
            return f"{int(age_secs/60)}min"
        return f"{age_secs/3600:.1f}h"
