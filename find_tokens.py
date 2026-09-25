"""
Utility script to look up Angel One symbol tokens for ETFs.

Run: python find_tokens.py SYMBOL1 SYMBOL2 ...
Example: python find_tokens.py ICICISILVER SETFGOLD CPSEETF

SECURITY NOTE: this script previously had real API credentials hardcoded
directly in the source. Never hardcode credentials in any script — always
load them from .env (which must never be committed to git). If you're
reading this after cloning a version that had hardcoded keys, rotate
those credentials immediately; treat anything that was ever committed
or shared as compromised.
"""

import sys
import pyotp
from SmartApi import SmartConnect

from config import load_config, ConfigError


def main():
    if len(sys.argv) < 2:
        print("Usage: python find_tokens.py SYMBOL1 SYMBOL2 ...")
        print("Example: python find_tokens.py ICICISILVER SETFGOLD CPSEETF")
        sys.exit(1)

    try:
        cfg = load_config()
    except ConfigError:
        sys.exit(1)

    smart = SmartConnect(api_key=cfg.angel_api_key)
    totp = pyotp.TOTP(cfg.angel_totp_secret).now()
    session = smart.generateSession(cfg.angel_client_id, cfg.angel_pin, totp)

    if not session.get("status"):
        print(f"❌ Login failed: {session.get('message')}")
        sys.exit(1)

    for symbol in sys.argv[1:]:
        result = smart.searchScrip("NSE", symbol)
        if result.get("status"):
            print(f"\n--- {symbol} ---")
            for d in result["data"]:
                print(f"  symbol: {d['tradingsymbol']} | token: {d['symboltoken']}")
        else:
            print(f"\n--- {symbol}: not found ---")

    smart.terminateSession(cfg.angel_client_id)


if __name__ == "__main__":
    main()
