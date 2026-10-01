"""
Static IP guard for live trading mode.

Angel One's algo-trading rules require orders to come from a registered
static IP. Data calls (LTP, holdings, funds) work from any IP; only
order placement is restricted.

This module:
  1. Fetches the current public IPv4 using two fallback services.
  2. Compares it against REGISTERED_STATIC_IP from config.
  3. Returns a clear result so bot.py can refuse to place orders
     (and fall back to alert_only) without crashing.

Security: IPs are partially masked in logs (first two octets visible,
last two replaced with *.*).
"""

from __future__ import annotations

import requests
from dataclasses import dataclass
from loguru import logger

# Two independent services — if one is down, try the other
_IP_SERVICES = [
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
]
_TIMEOUT = 5  # seconds per service


@dataclass
class IPCheckResult:
    current_ip:    str | None   # None if fetch failed
    registered_ip: str | None   # None if not configured
    match:         bool
    reason:        str


def _mask_ip(ip: str) -> str:
    """Partially mask an IP for safe logging: 1.2.*.* """
    if not ip:
        return "(unknown)"
    parts = ip.strip().split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.*.*"
    return "?.?.*.*"


def get_public_ip() -> str | None:
    """Fetch current public IPv4. Tries two services, returns None if both fail."""
    for url in _IP_SERVICES:
        try:
            resp = requests.get(url, timeout=_TIMEOUT)
            if resp.status_code == 200:
                ip = resp.text.strip()
                if ip:
                    return ip
        except Exception as e:
            logger.debug(f"IP service {url} failed: {type(e).__name__}")
    logger.warning("⚠️ Could not fetch public IP from any service")
    return None


def check_ip(registered_ip: str) -> IPCheckResult:
    """
    Check current IP against the registered static IP.

    Args:
        registered_ip: the IP from config (REGISTERED_STATIC_IP).
                       Empty string means "not configured".

    Returns:
        IPCheckResult with match=True only when current == registered.
        Any failure (fetch error, not configured, mismatch) → match=False.
    """
    if not registered_ip:
        return IPCheckResult(
            current_ip    = None,
            registered_ip = None,
            match         = False,
            reason        = (
                "REGISTERED_STATIC_IP not set in .env — "
                "cannot verify IP for live orders. "
                "Set it to your static IP or use MODE=alert_only."
            ),
        )

    current = get_public_ip()

    if current is None:
        return IPCheckResult(
            current_ip    = None,
            registered_ip = registered_ip,
            match         = False,
            reason        = (
                "Could not determine current public IP "
                "(both IP lookup services failed). "
                "Refusing live orders this session."
            ),
        )

    current_clean    = current.strip()
    registered_clean = registered_ip.strip()

    if current_clean == registered_clean:
        logger.info(
            f"✅ IP check passed: current={_mask_ip(current_clean)} "
            f"matches registered={_mask_ip(registered_clean)}"
        )
        return IPCheckResult(
            current_ip    = current_clean,
            registered_ip = registered_clean,
            match         = True,
            reason        = "IP matches registered static IP",
        )
    else:
        logger.warning(
            f"⚠️ IP mismatch: current={_mask_ip(current_clean)}, "
            f"registered={_mask_ip(registered_clean)}"
        )
        return IPCheckResult(
            current_ip    = current_clean,
            registered_ip = registered_clean,
            match         = False,
            reason        = (
                f"Current IP ({_mask_ip(current_clean)}) does not match "
                f"registered IP ({_mask_ip(registered_clean)}). "
                "Angel One may reject orders from this IP. "
                "Falling back to alert_only for this session."
            ),
        )


# Angel One order rejection patterns that indicate an IP/compliance issue
_IP_REJECTION_PHRASES = (
    "ip not registered",
    "ip address",
    "not whitelisted",
    "algo trading",
    "static ip",
    "unauthorized ip",
    "invalid source",
)


def is_ip_rejection(error_message: str) -> bool:
    """Return True if an Angel One order error looks like an IP/compliance rejection."""
    msg = error_message.lower()
    return any(phrase in msg for phrase in _IP_REJECTION_PHRASES)
