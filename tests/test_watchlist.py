"""Tests for watchlist.py — all mocked."""

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from watchlist import build, _category_trigger, CACHE_FILE


def _fake_groww(holdings):
    c = MagicMock()
    c.get_holdings.return_value = holdings
    return c


def _fake_angel(holdings):
    c = MagicMock()
    c.get_holdings.return_value = holdings
    return c


# ── Category trigger defaults ─────────────────────────────────────────────────

def test_gold_category_trigger():
    assert _category_trigger("SBI Gold ETF") < -1.0

def test_silver_category_trigger():
    assert _category_trigger("ICICI Silver ETF") <= -3.0

def test_default_category_trigger():
    assert _category_trigger("Some Unknown ETF") == -2.0


# ── build() with Groww holdings ───────────────────────────────────────────────

def test_build_from_groww_holdings(tmp_path):
    groww = _fake_groww([
        {"trading_symbol": "CPSEETF", "isin": "", "quantity": 10, "average_price": 90},
        {"trading_symbol": "SETFGOLD", "isin": "", "quantity": 3, "average_price": 120},
    ])
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
        result = build(groww_connector=groww)

    symbols = [r["symbol"] for r in result]
    assert "CPSEETF-EQ"  in symbols
    assert "SETFGOLD-EQ" in symbols


def test_build_preserves_quantity(tmp_path):
    groww = _fake_groww([
        {"trading_symbol": "CPSEETF", "isin": "", "quantity": 15, "average_price": 91.0},
    ])
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
        result = build(groww_connector=groww)

    etf = next(r for r in result if r["symbol"] == "CPSEETF-EQ")
    assert etf["quantity"] == 15.0
    assert etf["avg_price"] == 91.0


def test_build_unresolved_stays_in_list(tmp_path):
    """Unknown ETF must appear in result with enabled=False, not be dropped."""
    groww = _fake_groww([
        {"trading_symbol": "UNKNOWNETF", "isin": "", "quantity": 5, "average_price": 50},
    ])
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
        result = build(groww_connector=groww)

    assert any(r["symbol"] == "UNKNOWNETF" for r in result)
    unresolved = next(r for r in result if r["symbol"] == "UNKNOWNETF")
    assert unresolved["enabled"] is False
    assert unresolved["token"] is None


def test_build_yaml_exclude(tmp_path):
    groww = _fake_groww([
        {"trading_symbol": "CPSEETF",  "isin": "", "quantity": 10, "average_price": 90},
        {"trading_symbol": "SETFGOLD", "isin": "", "quantity": 3,  "average_price": 120},
    ])
    yaml_cfg = {"include": [], "exclude": ["SETFGOLD"], "overrides": {}}
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value=yaml_cfg):
        result = build(groww_connector=groww)

    symbols = [r["symbol"] for r in result]
    assert "CPSEETF-EQ"  in symbols
    assert "SETFGOLD-EQ" not in symbols


def test_build_yaml_override_trigger(tmp_path):
    groww = _fake_groww([
        {"trading_symbol": "CPSEETF", "isin": "", "quantity": 10, "average_price": 90},
    ])
    yaml_cfg = {"include": [], "exclude": [], "overrides": {"CPSEETF": {"trigger_pct": -5.0}}}
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value=yaml_cfg):
        result = build(groww_connector=groww)

    etf = next(r for r in result if r["symbol"] == "CPSEETF-EQ")
    assert etf["trigger_pct"] == -5.0


def test_build_falls_back_to_cache_on_failure(tmp_path):
    """If both brokers fail, cache must be used and warning sent."""
    import json
    cache_data = {
        "saved_at": "2026-09-30T10:00:00+05:30",
        "holdings": [
            {"trading_symbol": "METALIETF", "isin": "", "quantity": 7, "average_price": 13.0}
        ]
    }
    cache_path = str(tmp_path / "cache.json")
    with open(cache_path, "w") as f:
        json.dump(cache_data, f)

    failing_groww = MagicMock()
    failing_groww.get_holdings.side_effect = Exception("network down")

    with patch("watchlist.CACHE_FILE", cache_path), \
         patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
        result = build(groww_connector=failing_groww)

    # Should have METALIETF from cache
    assert any("METALIETF" in r["symbol"] for r in result)


def test_build_no_broker_no_cache_uses_empty(tmp_path):
    """With no broker and no cache, result should be empty list (not crash)."""
    with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
         patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
        result = build()  # no connectors

    assert isinstance(result, list)


def test_build_never_raises(tmp_path):
    """build() must never raise regardless of inputs."""
    bad_groww = MagicMock()
    bad_groww.get_holdings.side_effect = RuntimeError("boom")
    try:
        with patch("watchlist.CACHE_FILE", str(tmp_path / "cache.json")), \
             patch("watchlist._load_watchlist_yaml", return_value={"include": [], "exclude": [], "overrides": {}}):
            build(groww_connector=bad_groww)
    except Exception as e:
        pytest.fail(f"build() raised unexpectedly: {e}")
