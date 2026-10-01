"""
Instrument mapping layer — Groww holding -> Angel One instrument.

For each ETF held in Groww, resolves the exact Angel One symbol, token
and exchange needed to fetch a live price from ltpData().

Matching strategy (in order):
  1. Manual override from instrument_overrides.yaml (if present)
  2. ISIN match against nav_checker.ETF_LIST (if Groww provides ISIN)
  3. Symbol match — tries bare symbol and symbol + "-EQ"
  4. Unresolved — kept visible with status "unresolved" and reason

Resolved mappings are cached in instrument_map.json (atomic write).
Cache is refreshed weekly or when a new symbol is seen.

Every ETF is always kept in the output — nothing is silently dropped.
Unresolved or mismatched ETFs show "NO DATA (reason)" in all views.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

IST = ZoneInfo("Asia/Kolkata")

# ── File paths ────────────────────────────────────────────────────────────────
MAP_CACHE_FILE     = "instrument_map.json"
OVERRIDES_FILE     = "instrument_overrides.yaml"
OVERRIDES_EXAMPLE  = "instrument_overrides.example.yaml"
CACHE_REFRESH_DAYS = 7

# ── ETF_LIST from nav_checker (Angel symbols + tokens) ───────────────────────
from nav_checker import ETF_LIST

# Build reverse lookup: bare_symbol -> angel_symbol
# e.g. "CPSEETF" -> "CPSEETF-EQ"
_BARE_TO_ANGEL: dict[str, str] = {}
_ISIN_TO_ANGEL: dict[str, str] = {}  # populated if ETF_LIST ever gets ISINs

for _angel_sym, _info in ETF_LIST.items():
    bare = _angel_sym.replace("-EQ", "")
    _BARE_TO_ANGEL[bare.upper()] = _angel_sym
    _BARE_TO_ANGEL[_angel_sym.upper()] = _angel_sym  # exact match too


# ── Atomic write ──────────────────────────────────────────────────────────────

def _atomic_write(path: str, data: dict) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_imap_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ── Override loader ───────────────────────────────────────────────────────────

def _load_overrides() -> dict[str, dict]:
    """
    Load instrument_overrides.yaml if present.
    Format:
      CPSEETF:
        angel_symbol: CPSEETF-EQ
        token: "2328"
        exchange: NSE
    Returns {} if file not found or yaml not installed.
    """
    if not Path(OVERRIDES_FILE).exists():
        return {}
    try:
        import yaml  # optional dependency
        with open(OVERRIDES_FILE) as f:
            data = yaml.safe_load(f) or {}
        return data
    except ImportError:
        logger.debug("yaml not installed — skipping instrument_overrides.yaml")
        return {}
    except Exception as e:
        logger.warning(f"⚠️ Could not load {OVERRIDES_FILE}: {e}")
        return {}


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(MAP_CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _cache_is_fresh(cache: dict) -> bool:
    ts = cache.get("refreshed_at")
    if not ts:
        return False
    try:
        age = datetime.now(IST) - datetime.fromisoformat(ts)
        return age < timedelta(days=CACHE_REFRESH_DAYS)
    except Exception:
        return False


# ── Core resolver ─────────────────────────────────────────────────────────────

def resolve(trading_symbol: str, isin: str = "") -> dict:
    """
    Resolve a Groww trading_symbol (and optional ISIN) to an Angel instrument.

    Returns a dict:
      {
        "groww_symbol":  str,
        "angel_symbol":  str | None,
        "token":         str | None,
        "exchange":      str | None,
        "status":        "resolved" | "unresolved" | "override",
        "reason":        str,
        "last_checked":  str (ISO timestamp),
      }
    """
    sym_upper = trading_symbol.upper().strip()
    now_str   = datetime.now(IST).isoformat()

    # 1. Manual override
    overrides = _load_overrides()
    if sym_upper in overrides or trading_symbol in overrides:
        ov = overrides.get(sym_upper) or overrides.get(trading_symbol)
        logger.info(f"🔧 {trading_symbol}: using manual override → {ov}")
        return {
            "groww_symbol": trading_symbol,
            "angel_symbol": ov.get("angel_symbol"),
            "token":        str(ov.get("token", "")),
            "exchange":     ov.get("exchange", "NSE"),
            "status":       "override",
            "reason":       "manual override from instrument_overrides.yaml",
            "last_checked": now_str,
        }

    # 2. ISIN match (future: when ETF_LIST includes ISINs)
    if isin and isin in _ISIN_TO_ANGEL:
        angel_sym = _ISIN_TO_ANGEL[isin]
        info = ETF_LIST[angel_sym]
        logger.info(f"✅ {trading_symbol}: resolved via ISIN {isin} → {angel_sym}")
        return {
            "groww_symbol": trading_symbol,
            "angel_symbol": angel_sym,
            "token":        info["token"],
            "exchange":     info["exchange"],
            "status":       "resolved",
            "reason":       f"ISIN match: {isin}",
            "last_checked": now_str,
        }

    # 3. Symbol match (bare or with -EQ)
    angel_sym = _BARE_TO_ANGEL.get(sym_upper)
    if angel_sym:
        info = ETF_LIST[angel_sym]
        logger.info(f"✅ {trading_symbol}: resolved via symbol → {angel_sym}")
        return {
            "groww_symbol": trading_symbol,
            "angel_symbol": angel_sym,
            "token":        info["token"],
            "exchange":     info["exchange"],
            "status":       "resolved",
            "reason":       f"symbol match: {trading_symbol} → {angel_sym}",
            "last_checked": now_str,
        }

    # 4. Unresolved
    reason = (
        f"No match found for {trading_symbol!r} in ETF_LIST. "
        "Add a manual override in instrument_overrides.yaml if this is an ETF "
        "you want to track."
    )
    logger.warning(f"⚠️ {trading_symbol}: unresolved — {reason}")
    return {
        "groww_symbol": trading_symbol,
        "angel_symbol": None,
        "token":        None,
        "exchange":     None,
        "status":       "unresolved",
        "reason":       reason,
        "last_checked": now_str,
    }


# ── Batch resolver with cache ─────────────────────────────────────────────────

def resolve_holdings(holdings: list[dict],
                     notifier=None) -> list[dict]:
    """
    Resolve a list of Groww holdings to Angel instruments.

    holdings: output of GrowwConnector.get_holdings()
    notifier: optional Notifier instance for Telegram warnings

    Returns a list of dicts combining holding data + resolution result.
    Unresolved ETFs are included with status="unresolved" — never silently dropped.
    """
    cache    = _load_cache()
    mappings = cache.get("mappings", {})
    changed  = False
    results  = []

    # Track symbols that sent a warning today (dedupe)
    warned_today: set[str] = set(cache.get("warned_today", []))

    for h in holdings:
        sym  = h.get("trading_symbol", "")
        isin = h.get("isin", "")

        # Use cached result if fresh
        cached_entry = mappings.get(sym)
        if cached_entry and _cache_is_fresh(cache):
            resolution = cached_entry
        else:
            resolution = resolve(sym, isin)
            mappings[sym] = resolution
            changed = True

        # Warn once per day for unresolved ETFs
        if resolution["status"] == "unresolved" and sym not in warned_today:
            msg = (
                f"⚠️ Unresolved instrument: {sym}\n"
                f"{resolution['reason']}\n"
                f"Add it to instrument_overrides.yaml to track it."
            )
            logger.warning(msg)
            if notifier:
                try:
                    notifier.notify_error("UNRESOLVED_INSTRUMENT", msg)
                except Exception:
                    pass
            warned_today.add(sym)

        results.append({**h, **resolution})

    if changed:
        _atomic_write(MAP_CACHE_FILE, {
            "refreshed_at": datetime.now(IST).isoformat(),
            "mappings":     mappings,
            "warned_today": list(warned_today),
        })

    return results


# ── Example overrides file ────────────────────────────────────────────────────

def write_example_overrides() -> None:
    """Write instrument_overrides.example.yaml if it doesn't exist."""
    if Path(OVERRIDES_EXAMPLE).exists():
        return
    content = """# instrument_overrides.example.yaml
# Use this to manually pin a Groww trading symbol to an Angel One instrument.
# Copy to instrument_overrides.yaml (which is gitignored).
#
# Format:
#   GROWW_SYMBOL:
#     angel_symbol: ANGEL_SYMBOL-EQ
#     token: "TOKEN_FROM_MASTER"
#     exchange: NSE

# Example (replace with real values):
# ETF-A:
#   angel_symbol: ETF-A-EQ
#   token: "12345"
#   exchange: NSE
"""
    with open(OVERRIDES_EXAMPLE, "w") as f:
        f.write(content)
    logger.info(f"📄 Created {OVERRIDES_EXAMPLE}")
