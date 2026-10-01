"""
Angel One SmartAPI connector — hardened with retries and consistent error handling.
"""

import time
import pyotp
from loguru import logger
from SmartApi import SmartConnect

from config import Config

# Auth-related error substrings that indicate a session has expired.
_AUTH_ERRORS = ("invalid token", "not logged in", "session expired",
                "unauthorized", "token expired", "jwt")


class ConnectorError(Exception):
    """Raised on unrecoverable connector failures."""


def _with_retry(fn, *, retries=3, base_delay=1.5, what="request"):
    """Run fn() with exponential backoff retry on exception.
    Returns fn()'s result or None on total failure."""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            if attempt < retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(
                    f"⚠️ {what} failed (attempt {attempt}/{retries}): {e} "
                    f"— retrying in {delay:.1f}s"
                )
                time.sleep(delay)
            else:
                logger.error(f"❌ {what} failed after {retries} attempts: {e}")
    return None


def _is_auth_error(resp) -> bool:
    """Return True if an API response looks like a session/auth failure."""
    if resp is None:
        return False
    msg = str(resp.get("message", "")).lower()
    return any(kw in msg for kw in _AUTH_ERRORS)


class AngelOneConnector:
    def __init__(self, config: Config):
        self.config          = config
        self.smart           = SmartConnect(api_key=config.angel_api_key)
        self.refresh_token   = None
        self._logged_in      = False
        self._consec_failures = 0   # consecutive get_quote / get_ltp failures
        self._MAX_FAIL_BEFORE_RELOGIN = 3

    # ── Auth ────────────────────────────────────────────────────────────

    def login(self) -> bool:
        def attempt():
            totp = pyotp.TOTP(self.config.angel_totp_secret).now()
            return self.smart.generateSession(
                self.config.angel_client_id, self.config.angel_pin, totp
            )

        data = _with_retry(attempt, what="login")
        if data and data.get("status"):
            self.refresh_token    = data["data"]["refreshToken"]
            self._logged_in       = True
            self._consec_failures = 0
            logger.success(f"✅ Login successful — {self.config.angel_client_id}")
            return True

        msg = data.get("message") if data else "no response after retries"
        logger.error(f"❌ Login failed: {msg}")
        return False

    def _try_relogin(self) -> bool:
        """Re-login once on suspected session expiry. Returns True on success."""
        logger.warning("⚠️ Session may have expired — attempting re-login")
        ok = self.login()
        if ok:
            logger.success("✅ Re-login successful — resuming")
        else:
            logger.error("❌ Re-login failed — subsequent calls will also fail")
        return ok

    def is_logged_in(self) -> bool:
        return self._logged_in

    # ── Market data ─────────────────────────────────────────────────────

    def get_quote(self, exchange: str, symbol: str, token: str):
        """Return (ltp, day_open) using ltpData().

        ltpData() returns both the last-traded price and the day's official
        open price, so there is no need to record the price at bot startup.
        Returns (None, None) if either value is missing, zero, or the call fails.
        """
        def attempt():
            return self.smart.ltpData(exchange, symbol, token)

        data = _with_retry(attempt, retries=2, what=f"get_quote({symbol})")

        # Handle auth error → re-login once then retry
        if _is_auth_error(data):
            if self._try_relogin():
                data = _with_retry(attempt, retries=2, what=f"get_quote({symbol}) post-relogin")

        if data and data.get("status"):
            ltp  = float(data["data"].get("ltp")  or 0)
            open_ = float(data["data"].get("open") or 0)
            if ltp > 0 and open_ > 0:
                self._consec_failures = 0
                return ltp, open_
            logger.warning(
                f"⚠️ get_quote({symbol}): ltp={ltp} open={open_} — "
                "one or both are zero, skipping this symbol this cycle"
            )
            return None, None

        # Count consecutive failures — re-login after threshold
        self._consec_failures += 1
        if self._consec_failures >= self._MAX_FAIL_BEFORE_RELOGIN:
            logger.warning(
                f"⚠️ {self._consec_failures} consecutive quote failures — "
                "forcing re-login attempt"
            )
            self._try_relogin()
            self._consec_failures = 0
        return None, None

    def get_ltp(self, exchange: str, symbol: str, token: str):
        """Return LTP only (used by tranche_engine at order time)."""
        ltp, _ = self.get_quote(exchange, symbol, token)
        return ltp

    def get_funds(self):
        """Return available funds as float, or None on failure.

        Returns None (not 0.0) so callers can distinguish 'zero funds'
        from 'could not reach the API'.
        """
        def attempt():
            return self.smart.rmsLimit()

        data = _with_retry(attempt, what="get_funds")
        if _is_auth_error(data):
            if self._try_relogin():
                data = _with_retry(attempt, what="get_funds post-relogin")
        if data and data.get("status"):
            val = data["data"].get("net")
            if val is not None:
                return float(val)
        logger.warning("⚠️ get_funds returned no data — returning None")
        return None

    # ── Orders ───────────────────────────────────────────────────────────

    def place_buy_order(self, symbol, token, quantity, price=None, exchange="NSE"):
        """Place a buy order.

        NEVER retried automatically — retrying an order placement risks a
        duplicate buy if the first attempt succeeded but the response was lost.
        """
        try:
            params = {
                "variety":         "NORMAL",
                "tradingsymbol":   symbol,
                "symboltoken":     token,
                "transactiontype": "BUY",
                "exchange":        exchange,
                "ordertype":       "MARKET" if not price else "LIMIT",
                "producttype":     "DELIVERY",
                "duration":        "DAY",
                "quantity":        str(quantity),
            }
            if price:
                params["price"] = str(round(price, 2))
            resp = self.smart.placeOrder(params)
            if resp and resp.get("status"):
                oid = resp["data"]["orderid"]
                logger.success(f"✅ BUY {symbol} x{quantity} | Order: {oid}")
                return oid
            logger.error(f"❌ Order failed: {resp.get('message') if resp else 'no response'}")
        except Exception as e:
            logger.error(f"❌ Order error placing {symbol} x{quantity}: {e}")
        return None

    def get_order_status(self, order_id: str) -> str | None:
        """Return the status string for an order ('complete', 'rejected', etc.)
        or None if the call fails."""
        def attempt():
            return self.smart.orderBook()

        data = _with_retry(attempt, what=f"orderBook(for {order_id})")
        if data and data.get("status") and data.get("data"):
            for order in data["data"]:
                if str(order.get("orderid")) == str(order_id):
                    return str(order.get("orderstatus", "")).lower()
        return None

    def cancel_order(self, order_id: str, variety: str = "NORMAL") -> bool:
        """Cancel an open order. Returns True if accepted."""
        try:
            resp = self.smart.cancelOrder(variety, order_id)
            if resp and resp.get("status"):
                logger.info(f"🚫 Order {order_id} cancelled")
                return True
            logger.warning(f"⚠️ Cancel order {order_id} failed: {resp}")
        except Exception as e:
            logger.warning(f"⚠️ Cancel order error: {e}")
        return False

    def get_holdings(self):
        def attempt():
            return self.smart.holding()

        data = _with_retry(attempt, what="get_holdings")
        if data and data.get("status"):
            return data["data"]
        return []

    def logout(self):
        try:
            if self._logged_in:
                self.smart.terminateSession(self.config.angel_client_id)
                logger.info("👋 Logged out")
        except Exception as e:
            logger.warning(f"⚠️ Logout error (non-fatal): {e}")
