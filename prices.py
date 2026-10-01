"""
Price provider — Stage 6.

Primary source : Angel One ltpData  → (ltp, day_open, close, high, low)
Secondary source: Groww live quote  → only if Groww plan supports live data
                                       (detected once at startup, cached)

Rules:
- Primary always tried first.
- Secondary used only if detected as available at startup.
- If both fail → NO DATA (never fabricate or estimate a price).
- Cross-check: if both available and |ltp_diff| > PRICE_MISMATCH_PCT → warn once.
- Staleness guard: price older than PRICE_STALE_SECONDS → mark stale, no trigger.
- Light throttle: one call per symbol per cycle (no hammering).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

PRICE_STALE_SECONDS  = 120   # 2 minutes
PRICE_MISMATCH_PCT   = 0.5   # warn if primary vs secondary differ by more than this %
_MISMATCH_DEDUPE_TTL = 300   # only warn about mismatch once per 5 min per symbol


@dataclass
class PriceResult:
    symbol:     str
    ltp:        float | None
    day_open:   float | None
    source:     str            # "angel" | "groww" | "none"
    fetched_at: float          # time.monotonic()
    stale:      bool = False
    reason:     str  = ""

    @property
    def available(self) -> bool:
        return self.ltp is not None and self.day_open is not None

    @property
    def age_seconds(self) -> float:
        return time.monotonic() - self.fetched_at


class PriceProvider:
    """
    Fetches and caches prices for a set of symbols.

    Usage:
        pp = PriceProvider(angel_connector, groww_connector)
        pp.detect_groww_live()   # once at startup
        result = pp.get(symbol, token, exchange)
    """

    def __init__(self, angel_connector, groww_connector=None,
                 notifier=None):
        self._angel   = angel_connector
        self._groww   = groww_connector
        self._notifier = notifier
        self._groww_live_supported = False
        self._cache: dict[str, PriceResult] = {}
        self._mismatch_warned: dict[str, float] = {}  # symbol → monotonic time

    # ── Groww live detection ──────────────────────────────────────────────

    def detect_groww_live(self, test_symbol: str = "NIFTY50") -> bool:
        """
        Check once at startup whether Groww plan supports live quote data.
        Returns True if supported; stores result internally.
        Never crashes.
        """
        if not self._groww:
            self._groww_live_supported = False
            return False
        try:
            # Try fetching a quote — if plan doesn't support it, API returns error
            result = self._groww_get_ltp(test_symbol)
            supported = result is not None
            self._groww_live_supported = supported
            if supported:
                logger.info("✅ Groww live data: supported")
            else:
                logger.info("ℹ️ Groww live data: not available on current plan — disabled")
            return supported
        except Exception as e:
            logger.info(f"ℹ️ Groww live data not available: {e}")
            self._groww_live_supported = False
            return False

    def _groww_get_ltp(self, symbol: str) -> float | None:
        """Attempt to get LTP from Groww. Returns None if unsupported."""
        try:
            # growwapi SDK: GrowwAPI(token).get_ltp or similar
            # We use a conservative approach — try the method, catch any error
            if hasattr(self._groww, "get_ltp"):
                val = self._groww.get_ltp(symbol)
                if val is not None:
                    return float(val)
            return None
        except Exception:
            return None

    # ── Core fetch ────────────────────────────────────────────────────────

    def get(self, symbol: str, token: str,
            exchange: str = "NSE") -> PriceResult:
        """
        Get price for one symbol.
        Returns a PriceResult — never raises.
        """
        now = time.monotonic()

        # Return cached if fresh
        cached = self._cache.get(symbol)
        if cached and cached.age_seconds < PRICE_STALE_SECONDS:
            return cached

        # ── Primary: Angel One ────────────────────────────────────────────
        ltp, day_open = None, None
        source = "none"
        try:
            ltp, day_open = self._angel.get_quote(exchange, symbol, token)
            if ltp and day_open:
                source = "angel"
        except Exception as e:
            logger.warning(f"⚠️ Angel price fetch failed for {symbol}: {e}")

        # ── Secondary: Groww (if supported and primary failed) ────────────
        if source == "none" and self._groww_live_supported:
            try:
                groww_ltp = self._groww_get_ltp(symbol.replace("-EQ", ""))
                if groww_ltp:
                    ltp    = groww_ltp
                    source = "groww"
                    # day_open not available from Groww — mark as None
                    day_open = None
                    logger.info(f"ℹ️ {symbol}: using Groww price as fallback")
            except Exception as e:
                logger.warning(f"⚠️ Groww price fallback failed for {symbol}: {e}")

        # ── Cross-check (if both available) ──────────────────────────────
        if source == "angel" and self._groww_live_supported and ltp:
            try:
                groww_ltp = self._groww_get_ltp(symbol.replace("-EQ", ""))
                if groww_ltp and ltp:
                    diff_pct = abs(ltp - groww_ltp) / ltp * 100
                    if diff_pct > PRICE_MISMATCH_PCT:
                        last_warned = self._mismatch_warned.get(symbol, 0)
                        if now - last_warned > _MISMATCH_DEDUPE_TTL:
                            self._mismatch_warned[symbol] = now
                            msg = (
                                f"⚠️ Price mismatch {symbol}: "
                                f"Angel Rs.{ltp:.2f} vs Groww Rs.{groww_ltp:.2f} "
                                f"({diff_pct:.2f}%) — trusting Angel (primary)"
                            )
                            logger.warning(msg)
                            if self._notifier:
                                try:
                                    self._notifier.notify_error("PRICE_MISMATCH", msg)
                                except Exception:
                                    pass
            except Exception:
                pass

        # ── Build result ──────────────────────────────────────────────────
        if ltp and day_open and source == "angel":
            result = PriceResult(
                symbol=symbol, ltp=ltp, day_open=day_open,
                source=source, fetched_at=now,
                stale=False, reason="ok",
            )
        elif ltp and source == "groww":
            result = PriceResult(
                symbol=symbol, ltp=ltp, day_open=None,
                source=source, fetched_at=now,
                stale=False,
                reason="Groww fallback — day_open unavailable",
            )
        else:
            result = PriceResult(
                symbol=symbol, ltp=None, day_open=None,
                source="none", fetched_at=now,
                stale=False,
                reason="price unavailable from all sources",
            )

        self._cache[symbol] = result
        return result

    def get_all(self, watchlist: list[dict]) -> dict[str, PriceResult]:
        """
        Fetch prices for all symbols in the watchlist.
        Returns {symbol: PriceResult}.
        """
        results = {}
        for item in watchlist:
            sym      = item.get("symbol", "")
            token    = item.get("token") or ""
            exchange = item.get("exchange", "NSE")
            if not sym or not token:
                results[sym] = PriceResult(
                    symbol=sym, ltp=None, day_open=None,
                    source="none", fetched_at=time.monotonic(),
                    stale=False, reason="no token",
                )
                continue
            results[sym] = self.get(sym, token, exchange)
        return results

    def mark_stale(self, symbol: str) -> None:
        """Mark a cached price as stale (e.g. after 2 min with no update)."""
        if symbol in self._cache:
            self._cache[symbol].stale = True

    def invalidate(self, symbol: str) -> None:
        """Remove a symbol from cache so next call fetches fresh."""
        self._cache.pop(symbol, None)
