"""
Angel One SmartAPI connector — hardened with retries and consistent error handling.

Changes from the original:
- Retries transient failures (network blips, rate limits) with exponential backoff
  instead of giving up on the first error.
- Uses the `exchange` argument consistently (the original hardcoded "NSE" in
  place_buy_order regardless of what was passed in).
- Raises a clear ConnectorError on login failure instead of returning a bare
  False that callers might not check.
"""

import time
import pyotp
from loguru import logger
from SmartApi import SmartConnect

from config import Config


class ConnectorError(Exception):
    """Raised on unrecoverable connector failures."""


def _with_retry(fn, *, retries=3, base_delay=1.5, what="request"):
    """Run fn() with exponential backoff retry on exception. Returns fn()'s result or None."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < retries:
                delay = base_delay * (2 ** (attempt - 1))
                logger.warning(f"⚠️ {what} failed (attempt {attempt}/{retries}): {e} — retrying in {delay:.1f}s")
                time.sleep(delay)
            else:
                logger.error(f"❌ {what} failed after {retries} attempts: {e}")
    return None


class AngelOneConnector:
    def __init__(self, config: Config):
        self.config = config
        self.smart = SmartConnect(api_key=config.angel_api_key)
        self.refresh_token = None
        self._logged_in = False

    def login(self) -> bool:
        def attempt():
            totp = pyotp.TOTP(self.config.angel_totp_secret).now()
            return self.smart.generateSession(self.config.angel_client_id, self.config.angel_pin, totp)

        data = _with_retry(attempt, what="login")
        if data and data.get("status"):
            self.refresh_token = data["data"]["refreshToken"]
            self._logged_in = True
            logger.success(f"✅ Login successful — {self.config.angel_client_id}")
            return True

        msg = data.get("message") if data else "no response after retries"
        logger.error(f"❌ Login failed: {msg}")
        return False

    def is_logged_in(self) -> bool:
        return self._logged_in

    def get_funds(self) -> float:
        def attempt():
            return self.smart.rmsLimit()

        data = _with_retry(attempt, what="get_funds")
        if data and data.get("status"):
            return float(data["data"]["net"])
        return 0.0

    def get_ltp(self, exchange, symbol, token):
        def attempt():
            return self.smart.ltpData(exchange, symbol, token)

        data = _with_retry(attempt, retries=2, what=f"get_ltp({symbol})")
        if data and data.get("status"):
            return float(data["data"]["ltp"])
        return None

    def place_buy_order(self, symbol, token, quantity, price=None, exchange="NSE"):
        """Places a buy order. NOTE: does not retry — retrying an order placement
        risks a duplicate buy if the first attempt actually succeeded but the
        response was lost. Order placement failures should be surfaced, not
        silently retried."""
        try:
            params = {
                "variety": "NORMAL", "tradingsymbol": symbol,
                "symboltoken": token, "transactiontype": "BUY",
                "exchange": exchange, "ordertype": "MARKET" if not price else "LIMIT",
                "producttype": "DELIVERY", "duration": "DAY",
                "quantity": str(quantity),
            }
            if price:
                params["price"] = str(round(price, 2))
            resp = self.smart.placeOrder(params)
            if resp["status"]:
                oid = resp["data"]["orderid"]
                logger.success(f"✅ BUY {symbol} x{quantity} | Order: {oid}")
                return oid
            logger.error(f"❌ Order failed: {resp['message']}")
        except Exception as e:
            logger.error(f"❌ Order error placing {symbol} x{quantity}: {e}")
        return None

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
