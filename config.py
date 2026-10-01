"""
Centralized configuration loader with validation.

Loads everything from .env once at startup, validates it, and raises a
clear ConfigError if anything is missing or nonsensical — preventing
silent mid-day failures.
"""

import os
import sys
from dataclasses import dataclass, field
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


def _optional_float(name: str, default: float,
                    min_value: float = None, max_value: float = None) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a valid number")
    if min_value is not None and val < min_value:
        raise ConfigError(f"{name}={val} is below minimum allowed ({min_value})")
    if max_value is not None and val > max_value:
        raise ConfigError(f"{name}={val} is above maximum allowed ({max_value})")
    return val


# NSE holidays for the current year.
# Format: "YYYY-MM-DD".  Update this list each January.
NSE_HOLIDAYS: frozenset = frozenset({
    "2026-01-26",  # Republic Day
    "2026-02-19",  # Chhatrapati Shivaji Maharaj Jayanti
    "2026-03-25",  # Holi
    "2026-04-02",  # Ram Navami (tentative)
    "2026-04-14",  # Dr. Ambedkar Jayanti
    "2026-04-15",  # Good Friday (tentative)
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-10-02",  # Gandhi Jayanti
    "2026-10-22",  # Dussehra (tentative)
    "2026-11-01",  # Diwali Laxmi Puja (tentative)
    "2026-11-04",  # Diwali Balipratipada (tentative)
    "2026-11-25",  # Guru Nanak Jayanti (tentative)
    "2026-12-25",  # Christmas
})


@dataclass(frozen=True)
class Config:
    # ── Broker credentials ───────────────────────────────────────────────
    angel_api_key:    str
    angel_client_id:  str
    angel_pin:        str
    angel_totp_secret: str

    # ── Core budget & risk ───────────────────────────────────────────────
    monthly_budget:   float   # total budget per calendar month (₹)
    daily_cap_pct:    float   # max fraction of monthly_budget spendable in one day
    daily_loss_limit: float   # skip buying if today's spend would exceed this (₹)

    # ── NAV check mode ───────────────────────────────────────────────────
    # "strict"   — block buys beyond per-ETF max_premium threshold
    # "advisory" — log premium but only hard-block above 2%
    nav_check_mode: str

    # ── Bot run mode ─────────────────────────────────────────────────────
    # "alert_only" (DEFAULT): compute signals, send Telegram BUY SIGNAL, NO orders
    # "dry_run"             : simulate full order flow, fake IDs, separate state files
    # "live"                : real orders; requires --confirm-live or typed YES at startup
    mode: str

    # ── Alerts (optional) ────────────────────────────────────────────────
    telegram_token:     str
    telegram_chat_id:   str
    telegram_chat_id_2: str

    # ── Static IP for live order verification (optional) ─────────────────
    # Set to your registered static IP. Leave empty to skip IP check.
    registered_static_ip: str = ""

    # ── Holiday set (injected from module-level NSE_HOLIDAYS) ────────────
    nse_holidays: frozenset = field(default_factory=frozenset)


def load_config() -> Config:
    """Load and validate config. Raises ConfigError with a clear message on failure."""
    try:
        mode_env = os.getenv("MODE", "alert_only").strip().lower()
        if mode_env not in ("alert_only", "dry_run", "live"):
            raise ConfigError("MODE must be 'alert_only', 'dry_run', or 'live'")

        nav_mode = os.getenv("NAV_CHECK_MODE", "advisory").strip().lower()
        if nav_mode not in ("strict", "advisory"):
            raise ConfigError("NAV_CHECK_MODE must be 'strict' or 'advisory'")

        cfg = Config(
            angel_api_key    =_require("ANGEL_API_KEY"),
            angel_client_id  =_require("ANGEL_CLIENT_ID"),
            angel_pin        =_require("ANGEL_PIN"),
            angel_totp_secret=_require("ANGEL_TOTP_SECRET"),
            monthly_budget   =_optional_float("MONTHLY_BUDGET", 1500.0, min_value=1),
            daily_cap_pct    =_optional_float("DAILY_CAP_PCT",  0.33,
                                              min_value=0.05, max_value=1.0),
            daily_loss_limit =_optional_float("DAILY_LOSS_LIMIT", 5000.0, min_value=0),
            nav_check_mode   =nav_mode,
            mode             =mode_env,
            telegram_token        =os.getenv("TELEGRAM_TOKEN",         "").strip(),
            telegram_chat_id      =os.getenv("TELEGRAM_CHAT_ID",       "").strip(),
            telegram_chat_id_2    =os.getenv("TELEGRAM_CHAT_ID_2",     "").strip(),
            registered_static_ip  =os.getenv("REGISTERED_STATIC_IP",   "").strip(),
            nse_holidays      =NSE_HOLIDAYS,
        )
    except ConfigError as e:
        print(f"\n❌ Configuration error: {e}", file=sys.stderr)
        print("   Check your .env file against .env.example\n", file=sys.stderr)
        raise
    return cfg


if __name__ == "__main__":
    try:
        c = load_config()
        print("✅ Config loaded successfully.")
        print(f"   Client ID:        {c.angel_client_id}")
        print(f"   Monthly budget:   ₹{c.monthly_budget:,.2f}")
        print(f"   Daily cap:        {c.daily_cap_pct*100:.0f}% of monthly budget")
        print(f"   Daily loss limit: ₹{c.daily_loss_limit:,.2f}")
        print(f"   NAV check mode:   {c.nav_check_mode}")
        print(f"   Mode:             {c.mode}")
        print(f"   Telegram alerts:  {'enabled' if c.telegram_token else 'disabled'}")
    except ConfigError:
        sys.exit(1)
