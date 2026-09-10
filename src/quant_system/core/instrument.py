"""Instrument (contract master) model.

This is deliberately exchange-agnostic in shape but carries the fields that
MCX and NSE F&O both require: lot size, tick size, expiry, and price
quotation basis (MCX quotes some contracts per unit that isn't the trading
lot unit — e.g. gold in Rs/10g while lot size is in grams/kg — so we keep
that distinction explicit instead of assuming lot size and quotation unit
are the same thing).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from quant_system.core.enums import ContractType, Exchange


class Instrument(BaseModel):
    model_config = {"frozen": True}

    tradingsymbol: str
    exchange: Exchange
    contract_type: ContractType
    instrument_token: int  # broker-assigned unique id (Kite instrument_token)

    lot_size: int = Field(gt=0)
    tick_size: Decimal = Field(gt=0)

    # The unit the price is quoted in, if different from "per lot".
    # e.g. "per 10 grams" for MCX gold, "per kg" for MCX silver mini, etc.
    # Free text is intentional: exchange conventions vary too much to enum.
    price_quotation_basis: str = "per_unit"

    expiry: date | None = None
    underlying: str | None = None
    strike: Decimal | None = None

    @field_validator("tradingsymbol")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    def round_to_tick(self, price: Decimal) -> Decimal:
        """Round an arbitrary price to the nearest valid tick for this
        instrument. Rounds half-up, since that's the exchange convention
        for tick rounding in most order-entry validation."""
        ticks = (price / self.tick_size).to_integral_value(rounding="ROUND_HALF_UP")
        return ticks * self.tick_size

    def is_expired(self, as_of: date) -> bool:
        return self.expiry is not None and as_of > self.expiry

    def days_to_expiry(self, as_of: date) -> int | None:
        if self.expiry is None:
            return None
        return (self.expiry - as_of).days
