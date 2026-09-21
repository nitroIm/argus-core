# ============================================================
# ARGUS-Trader — CONFIG
# ------------------------------------------------------------
# v2: fix — max_age_hours для разных метрик, исторические
#     метрики (OHLCV, funding, oi, ls, taker) — до 90 дней.
#     Только context — свежий (2 часа).
# ============================================================

import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR
DATA_DIR = CRYPTO_ROOT / "data"
COLLECT_DIR = CRYPTO_ROOT / "collect"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- БАЗА ДАННЫХ ---
DB_URL = (os.getenv("ARGUS_DB_URL") or "").strip()

# --- СИМВОЛЫ ---
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

SYMBOL_FORMATS = {
    "okx":    {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"},
    "bitget": {"BTCUSDT": "BTCUSDT",       "ETHUSDT": "ETHUSDT"},
    "gate":   {"BTCUSDT": "BTC_USDT",      "ETHUSDT": "ETH_USDT"},
    "kucoin": {"BTCUSDT": "XBTUSDTM",      "ETHUSDT": "ETHUSDTM"},
    "mexc":   {"BTCUSDT": "BTCUSDT",       "ETHUSDT": "ETHUSDT"},
    "coingecko": {"BTCUSDT": "bitcoin",    "ETHUSDT": "ethereum"},
}

# --- ТАЙМФРЕЙМЫ ---
TIMEFRAMES = ["1h", "1d"]

# --- ПРИОРИТЕТЫ ---
PRIORITY = {
    "ohlcv":     ["okx", "bitget", "gate", "kucoin"],
    "funding":   ["okx", "bitget", "gate", "kucoin"],
    "oi":        ["okx", "bitget", "gate"],
    "ls_ratio":  ["okx", "bitget", "gate"],
    "taker":     ["okx", "gate"],
    "spot_price": ["okx", "bitget", "coingecko"],
}

# --- RETENTION ---
RETENTION = {
    "raw_candles":       90,
    "raw_funding":       90,
    "raw_oi":            90,
    "raw_ls":            90,
    "raw_taker":         90,
    "raw_liquidations":  30,
}

# --- API ---
EXCHANGE_ENDPOINTS = {
    "okx":       "https://www.okx.com",
    "bitget":    "https://api.bitget.com",
    "gate":      "https://api.gateio.ws",
    "kucoin":    "https://api-futures.kucoin.com",
    "mexc":      "https://api.mexc.com",
    "coingecko": "https://api.coingecko.com",
}

COINGECKO_API_KEY = (os.getenv("COINGECKO_API_KEY") or "").strip()

# --- ВАЛИДАЦИЯ ---
# v2: max_age_hours_* — разные для разных метрик.
#     Исторические данные (OHLCV, funding, oi, ls, taker) — 90 дней.
#     Context — 2 часа (свежий снимок).
VALIDATION = {
    "min_price": 1.0,
    "max_change_pct_1h": 30.0,
    "max_future_minutes": 5,
    # Свежесть по метрикам:
    "max_age_hours": {
        "ohlcv":     24 * 90,   # 90 дней
        "funding":   24 * 90,
        "oi":        24 * 90,
        "ls_ratio":  24 * 90,
        "taker":     24 * 90,
        "context":   2,          # только context свежий
    },
    "default_max_age_hours": 24 * 90,
}

# --- TELEGRAM ---
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# --- ЛОГИ ---
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


if __name__ == "__main__":
    print("📋 ARGUS-Trader CONFIG v2")
    print("=" * 50)
    print(f"CRYPTO_ROOT: {CRYPTO_ROOT}")
    print(f"DATA_DIR:    {DATA_DIR}")
    print(f"DB_URL:      {'✅ есть' if DB_URL else '❌ нет'}")
    print(f"Symbols:     {SYMBOLS}")
    print(f"Timeframes:  {TIMEFRAMES}")
    print(f"Retention:   {RETENTION['raw_candles']} дней для свечей")
    print(f"Priority OHLCV: {PRIORITY['ohlcv']}")
    print(f"max_age по метрикам: {VALIDATION['max_age_hours']}")
    print("=" * 50)