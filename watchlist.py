"""
Dynamic watchlist builder — Stage 5.

Builds the final ETF watchlist at startup and refreshes it before market open.
Sources (merged in order, later sources override earlier for the same symbol):

  1. Groww live holdings  — what you actually own in Groww demat
  2. Angel holdings       — ETFs bought via Angel One API
  3. Holdings cache       — last successful sync (used when a broker is down)
  4. watchlist.yaml       — manual include/exclude/overrides

Merge logic:
  final = (Groww ∪ Angel ∪ cache_if_broker_failed ∪ yaml_include) - yaml_exclude
  yaml overrides win for trigger_pct, max_premium, enabled, notes

Each ETF always stays in the list — missing token/price/open shows "NO DATA (reason)".
One Telegram warning per day per symbol for missing data.

Output format (list of dicts):
  {
    "symbol":       str,   # Angel symbol e.g. "CPSEETF-EQ"
    "token":        str,
    "exchange":     str,
    "full_name":    str,
    "trigger_pct":  float,
    "max_premium":  float,
    "quantity":     float,   # total units held (Groww + Angel)
    "avg_price":    float,   # weighted average
    "source":       str,     # "groww" | "angel" | "cache" | "yaml" | "mixed"
    "enabled":      bool,
    "nav_url":      str,
    "is_silver":    bool,
    "fixed_amt":    float | None,
  }
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from loguru import logger

from nav_checker import ETF_LIST
from instrument_map import resolve_holdings

IST             = ZoneInfo("Asia/Kolkata")
CACHE_FILE      = "holdings_cache.json"
WATCHLIST_YAML  = "watchlist.yaml"
CACHE_MAX_DAYS  = 14   # warn loudly if cache older than this


# ── Default category triggers ─────────────────────────────────────────────────
# Applied when watchlist.yaml does not override trigger_pct for a symbol.
# Keys are substrings matched (case-insensitive) against the ETF full_name.
CATEGORY_DEFAULTS: dict[str, float] = {
    "gold":    -1.5,
    "silver":  -4.0,
    "metal":   -3.0,
    "defence": -3.0,
    "psu":     -3.0,
    "cpse":    -3.0,
    "bank":    -3.0,
    "it":      -3.0,
    "pharma":  -3.0,
    "default": -2.0,
}


def _category_trigger(full_name: str) -> float:
    name_lower = full_name.lower()
    for kw, trig in CATEGORY_DEFAULTS.items():
        if kw == "default":
            continue
        if kw in name_lower:
            return trig
    return CATEGORY_DEFAULTS["default"]


# ── Atomic write ──────────────────────────────────────────────────────────────

def _atomic_write(path: str, data: dict) -> None:
    dir_ = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".tmp_wl_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


# ── Cache ─────────────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_cache(holdings: list[dict]) -> None:
    _atomic_write(CACHE_FILE, {
        "saved_at": datetime.now(IST).isoformat(),
        "holdings": holdings,
    })


def _cache_age_days(cache: dict) -> float | None:
    ts = cache.get("saved_at")
    if not ts:
        return None
    try:
        age = datetime.now(IST) - datetime.fromisoformat(ts)
        return age.total_seconds() / 86400
    except Exception:
        return None


# ── YAML loader ───────────────────────────────────────────────────────────────

def _load_watchlist_yaml() -> dict:
    """
    Load watchlist.yaml (gitignored, user-managed).
    Returns {"include": [...], "exclude": [...], "overrides": {...}}
    """
    if not Path(WATCHLIST_YAML).exists():
        return {"include": [], "exclude": [], "overrides": {}}
    try:
        import yaml
        with open(WATCHLIST_YAML) as f:
            data = yaml.safe_load(f) or {}
        return {
            "include":   data.get("include", []),
            "exclude":   data.get("exclude", []),
            "overrides": data.get("overrides", {}),
        }
    except ImportError:
        logger.debug("yaml not installed — watchlist.yaml not loaded")
        return {"include": [], "exclude": [], "overrides": {}}
    except Exception as e:
        logger.warning(f"⚠️ Could not load {WATCHLIST_YAML}: {e}")
        return {"include": [], "exclude": [], "overrides": {}}


# ── ETF detection heuristic ───────────────────────────────────────────────────

_ETF_KEYWORDS = {"etf", "bees", "fund", "nifty", "index"}

def _looks_like_etf(symbol: str, full_name: str = "") -> bool:
    """Conservative heuristic: symbol or name contains ETF keywords."""
    combined = (symbol + " " + full_name).lower()
    return any(kw in combined for kw in _ETF_KEYWORDS)


# ── Core builder ─────────────────────────────────────────────────────────────

def build(groww_connector=None, angel_connector=None,
          notifier=None) -> list[dict]:
    """
    Build the final watchlist.

    groww_connector : GrowwConnector instance (or None)
    angel_connector : AngelOneConnector instance (or None)
    notifier        : Notifier instance for Telegram warnings (or None)

    Returns list of watchlist item dicts. Never raises — falls back to cache.
    """
    yaml_cfg  = _load_watchlist_yaml()
    exclude   = {s.upper() for s in yaml_cfg.get("exclude", [])}
    overrides = yaml_cfg.get("overrides", {})

    # ── 1. Collect Groww holdings ─────────────────────────────────────────
    groww_raw: list[dict] = []
    groww_ok = False
    if groww_connector:
        try:
            raw = groww_connector.get_holdings()
            if raw is not None:
                groww_raw = raw
                groww_ok  = True
                logger.info(f"✅ Groww: {len(groww_raw)} holdings fetched")
        except Exception as e:
            logger.warning(f"⚠️ Groww holdings failed: {e}")

    # ── 2. Collect Angel holdings ─────────────────────────────────────────
    angel_raw: list[dict] = []
    angel_ok = False
    if angel_connector:
        try:
            raw = angel_connector.get_holdings()
            if raw:
                angel_raw = raw
                angel_ok  = True
                logger.info(f"✅ Angel: {len(angel_raw)} holdings fetched")
        except Exception as e:
            logger.warning(f"⚠️ Angel holdings failed: {e}")

    # ── 3. Cache fallback ─────────────────────────────────────────────────
    cache = _load_cache()
    using_cache = False

    if not groww_ok and not angel_ok:
        cached = cache.get("holdings", [])
        age    = _cache_age_days(cache)
        if cached:
            age_str = f"{age:.1f} days" if age is not None else "unknown age"
            msg = (
                f"⚠️ Both broker syncs failed — using holdings cache ({age_str} old). "
                f"Watchlist may be stale."
            )
            logger.warning(msg)
            if notifier:
                try:
                    notifier.notify_error("HOLDINGS_SYNC_FAILED", msg)
                except Exception:
                    pass
            if age is not None and age > CACHE_MAX_DAYS:
                loud = f"❌ Holdings cache is {age:.0f} days old — very stale. Update manually."
                logger.error(loud)
                if notifier:
                    try:
                        notifier.notify_error("CACHE_VERY_STALE", loud)
                    except Exception:
                        pass
            groww_raw  = cached
            using_cache = True
        else:
            logger.warning("⚠️ No holdings from any source and no cache — using ETF_LIST only")

    # Save fresh cache
    if groww_ok or angel_ok:
        all_fresh = groww_raw + [
            {"trading_symbol": h.get("tradingsymbol", ""),
             "isin": h.get("isin", ""),
             "quantity": float(h.get("quantity", 0)),
             "average_price": float(h.get("averageprice", 0)),
             "source": "angel"}
            for h in angel_raw
        ]
        _save_cache(all_fresh)

    # ── 4. Resolve Groww symbols to Angel instruments ─────────────────────
    resolved_groww = resolve_holdings(groww_raw, notifier=notifier)

    # ── 5. Build merged symbol dict ───────────────────────────────────────
    # key = angel_symbol (e.g. "CPSEETF-EQ")
    merged: dict[str, dict] = {}

    # From Groww (resolved)
    for item in resolved_groww:
        angel_sym = item.get("angel_symbol")
        if not angel_sym:
            # Unresolved — include with NO DATA marker
            bare = item.get("groww_symbol", "UNKNOWN")
            merged[bare] = {
                "symbol":      bare,
                "token":       None,
                "exchange":    None,
                "full_name":   bare,
                "trigger_pct": _category_trigger(bare),
                "max_premium": 0.5,
                "quantity":    float(item.get("quantity", 0)),
                "avg_price":   float(item.get("average_price", 0)),
                "source":      "cache" if using_cache else "groww",
                "enabled":     False,
                "status":      "unresolved",
                "reason":      item.get("reason", "unresolved"),
                "nav_url":     "",
                "is_silver":   False,
                "fixed_amt":   None,
            }
            continue

        info = ETF_LIST.get(angel_sym, {})
        if angel_sym not in merged:
            merged[angel_sym] = {
                "symbol":      angel_sym,
                "token":       item.get("token") or info.get("token"),
                "exchange":    item.get("exchange") or info.get("exchange", "NSE"),
                "full_name":   info.get("full_name", angel_sym),
                "trigger_pct": info.get("trigger_pct", _category_trigger(info.get("full_name", ""))),
                "max_premium": info.get("max_premium", 0.5),
                "quantity":    float(item.get("quantity", 0)),
                "avg_price":   float(item.get("average_price", 0)),
                "source":      "cache" if using_cache else "groww",
                "enabled":     True,
                "status":      "resolved",
                "reason":      item.get("reason", ""),
                "nav_url":     info.get("nav_url", ""),
                "is_silver":   info.get("is_silver", False),
                "fixed_amt":   info.get("fixed_amt"),
            }
        else:
            # Merge quantities
            merged[angel_sym]["quantity"] += float(item.get("quantity", 0))

    # From Angel holdings
    for h in angel_raw:
        sym = str(h.get("tradingsymbol", "")).strip().upper()
        if not sym:
            continue
        # Try to match to ETF_LIST
        from instrument_map import _BARE_TO_ANGEL
        angel_sym = _BARE_TO_ANGEL.get(sym, sym)
        info = ETF_LIST.get(angel_sym, {})
        if not info:
            continue  # not an ETF we track
        qty   = float(h.get("quantity", 0))
        price = float(h.get("averageprice", 0))
        if angel_sym in merged:
            # Weighted average
            existing = merged[angel_sym]
            total_qty = existing["quantity"] + qty
            if total_qty > 0:
                existing["avg_price"] = (
                    existing["avg_price"] * existing["quantity"] + price * qty
                ) / total_qty
            existing["quantity"] = total_qty
            existing["source"]   = "mixed"
        else:
            merged[angel_sym] = {
                "symbol":      angel_sym,
                "token":       info.get("token"),
                "exchange":    info.get("exchange", "NSE"),
                "full_name":   info.get("full_name", angel_sym),
                "trigger_pct": info.get("trigger_pct", _category_trigger(info.get("full_name", ""))),
                "max_premium": info.get("max_premium", 0.5),
                "quantity":    qty,
                "avg_price":   price,
                "source":      "angel",
                "enabled":     True,
                "status":      "resolved",
                "reason":      "Angel holdings",
                "nav_url":     info.get("nav_url", ""),
                "is_silver":   info.get("is_silver", False),
                "fixed_amt":   info.get("fixed_amt"),
            }

    # ── 6. YAML include ───────────────────────────────────────────────────
    for entry in yaml_cfg.get("include", []):
        sym = entry.get("symbol", "").upper() if isinstance(entry, dict) else str(entry).upper()
        if not sym or sym in merged:
            continue
        from instrument_map import _BARE_TO_ANGEL
        angel_sym = _BARE_TO_ANGEL.get(sym, sym)
        info = ETF_LIST.get(angel_sym, {})
        trig = entry.get("trigger_pct") if isinstance(entry, dict) else None
        merged[angel_sym] = {
            "symbol":      angel_sym,
            "token":       info.get("token"),
            "exchange":    info.get("exchange", "NSE"),
            "full_name":   info.get("full_name", angel_sym),
            "trigger_pct": trig or info.get("trigger_pct", _category_trigger(info.get("full_name", ""))),
            "max_premium": info.get("max_premium", 0.5),
            "quantity":    0.0,
            "avg_price":   0.0,
            "source":      "yaml",
            "enabled":     True,
            "status":      "resolved" if info else "unresolved",
            "reason":      "yaml include",
            "nav_url":     info.get("nav_url", ""),
            "is_silver":   info.get("is_silver", False),
            "fixed_amt":   info.get("fixed_amt"),
        }

    # ── 7. Apply YAML overrides ───────────────────────────────────────────
    for sym, ov in overrides.items():
        key = sym.upper()
        from instrument_map import _BARE_TO_ANGEL
        angel_sym = _BARE_TO_ANGEL.get(key, key)
        if angel_sym in merged:
            item = merged[angel_sym]
            if "trigger_pct"  in ov: item["trigger_pct"]  = float(ov["trigger_pct"])
            if "max_premium"  in ov: item["max_premium"]  = float(ov["max_premium"])
            if "enabled"      in ov: item["enabled"]       = bool(ov["enabled"])
            if "notes"        in ov: item["notes"]         = str(ov["notes"])

    # ── 8. Remove excluded ────────────────────────────────────────────────
    for sym in list(merged.keys()):
        bare = sym.replace("-EQ", "").upper()
        if sym.upper() in exclude or bare in exclude:
            logger.info(f"🚫 Excluding {sym} (watchlist.yaml exclude)")
            del merged[sym]

    result = list(merged.values())
    logger.info(f"📋 Watchlist built: {len(result)} ETFs "
                f"({sum(1 for e in result if e['enabled'])} enabled, "
                f"{sum(1 for e in result if not e['enabled'])} disabled)")
    return result


# ── Example watchlist.yaml ────────────────────────────────────────────────────

WATCHLIST_EXAMPLE = "watchlist.example.yaml"

def write_example_watchlist() -> None:
    if Path(WATCHLIST_EXAMPLE).exists():
        return
    content = """# watchlist.example.yaml
# Copy to watchlist.yaml (gitignored).
#
# include: extra ETFs to watch even if not in holdings
# exclude: ETFs to never track
# overrides: per-symbol parameter overrides

include:
  - symbol: ETF-A        # bare symbol, resolved automatically
    trigger_pct: -2.0    # optional override

exclude:
  - ETF-B                # never track this symbol

overrides:
  ETF-A:
    trigger_pct: -2.5
    max_premium: 0.3
    enabled: true
    notes: "Added manually — not in Groww holdings"
"""
    with open(WATCHLIST_EXAMPLE, "w") as f:
        f.write(content)
    logger.info(f"📄 Created {WATCHLIST_EXAMPLE}")
