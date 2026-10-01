"""
Tests for groww_connector.py — all mocked, no real network calls.
"""

import pytest
from unittest.mock import MagicMock, patch
from groww_connector import GrowwConnector, GrowwConnectorError


def _make_conn():
    return GrowwConnector(api_key="fake_key", api_secret="fake_secret")


def _ok_token_response():
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"accessToken": "test_token_abc123"}
    return r


def _ok_holdings_response(holdings):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = holdings
    return r


# ── Auth ──────────────────────────────────────────────────────────────────────

def test_login_success():
    conn = _make_conn()
    with patch("groww_connector.requests.post", return_value=_ok_token_response()):
        assert conn.login() is True
    assert conn._access_token == "test_token_abc123"


def test_login_failure_returns_false():
    conn = _make_conn()
    bad = MagicMock()
    bad.status_code = 401
    bad.text = "Unauthorized"
    with patch("groww_connector.requests.post", return_value=bad):
        assert conn.login() is False
    assert conn._access_token is None


def test_missing_credentials_raises():
    with pytest.raises(GrowwConnectorError):
        GrowwConnector(api_key="", api_secret="secret")
    with pytest.raises(GrowwConnectorError):
        GrowwConnector(api_key="key", api_secret="")


def test_token_auto_refresh_on_401():
    """On 401 from holdings endpoint, connector must re-auth and retry."""
    conn = _make_conn()
    conn._access_token = "old_token"
    conn._token_fetched_at = 9999999999  # far future — not stale

    unauth = MagicMock()
    unauth.status_code = 401
    unauth.text = "Unauthorized"

    ok_holdings = _ok_holdings_response([{
        "isin": "INE002A01018", "trading_symbol": "CPSEETF",
        "quantity": 10, "average_price": 90,
    }])

    call_count = {"n": 0}
    def mock_get(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return unauth
        return ok_holdings

    with patch("groww_connector.requests.post", return_value=_ok_token_response()), \
         patch("groww_connector.requests.get", side_effect=mock_get):
        holdings = conn.get_holdings()

    assert len(holdings) == 1
    assert holdings[0]["trading_symbol"] == "CPSEETF"


# ── Holdings ──────────────────────────────────────────────────────────────────

def test_get_holdings_returns_list():
    conn = _make_conn()
    conn._access_token = "tok"
    conn._token_fetched_at = 9999999999

    data = [
        {"isin": "INE001A01036", "trading_symbol": "SETFGOLD",
         "quantity": 5, "average_price": 120},
        {"isin": "INE002A01018", "trading_symbol": "CPSEETF",
         "quantity": 20, "average_price": 88},
    ]
    with patch("groww_connector.requests.get",
               return_value=_ok_holdings_response(data)):
        holdings = conn.get_holdings()

    assert len(holdings) == 2
    assert holdings[0]["trading_symbol"] == "SETFGOLD"
    assert holdings[0]["quantity"] == 5.0
    assert holdings[0]["source"] == "groww"


def test_get_holdings_returns_empty_on_failure():
    """Network error must return [] and never raise."""
    conn = _make_conn()
    conn._access_token = "tok"
    conn._token_fetched_at = 9999999999

    with patch("groww_connector.requests.get",
               side_effect=Exception("network down")):
        holdings = conn.get_holdings()

    assert holdings == []


def test_get_holdings_warns_on_missing_fields(caplog):
    """Missing expected fields should warn, not crash."""
    conn = _make_conn()
    conn._access_token = "tok"
    conn._token_fetched_at = 9999999999

    # Missing average_price
    data = [{"isin": "X", "trading_symbol": "ETF-A", "quantity": 3}]
    with patch("groww_connector.requests.get",
               return_value=_ok_holdings_response(data)):
        holdings = conn.get_holdings()

    assert len(holdings) == 1
    assert holdings[0]["average_price"] == 0.0  # default


def test_get_holdings_returns_empty_if_no_token():
    """If auth fails entirely, get_holdings returns []."""
    conn = _make_conn()
    bad = MagicMock()
    bad.status_code = 401
    bad.text = "bad"
    with patch("groww_connector.requests.post", return_value=bad):
        holdings = conn.get_holdings()
    assert holdings == []


# ── Order stub ────────────────────────────────────────────────────────────────

def test_place_limit_buy_raises_not_implemented():
    conn = _make_conn()
    with pytest.raises(NotImplementedError):
        conn.place_limit_buy("CPSEETF", 10, 91.0)


def test_no_order_api_call_in_any_normal_flow():
    """Holdings fetch must never call an order endpoint."""
    conn = _make_conn()
    conn._access_token = "tok"
    conn._token_fetched_at = 9999999999

    with patch("groww_connector.requests.get",
               return_value=_ok_holdings_response([])) as mock_get:
        conn.get_holdings()

    # Verify URL called is holdings endpoint, not orders
    call_url = mock_get.call_args[0][0]
    assert "holdings" in call_url
    assert "order" not in call_url.lower()
