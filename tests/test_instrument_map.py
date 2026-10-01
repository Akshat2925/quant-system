"""Tests for instrument_map.py — no network, no broker."""

import json
import os
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

from instrument_map import resolve, resolve_holdings


# ── resolve() ─────────────────────────────────────────────────────────────────

def test_resolve_bare_symbol():
    """CPSEETF (bare, no -EQ) should resolve to CPSEETF-EQ."""
    r = resolve("CPSEETF")
    assert r["status"] == "resolved"
    assert r["angel_symbol"] == "CPSEETF-EQ"
    assert r["token"] == "2328"
    assert r["exchange"] == "NSE"


def test_resolve_symbol_with_eq():
    """CPSEETF-EQ (already correct format) should also resolve."""
    r = resolve("CPSEETF-EQ")
    assert r["status"] == "resolved"
    assert r["angel_symbol"] == "CPSEETF-EQ"


def test_resolve_case_insensitive():
    """Symbol matching must be case-insensitive."""
    r = resolve("cpseetf")
    assert r["status"] == "resolved"
    assert r["angel_symbol"] == "CPSEETF-EQ"


def test_resolve_all_five_etfs():
    """All 5 ETFs in ETF_LIST must resolve correctly."""
    expected = {
        "SILVERIETF": "SILVERIETF-EQ",
        "SETFGOLD":   "SETFGOLD-EQ",
        "MODEFENCE":  "MODEFENCE-EQ",
        "CPSEETF":    "CPSEETF-EQ",
        "METALIETF":  "METALIETF-EQ",
    }
    for bare, angel in expected.items():
        r = resolve(bare)
        assert r["status"] == "resolved", f"{bare} not resolved: {r['reason']}"
        assert r["angel_symbol"] == angel


def test_resolve_unknown_symbol():
    """Unknown symbol must return unresolved — not raise."""
    r = resolve("UNKNOWNETF123")
    assert r["status"] == "unresolved"
    assert r["angel_symbol"] is None
    assert r["token"] is None


def test_resolve_unknown_never_raises():
    """resolve() must never raise, even with garbage input."""
    for sym in ["", "   ", "!@#$", "A" * 100]:
        result = resolve(sym)
        assert "status" in result


def test_resolve_includes_groww_symbol():
    """Result must always include the original groww_symbol."""
    r = resolve("CPSEETF")
    assert r["groww_symbol"] == "CPSEETF"


def test_resolve_manual_override(tmp_path):
    """Manual override in instrument_overrides.yaml wins over auto-match."""
    override_data = {
        "MYETF": {
            "angel_symbol": "MYETF-EQ",
            "token": "99999",
            "exchange": "BSE",
        }
    }
    with patch("instrument_map._load_overrides", return_value=override_data):
        r = resolve("MYETF")
    assert r["status"] == "override"
    assert r["angel_symbol"] == "MYETF-EQ"
    assert r["token"] == "99999"
    assert r["exchange"] == "BSE"


# ── resolve_holdings() ────────────────────────────────────────────────────────

def test_resolve_holdings_returns_all(tmp_path):
    """Every holding must appear in output — none silently dropped."""
    holdings = [
        {"trading_symbol": "CPSEETF",     "isin": "", "quantity": 10, "average_price": 90},
        {"trading_symbol": "UNKNOWNETF",  "isin": "", "quantity": 5,  "average_price": 50},
    ]
    with patch("instrument_map.MAP_CACHE_FILE", str(tmp_path / "imap.json")):
        results = resolve_holdings(holdings)

    assert len(results) == 2
    syms = [r["groww_symbol"] for r in results]
    assert "CPSEETF"    in syms
    assert "UNKNOWNETF" in syms


def test_resolve_holdings_resolved_has_token(tmp_path):
    holdings = [{"trading_symbol": "SETFGOLD", "isin": "", "quantity": 3, "average_price": 120}]
    with patch("instrument_map.MAP_CACHE_FILE", str(tmp_path / "imap.json")):
        results = resolve_holdings(holdings)
    assert results[0]["token"] == "17272"
    assert results[0]["status"] == "resolved"


def test_resolve_holdings_unresolved_no_token(tmp_path):
    holdings = [{"trading_symbol": "FAKEETF", "isin": "", "quantity": 1, "average_price": 100}]
    with patch("instrument_map.MAP_CACHE_FILE", str(tmp_path / "imap.json")):
        results = resolve_holdings(holdings)
    assert results[0]["token"] is None
    assert results[0]["status"] == "unresolved"


def test_resolve_holdings_preserves_quantity(tmp_path):
    """Original holding fields must be preserved in output."""
    holdings = [{"trading_symbol": "CPSEETF", "isin": "", "quantity": 42, "average_price": 91.5}]
    with patch("instrument_map.MAP_CACHE_FILE", str(tmp_path / "imap.json")):
        results = resolve_holdings(holdings)
    assert results[0]["quantity"] == 42
    assert results[0]["average_price"] == 91.5
