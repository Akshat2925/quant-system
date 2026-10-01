"""
Groww Trade API — READ-ONLY connector.

Used only to fetch holdings (what ETFs you own and at what average price).
Never places orders. Order placement is a stub that raises NotImplementedError.

Auth: API Key + Secret flow with auto-refresh.
The access token expires daily at 6 AM IST. This connector detects expiry
and re-authenticates automatically using the stored key and secret.

Open questions before Groww order placement can be considered:
  - Static IP registration requirement (SEBI mandate, deadline March 2026)
  - LIMIT-only order requirement (same as Angel One)
  - Fill verification flow via Groww order status API
  - Whether Groww free plan rate limits allow polling during market hours

Dependencies:
  pip install growwapi
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

# Verified response fields from Groww SDK docs (portfolio endpoint):
# isin, trading_symbol, quantity, average_price, t1_quantity,
# demat_free_quantity, pledge_quantity, demat_locked_quantity
_EXPECTED_HOLDING_FIELDS = {
    "isin", "trading_symbol", "quantity", "average_price"
}

_TOKEN_ENDPOINT = "https://groww.in/v1/api/trade/login/v2/token/api/access"
_HOLDINGS_ENDPOINT = "https://groww.in/v1/api/trade/portfolio/v1/holdings"
_TIMEOUT = 10  # seconds


def _mask(token: str) -> str:
    if not token:
        return "(empty)"
    return token[:12] + "…"


class GrowwConnectorError(Exception):
    pass


class GrowwConnector:
    """
    Read-only Groww connector.

    Fetches holdings from the Groww demat account.
    Never calls any order endpoint.
    Auto-refreshes the access token when it expires (daily at 6 AM IST).
    """

    def __init__(self, api_key: str, api_secret: str):
        if not api_key or not api_secret:
            raise GrowwConnectorError(
                "GROWW_API_KEY and GROWW_API_SECRET are required"
            )
        self._api_key     = api_key
        self._api_secret  = api_secret
        self._access_token: str | None = None
        self._token_fetched_at: float  = 0.0

    # ── Auth ─────────────────────────────────────────────────────────────

    def _token_is_stale(self) -> bool:
        """Token expires daily at 6 AM IST. Returns True if we need a refresh."""
        if not self._access_token:
            return True
        now_ist = datetime.now(IST)
        # Refresh if token was fetched before today's 6 AM IST
        today_6am = now_ist.replace(hour=6, minute=0, second=0, microsecond=0)
        fetched_dt = datetime.fromtimestamp(self._token_fetched_at, tz=IST)
        return fetched_dt < today_6am

    def _refresh_token(self) -> bool:
        """Fetch a fresh access token using API key + secret."""
        logger.info("🔄 Groww: refreshing access token…")
        try:
            resp = requests.post(
                _TOKEN_ENDPOINT,
                json={
                    "apiKey":    self._api_key,
                    "apiSecret": self._api_secret,
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                token = data.get("accessToken") or data.get("access_token") or data.get("token")
                if token:
                    self._access_token   = token
                    self._token_fetched_at = time.monotonic()
                    logger.success(
                        f"✅ Groww token refreshed (token: {_mask(token)})"
                    )
                    return True
                logger.warning(
                    f"⚠️ Groww token response missing accessToken field: "
                    f"{list(data.keys())}"
                )
            else:
                logger.warning(
                    f"⚠️ Groww token refresh failed: HTTP {resp.status_code} "
                    f"— {resp.text[:120]}"
                )
        except Exception as e:
            safe = str(e).replace(self._api_secret, "***")
            logger.warning(f"⚠️ Groww token refresh error: {type(e).__name__}: {safe}")
        return False

    def _ensure_token(self) -> bool:
        """Ensure a valid token exists. Returns False if auth fails."""
        if self._token_is_stale():
            return self._refresh_token()
        return True

    def login(self) -> bool:
        """Authenticate and verify connectivity. Returns True on success."""
        return self._refresh_token()

    # ── Holdings ─────────────────────────────────────────────────────────

    def get_holdings(self) -> list[dict]:
        """
        Fetch all holdings from the Groww demat account.

        Returns a list of dicts with verified fields:
          isin           — ISIN code
          trading_symbol — e.g. "CPSEETF"
          quantity       — net quantity held
          average_price  — average buy price in rupees

        Returns [] on failure (never raises — bot must continue without Groww).
        """
        if not self._ensure_token():
            logger.warning("⚠️ Groww: cannot get holdings — token unavailable")
            return []

        try:
            resp = requests.get(
                _HOLDINGS_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self._access_token}",
                    "Content-Type":  "application/json",
                },
                timeout=_TIMEOUT,
            )

            if resp.status_code == 401:
                logger.warning("⚠️ Groww: 401 — token expired, re-authenticating…")
                if self._refresh_token():
                    return self.get_holdings()
                return []

            if resp.status_code != 200:
                logger.warning(
                    f"⚠️ Groww holdings failed: HTTP {resp.status_code} "
                    f"— {resp.text[:120]}"
                )
                return []

            data = resp.json()
            # Response may be a list directly or wrapped in a key
            holdings_raw = data if isinstance(data, list) else \
                           data.get("holdings") or data.get("data") or []

            holdings = []
            for h in holdings_raw:
                # Defensive: warn on missing expected fields
                missing = _EXPECTED_HOLDING_FIELDS - set(h.keys())
                if missing:
                    logger.warning(
                        f"⚠️ Groww holding missing fields {missing}: "
                        f"{list(h.keys())} — using .get() with defaults"
                    )
                holdings.append({
                    "isin":           h.get("isin", ""),
                    "trading_symbol": h.get("trading_symbol", ""),
                    "quantity":       float(h.get("quantity", 0)),
                    "average_price":  float(h.get("average_price", 0)),
                    "t1_quantity":    float(h.get("t1_quantity", 0)),
                    "demat_free_qty": float(h.get("demat_free_quantity", 0)),
                    "source":         "groww",
                })

            logger.success(f"✅ Groww: fetched {len(holdings)} holdings")
            return holdings

        except Exception as e:
            logger.warning(f"⚠️ Groww get_holdings error: {type(e).__name__}: {e}")
            return []

    # ── Order stub (NOT IMPLEMENTED) ─────────────────────────────────────

    def place_limit_buy(self, *args, **kwargs):
        """
        NOT IMPLEMENTED — Groww order placement is not enabled in this phase.

        Open questions before this can be added:
        1. Static IP must be registered with Groww (SEBI mandate, deadline March 2026)
        2. LIMIT-only orders required (no MARKET/IOC)
        3. Fill verification via Groww order status API needs testing
        4. Free plan rate limits during market hours need validation
        """
        raise NotImplementedError(
            "Groww order placement is not implemented. "
            "This bot uses Angel One for order execution. "
            "See groww_connector.py for open questions."
        )
