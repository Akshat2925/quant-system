"""
Centralized configuration loader with validation.

Why this exists: the old version read os.getenv() scattered across files
with no checks. A blank or malformed value in .env would not fail until
the bot hit that code path mid-trading-day — often silently. This module
loads everything once, validates it, and raises a clear error at startup
if anything is missing or nonsensical.
"""

import os
import sys
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val or not val.strip():
        raise ConfigError(f"Missing required .env value: {name}")
    return val.strip()


def _require_float(name: str, min_value: float = None, max_value: float = None) -> float:
    raw = _require(name)
    try:
        val = float(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid number")
    if min_value is not None and val < min_value:
        raise ConfigError(f"{name}={val} is below minimum allowed ({min_value})")
    if max_value is not None and val > max_value:
        raise ConfigError(f"{name}={val} is above maximum allowed ({max_value})")
    return val


@dataclass(frozen=True)
class Config:
    # Broker credentials
    angel_api_key: str
    angel_client_id: str
    angel_pin: str
    angel_totp_secret: str

    # Trading parameters
    capital: float
    daily_loss_limit: float
    max_premium_pct: float
    monthly_budget: float

    # Alerts (optional)
    telegram_token: str
    telegram_chat_id: str


def load_config() -> Config:
    """Load and validate config. Raises ConfigError with a clear message on failure."""
    try:
        cfg = Config(
            angel_api_key=_require("ANGEL_API_KEY"),
            angel_client_id=_require("ANGEL_CLIENT_ID"),
            angel_pin=_require("ANGEL_PIN"),
            angel_totp_secret=_require("ANGEL_TOTP_SECRET"),
            capital=_require_float("CAPITAL", min_value=1),
            daily_loss_limit=_require_float("DAILY_LOSS_LIMIT", min_value=0),
            max_premium_pct=_require_float("MAX_PREMIUM_PCT", min_value=0, max_value=100),
            monthly_budget=_require_float("MONTHLY_BUDGET", min_value=1) if os.getenv("MONTHLY_BUDGET") else 1500.0,
            telegram_token=os.getenv("TELEGRAM_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        )
    except ConfigError as e:
        print(f"\n❌ Configuration error: {e}", file=sys.stderr)
        print("   Check your .env file against .env.example\n", file=sys.stderr)
        raise
    return cfg


if __name__ == "__main__":
    # Quick standalone check: `python config.py`
    try:
        c = load_config()
        print("✅ Config loaded successfully.")
        print(f"   Client ID:        {c.angel_client_id}")
        print(f"   Capital:          ₹{c.capital:,.2f}")
        print(f"   Daily loss limit: ₹{c.daily_loss_limit:,.2f}")
        print(f"   Telegram alerts:  {'enabled' if c.telegram_token else 'disabled'}")
    except ConfigError:
        sys.exit(1)
