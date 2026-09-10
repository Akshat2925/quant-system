from datetime import date
from decimal import Decimal

import pytest

from quant_system.core import ContractType, Exchange, Instrument


def make_gold_instrument(**overrides) -> Instrument:
    defaults = dict(
        tradingsymbol="goldm25decfut",
        exchange=Exchange.MCX,
        contract_type=ContractType.FUTURE,
        instrument_token=123456,
        lot_size=100,  # grams
        tick_size=Decimal("1"),
        price_quotation_basis="per_10_grams",
        expiry=date(2025, 12, 5),
        underlying="GOLD",
    )
    defaults.update(overrides)
    return Instrument(**defaults)


def test_tradingsymbol_is_normalized_uppercase():
    inst = make_gold_instrument()
    assert inst.tradingsymbol == "GOLDM25DECFUT"


def test_round_to_tick_rounds_half_up():
    inst = make_gold_instrument(tick_size=Decimal("5"))
    assert inst.round_to_tick(Decimal("102.4")) == Decimal("100")
    assert inst.round_to_tick(Decimal("102.5")) == Decimal("105")
    assert inst.round_to_tick(Decimal("107.5")) == Decimal("110")


def test_is_expired():
    inst = make_gold_instrument(expiry=date(2025, 12, 5))
    assert inst.is_expired(date(2025, 12, 6)) is True
    assert inst.is_expired(date(2025, 12, 5)) is False
    assert inst.is_expired(date(2025, 12, 4)) is False


def test_days_to_expiry():
    inst = make_gold_instrument(expiry=date(2025, 12, 5))
    assert inst.days_to_expiry(date(2025, 12, 1)) == 4


def test_no_expiry_returns_none():
    inst = make_gold_instrument(expiry=None)
    assert inst.is_expired(date(2030, 1, 1)) is False
    assert inst.days_to_expiry(date(2030, 1, 1)) is None


def test_lot_size_must_be_positive():
    with pytest.raises(Exception):
        make_gold_instrument(lot_size=0)


def test_instrument_is_frozen():
    inst = make_gold_instrument()
    with pytest.raises(Exception):
        inst.lot_size = 200
