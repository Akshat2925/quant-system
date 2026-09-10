"""Deterministic client-order-id generation.

Every order this system places carries a client order id that is
deterministic given (strategy_run_id, instrument, intent_sequence). This is
what makes "retry a possibly-failed placement" safe: if the first attempt
actually succeeded on the broker side, the retry reuses the same id and the
broker/reconciliation layer can recognize it as a duplicate instead of
double-placing.

This module has zero I/O and zero broker knowledge on purpose — it must be
usable identically from the live path and the backtest path.
"""

from __future__ import annotations

import hashlib
import uuid


def make_client_order_id(
    strategy_run_id: str,
    instrument_token: int,
    intent_sequence: int,
    *,
    namespace: uuid.UUID | None = None,
) -> str:
    """Build a deterministic, broker-safe client order id.

    Args:
        strategy_run_id: unique id for this strategy's live/backtest run
            (e.g. a UUID minted once when the engine starts).
        instrument_token: broker instrument token for the order's instrument.
        intent_sequence: monotonically increasing counter of order intents
            raised by the strategy for this instrument within this run.
        namespace: optional UUID namespace override, mainly for testing.

    Returns:
        A string safe to use as a Kite `tag`/client id: alphanumeric,
        deterministic, and collision-resistant across runs.
    """
    ns = namespace or uuid.NAMESPACE_OID
    seed = f"{strategy_run_id}:{instrument_token}:{intent_sequence}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
    generated = uuid.uuid5(ns, seed)
    # Kite tags are capped at 20 chars historically for some order types;
    # keep this short and broker-safe while still effectively unique.
    return f"qs{digest}"[:20], str(generated)


def short_client_order_id(strategy_run_id: str, instrument_token: int, intent_sequence: int) -> str:
    """Convenience wrapper returning just the short, broker-safe id."""
    short_id, _ = make_client_order_id(strategy_run_id, instrument_token, intent_sequence)
    return short_id
