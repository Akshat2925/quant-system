"""GDFL (Global Datafeeds) tick feed adapter.

GDFL provides MCX and NSE tick data via a TCP socket protocol.
This adapter normalises GDFL's binary/text protocol into standard `Tick` objects.

GDFL is commonly used for MCX commodity data in Indian algo-trading setups
because of its low latency and reliable MCX coverage.

Protocol: TCP socket, CSV-delimited messages.
Authentication: username + password in connection handshake.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from quant_system.data.base import Tick, TickFeed

logger = logging.getLogger(__name__)

# GDFL field positions in CSV tick message
_F_SYMBOL = 0
_F_LTP = 1
_F_LTQ = 2
_F_VOL = 3
_F_BID = 4
_F_ASK = 5
_F_OI = 6
_F_TIMESTAMP = 7


class GDFLFeed(TickFeed):
    """GDFL real-time tick feed via TCP socket.

    Args:
        host    : GDFL server host
        port    : GDFL server port (default 18002)
        username: GDFL account username
        password: GDFL account password
        symbol_token_map: dict mapping GDFL symbol strings to instrument tokens
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 18002,
        symbol_token_map: dict[str, int] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._symbol_map = symbol_token_map or {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=10000)

    async def connect(self) -> None:
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        # Send auth handshake
        auth = f"{self._username}|{self._password}\r\n"
        self._writer.write(auth.encode())
        await self._writer.drain()
        response = await self._reader.readline()
        if b"OK" not in response.upper():
            raise RuntimeError(f"GDFL authentication failed: {response.decode()}")
        asyncio.create_task(self._receive_loop())
        logger.info("gdfl_connected", extra={"host": self._host, "port": self._port})

    async def disconnect(self) -> None:
        if self._writer:
            self._writer.close()
            await self._writer.wait_closed()
        logger.info("gdfl_disconnected")

    async def subscribe(self, instrument_tokens: list[int]) -> None:
        """Subscribe to symbols. Reverse-maps tokens to GDFL symbol strings."""
        token_to_symbol = {v: k for k, v in self._symbol_map.items()}
        symbols = [token_to_symbol[t] for t in instrument_tokens if t in token_to_symbol]
        if self._writer and symbols:
            msg = "SUBSCRIBE|" + ",".join(symbols) + "\r\n"
            self._writer.write(msg.encode())
            await self._writer.drain()
            logger.info("gdfl_subscribed", extra={"symbols": symbols})

    async def stream(self) -> AsyncIterator[Tick]:
        while True:
            tick = await self._queue.get()
            yield tick

    # ------------------------------------------------------------------ #

    async def _receive_loop(self) -> None:
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    logger.warning("gdfl_connection_closed")
                    break
                try:
                    tick = self._parse_line(line.decode().strip())
                    if tick is not None:
                        try:
                            self._queue.put_nowait(tick)
                        except asyncio.QueueFull:
                            logger.warning("gdfl_queue_full_dropping_tick")
                except Exception as exc:
                    logger.warning("gdfl_parse_error", extra={"error": str(exc)})
        except Exception as exc:
            logger.error("gdfl_receive_loop_error", extra={"error": str(exc)})

    def _parse_line(self, line: str) -> Tick | None:
        """Parse a GDFL CSV tick line into a normalised Tick."""
        if not line or line.startswith("#"):
            return None
        parts = line.split(",")
        if len(parts) < 6:
            return None

        symbol = parts[_F_SYMBOL].strip()
        token = self._symbol_map.get(symbol)
        if token is None:
            return None

        try:
            ts_str = parts[_F_TIMESTAMP].strip() if len(parts) > _F_TIMESTAMP else ""
            try:
                ts = datetime.strptime(ts_str, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except ValueError:
                ts = datetime.now(timezone.utc)

            return Tick(
                instrument_token=token,
                timestamp=ts,
                last_price=Decimal(parts[_F_LTP].strip()),
                last_quantity=int(float(parts[_F_LTQ].strip())),
                volume=int(float(parts[_F_VOL].strip())),
                bid=Decimal(parts[_F_BID].strip()) if parts[_F_BID].strip() else None,
                ask=Decimal(parts[_F_ASK].strip()) if parts[_F_ASK].strip() else None,
                open_interest=int(float(parts[_F_OI].strip())) if len(parts) > _F_OI and parts[_F_OI].strip() else None,
                source="gdfl",
            )
        except (ValueError, IndexError):
            return None
