"""Tests for portfolio.py."""

from unittest.mock import MagicMock
import pytest
from portfolio import build, PortfolioItem, PortfolioSummary


def _price(ltp, open_=None):
    p = MagicMock()
    p.available = True
    p.ltp = ltp
    p.day_open = open_
    p.source = "angel"
    return p


def _no_price():
    p = MagicMock()
    p.available = False
    p.ltp = None
    p.day_open = None
    p.source = "none"
    return p


def _etf(symbol, qty, avg_price, source="groww"):
    return {
        "symbol": symbol, "full_name": symbol,
        "quantity": qty, "avg_price": avg_price,
        "source": source, "token": "1234", "exchange": "NSE",
    }


# ── Basic build ───────────────────────────────────────────────────────────────

def test_build_returns_summary():
    wl = [_etf("CPSEETF-EQ", 10, 90.0)]
    s  = build(wl)
    assert isinstance(s, PortfolioSummary)
    assert len(s.items) == 1


def test_invested_calculation():
    wl = [_etf("CPSEETF-EQ", 10, 91.5)]
    s  = build(wl)
    assert s.items[0].invested == 915.0
    assert s.total_invested == 915.0


def test_total_invested_sums_all():
    wl = [
        _etf("CPSEETF-EQ",  10, 90.0),
        _etf("SETFGOLD-EQ",  3, 120.0),
    ]
    s = build(wl)
    assert s.total_invested == 10*90 + 3*120


def test_zero_quantity_invested_is_zero():
    wl = [_etf("CPSEETF-EQ", 0, 90.0)]
    s  = build(wl)
    assert s.items[0].invested == 0.0


# ── P&L with prices ───────────────────────────────────────────────────────────

def test_pnl_positive():
    wl = [_etf("CPSEETF-EQ", 10, 90.0)]
    pr = {"CPSEETF-EQ": _price(100.0)}
    s  = build(wl, pr)
    item = s.items[0]
    assert item.current_value  == 1000.0
    assert item.unrealised_pnl == 100.0
    assert item.unrealised_pct == pytest.approx(100/900*100, rel=0.01)


def test_pnl_negative():
    wl = [_etf("CPSEETF-EQ", 10, 100.0)]
    pr = {"CPSEETF-EQ": _price(90.0)}
    s  = build(wl, pr)
    assert s.items[0].unrealised_pnl == -100.0


def test_no_price_gives_none_pnl():
    wl = [_etf("CPSEETF-EQ", 10, 90.0)]
    pr = {"CPSEETF-EQ": _no_price()}
    s  = build(wl, pr)
    assert s.items[0].current_value   is None
    assert s.items[0].unrealised_pnl  is None


def test_total_pnl_sums_items():
    wl = [
        _etf("CPSEETF-EQ",  10, 90.0),
        _etf("SETFGOLD-EQ",  5, 100.0),
    ]
    pr = {
        "CPSEETF-EQ":  _price(100.0),
        "SETFGOLD-EQ": _price(110.0),
    }
    s = build(wl, pr)
    expected_value = 10*100 + 5*110
    assert s.total_value == expected_value
    assert s.total_pnl   == expected_value - s.total_invested


# ── Weights ───────────────────────────────────────────────────────────────────

def test_weights_sum_to_100():
    wl = [
        _etf("CPSEETF-EQ",  10, 90.0),
        _etf("SETFGOLD-EQ",  5, 100.0),
    ]
    pr = {
        "CPSEETF-EQ":  _price(100.0),
        "SETFGOLD-EQ": _price(110.0),
    }
    s = build(wl, pr)
    total_weight = sum(i.weight_pct for i in s.items if i.weight_pct)
    assert total_weight == pytest.approx(100.0, rel=0.01)


def test_weight_none_when_no_price():
    wl = [_etf("CPSEETF-EQ", 10, 90.0)]
    s  = build(wl, {"CPSEETF-EQ": _no_price()})
    assert s.items[0].weight_pct is None


# ── to_dict ───────────────────────────────────────────────────────────────────

def test_to_dict_has_required_keys():
    wl = [_etf("CPSEETF-EQ", 10, 90.0)]
    s  = build(wl)
    d  = s.to_dict()
    assert "total_invested" in d
    assert "items" in d
    assert "symbol" in d["items"][0]


def test_build_never_raises():
    try:
        build([])
    except Exception as e:
        pytest.fail(f"build() raised: {e}")


def test_build_empty_watchlist():
    s = build([])
    assert s.total_invested == 0.0
    assert s.items == []
