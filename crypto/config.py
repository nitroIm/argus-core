# ============================================================
# ARGUS-Trader — CONFIG
# ------------------------------------------------------------
# v6: PRIORITY, SYMBOL_FORMATS, COINGECKO_API_KEY restored
#     (v5 removed them — unsafe without full repo grep).
#     RETENTION 400 (train window ~378 days).
# v5: RETENTION 90 -> 400, dead-code removal (reverted).
# v4: PRIORITY oi/taker fixed in collect/priority (not here).
# v3: LIMITS incremental/backfill.
# ============================================================

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR
DATA_DIR = CRYPTO_ROOT / "data"
COLLECT_DIR = CRYPTO_ROOT / "collect"

DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_URL = (
    os.getenv("ARGUS_DB_URL") or ""
).strip()

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# Kept for compat. Real routing is in exchanges.py per-client.
SYMBOL_FORMATS = {
    "okx": {
        "BTCUSDT": "BTC-USDT-SWAP",
        "ETHUSDT": "ETH-USDT-SWAP",
    },
    "bitget": {
        "BTCUSDT": "BTCUSDT",
        "ETHUSDT": "ETHUSDT",
    },
    "gate": {
        "BTCUSDT": "BTC_USDT",
        "ETHUSDT": "ETH_USDT",
    },
    "kucoin": {
        "BTCUSDT": "XBTUSDTM",
        "ETHUSDT": "ETHUSDTM",
    },
    "mexc": {
        "BTCUSDT": "BTCUSDT",
        "ETHUSDT": "ETHUSDT",
    },
    "coingecko": {
        "BTCUSDT": "bitcoin",
        "ETHUSDT": "ethereum",
    },
}

TIMEFRAMES = ["1h", "1d"]

# Kept for compat. Real priority lives in collect/priority.py.
PRIORITY = {
    "ohlcv": [
        "okx", "bitget", "gate", "kucoin",
    ],
    "funding": [
        "okx", "bitget", "gate", "kucuin",
    ],
    "oi": ["okx", "gate"],
    "ls_ratio": ["okx", "bitget", "gate"],
    "taker": ["okx"],
    "spot_price": ["okx", "bitget", "coingecko"],
}

# Retention for raw tables. Must exceed train window (~378 days).
RETENTION = {
    "raw_candles": 400,
    "raw_funding": 400,
    "raw_oi": 400,
    "raw_ls": 400,
    "raw_taker": 400,
}

EXCHANGE_ENDPOINTS = {
    "okx": "https://www.okx.com",
    "bitget": "https://api.bitget.com",
    "gate": "https://api.gateio.ws",
    "kucoin": "https://api-futures.kucoin.com",
    "mexc": "https://api.mexc.com",
    "coingecko": "https://api.coingecko.com",
}

# Kept for compat.
COINGECKO_API_KEY = (
    os.getenv("COINGECKO_API_KEY") or ""
).strip()

VALIDATION = {
    "min_price": 1.0,
    "max_change_pct_1h": 30.0,
    "max_future_minutes": 5,
    "max_age_hours": {
        "ohlcv": 24 * 90,
        "funding": 24 * 90,
        "oi": 24 * 90,
        "ls_ratio": 24 * 90,
        "taker": 24 * 90,
        "context": 2,
    },
    "default_max_age_hours": 24 * 90,
}

LIMITS_INCREMENTAL = {
    "ohlcv": 3,
    "funding": 3,
    "oi": 3,
    "ls_ratio": 3,
    "taker": 3,
}

LIMITS_BACKFILL = {
    "ohlcv": 100,
    "funding": 100,
    "oi": 720,
    "ls_ratio": 720,
    "taker": 72,
}

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID") or ""
).strip()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


if __name__ == "__main__":
    print("ARGUS-Trader CONFIG v6")
    print("=" * 50)
    ok = "yes" if DB_URL else "no"
    print("DB_URL: %s" % ok)
    print("Symbols: %s" % SYMBOLS)
    print("Limits incr: %s" % LIMITS_INCREMENTAL)
    print("Limits backfill: %s" % LIMITS_BACKFILL)
    print("=" * 50)