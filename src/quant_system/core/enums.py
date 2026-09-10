"""Enumerations shared across core, execution, broker, and backtest modules.

Keeping these in one place avoids the classic failure mode where the live
adapter and the backtest adapter each grow their own slightly-different
status vocabulary and reconciliation silently breaks.
"""

from __future__ import annotations

from enum import Enum


class Exchange(str, Enum):
    MCX = "MCX"
    NSE = "NSE"
    NFO = "NFO"  # NSE F&O segment


class ContractType(str, Enum):
    FUTURE = "FUTURE"
    OPTION_CE = "OPTION_CE"
    OPTION_PE = "OPTION_PE"
    EQUITY = "EQUITY"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"  # stop-loss limit
    SL_M = "SL_M"  # stop-loss market


class OrderStatus(str, Enum):
    """Order lifecycle. Every transition must be explicit and logged.

    PENDING          -> created locally, not yet sent to broker
    SUBMITTED        -> sent to broker, awaiting acknowledgement
    OPEN             -> acknowledged by broker, resting / working
    PARTIALLY_FILLED -> some quantity filled, remainder still open
    FILLED           -> fully filled
    CANCEL_REQUESTED -> cancel sent, awaiting broker confirmation
    CANCELLED        -> confirmed cancelled by broker
    REJECTED         -> broker rejected the order
    UNKNOWN          -> reconciliation could not determine true state
                        (must block new orders on this instrument until resolved)
    """

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    OPEN = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


TERMINAL_ORDER_STATUSES = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
)

OPEN_ORDER_STATUSES = frozenset(
    {
        OrderStatus.PENDING,
        OrderStatus.SUBMITTED,
        OrderStatus.OPEN,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_REQUESTED,
    }
)


class RegimeState(str, Enum):
    """Macro regime classification. Extend as the regime engine's scoring
    model grows, but keep this list the single source of truth — strategy
    override tables key off these values."""

    RISK_ON = "RISK_ON"
    RISK_OFF = "RISK_OFF"
    NEUTRAL = "NEUTRAL"
    HIGH_VOL = "HIGH_VOL"
    CIRCUIT_HALT = "CIRCUIT_HALT"
