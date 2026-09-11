"""TrueData tick feed adapter.

TrueData is a popular Indian tick data vendor used for MCX and NSE.
This adapter normalises TrueData's WebSocket feed into the system's
standard `Tick` objects.

TrueData WebSocket API reference:
    https://truedata.in/api-documentation/

Authentication: username + password → session token (daily refresh).
WebSocket endpoint: wss://api.truedata.in/

Note: This adapter requires a TrueData subscription. For backtesting,
use the `HistoricalDataFeed` which reads from local Parquet/CSV files.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from quant_system.data.base import Tick, TickFeed

logger = logging.getLogger(__name__)


class TrueDataFeed(TickFeed):
    """TrueData real-time tick feed.

    Args:
        username: TrueData account username
        password: TrueData account password
        url     : WebSocket URL (default: TrueData live endpoint)
    """

    _WS_URL = "wss://api.truedata.in/marketdata/ws"

    def __init__(self, username: str, password: str, url: str | None = None) -> None:
        self._username = username
        self._password = password
        self._url = url or self._WS_URL
        self._ws = None
        self._token: str | None = None
        self._subscribed: list[str] = []
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=10000)

    async def connect(self) -> None:
        """Authenticate and open WebSocket connection."""
        try:
            import websockets  # type: ignore[import]
        except ImportError:
            raise ImportError("websockets package required: pip install websockets")

        # Authenticate via REST to get session token
        self._token = await self._authenticate()
        self._ws = await websockets.connect(
            f"{self._url}?token={self._token}",
            ping_interval=20,
            ping_timeout=10,
        )
        asyncio.create_task(self._receive_loop())
        logger.info("truedata_connected", extra={"url": self._url})

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("truedata_disconnected")

    async def subscribe(self, instrument_tokens: list[int]) -> None:
        """Subscribe to symbols by TrueData symbol codes."""
        # TrueData uses symbol strings, not integer tokens
        # Map instrument_token -> TrueData symbol via contract master
        symbols = [str(t) for t in instrument_tokens]
        self._subscribed.extend(symbols)
        if self._ws:
            payload = json.dumps({"action": "subscribe", "symbols": symbols})
            await self._ws.send(payload)
            logger.info("truedata_subscribed", extra={"symbols": symbols})

    async def stream(self) -> AsyncIterator[Tick]:
        """Yield normalised ticks from the internal queue."""
        while True:
            tick = await self._queue.get()
            yield tick

    # ------------------------------------------------------------------ #

    async def _authenticate(self) -> str:
        """Get session token from TrueData REST API."""
        import urllib.request
        import urllib.parse

        data = urllib.parse.urlencode({
            "user_id": self._username,
            "password": self._password,
        }).encode()
        req = urllib.request.Request(
            "https://api.truedata.in/getSessionId",
            data=data,
            method="POST",
        )
        response = await asyncio.to_thread(urllib.request.urlopen, req, 10)
        result = json.loads(response.read())
        token = result.get("sessionid")
        if not token:
            raise RuntimeError(f"TrueData authentication failed: {result}")
        logger.info("truedata_authenticated")
        return token

    async def _receive_loop(self) -> None:
        """Background loop: receive raw WebSocket messages, normalise, queue."""
        try:
            async for raw_msg in self._ws:
                try:
                    tick = self._parse_tick(raw_msg)
                    if tick is not None:
                        try:
                            self._queue.put_nowait(tick)
                        except asyncio.QueueFull:
                            logger.warning("truedata_queue_full_dropping_tick")
                except Exception as exc:
                    logger.warning("truedata_parse_error", extra={"error": str(exc)})
        except Exception as exc:
            logger.error("truedata_receive_loop_error", extra={"error": str(exc)})

    def _parse_tick(self, raw: str) -> Tick | None:
        """Normalise a TrueData WebSocket message into a Tick."""
        try:
            data = json.loads(raw)
            if data.get("type") != "tick":
                return None

            return Tick(
                instrument_token=int(data["token"]),
                timestamp=datetime.fromtimestamp(data["timestamp"], tz=timezone.utc),
                last_price=Decimal(str(data["ltp"])),
                last_quantity=int(data.get("ltq", 0)),
                volume=int(data.get("vol", 0)),
                bid=Decimal(str(data["bid"])) if data.get("bid") else None,
                ask=Decimal(str(data["ask"])) if data.get("ask") else None,
                open_interest=int(data["oi"]) if data.get("oi") else None,
                source="truedata",
            )
        except (KeyError, ValueError):
            return None
