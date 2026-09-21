# ============================================================
# ARGUS-Trader — CONFIG
# ------------------------------------------------------------
# Все константы модуля: символы, метрики, лимиты, приоритеты.
# Импортируется всеми остальными файлами.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import os
from pathlib import Path

# ============================================================
# ПУТИ
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent          # crypto/
CRYPTO_ROOT = SCRIPT_DIR                              # crypto/
DATA_DIR = CRYPTO_ROOT / "data"                       # crypto/data/
COLLECT_DIR = CRYPTO_ROOT / "collect"                 # crypto/collect/

DATA_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# БАЗА ДАННЫХ (Supabase)
# ============================================================
DB_URL = (os.getenv("ARGUS_DB_URL") or "").strip()

# ============================================================
# СИМВОЛЫ
# ============================================================
# Внутренний формат: BTCUSDT, ETHUSDT (нормализованный)
# Каждая биржа имеет свой формат — конвертация в exchanges.py

SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# Форматы символов для разных бирж
SYMBOL_FORMATS = {
    "okx":    {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"},
    "bitget": {"BTCUSDT": "BTCUSDT",       "ETHUSDT": "ETHUSDT"},
    "gate":   {"BTCUSDT": "BTC_USDT",      "ETHUSDT": "ETH_USDT"},
    "kucoin": {"BTCUSDT": "XBTUSDTM",      "ETHUSDT": "ETHUSDTM"},
    "mexc":   {"BTCUSDT": "BTCUSDT",       "ETHUSDT": "ETHUSDT"},
    # CoinGecko использует coin_id, а не symbol
    "coingecko": {"BTCUSDT": "bitcoin",    "ETHUSDT": "ethereum"},
}

# ============================================================
# ТАЙМФРЕЙМЫ
# ============================================================
TIMEFRAMES = ["1h", "1d"]           # пока только эти

# ============================================================
# ПРИОРИТЕТЫ ПО МЕТРИКАМ (fallback chain)
# ============================================================
# Для каждой метрики — список бирж в порядке приоритета.
# Если первая упала — идём к следующей.

PRIORITY = {
    "ohlcv":     ["okx", "bitget", "gate", "kucoin", "mexc"],
    "funding":   ["okx", "bitget", "gate", "kucoin"],
    "oi":        ["okx", "bitget", "gate"],
    "ls_ratio":  ["okx", "bitget", "gate"],
    "taker":     ["okx", "gate"],
    "spot_price": ["okx", "bitget", "mexc", "coingecko"],
}

# ============================================================
# RETENTION (дни)
# ============================================================
RETENTION = {
    "raw_candles":       90,
    "raw_funding":       90,
    "raw_oi":            90,
    "raw_ls":            90,
    "raw_taker":         90,
    "raw_liquidations":  30,
    # Агрегаты и производные — навсегда (не в этой таблице)
}

# ============================================================
# API — БИРЖИ
# ============================================================
EXCHANGE_ENDPOINTS = {
    "okx":     "https://www.okx.com",
    "bitget":  "https://api.bitget.com",
    "gate":    "https://api.gateio.ws",
    "kucoin":  "https://api-futures.kucoin.com",
    "mexc":    "https://api.mexc.com",
    "coingecko": "https://api.coingecko.com",
}

# CoinGecko API (можно передавать ключ через env, если есть Pro)
COINGECKO_API_KEY = (os.getenv("COINGECKO_API_KEY") or "").strip()

# ============================================================
# ВАЛИДАЦИЯ
# ============================================================
VALIDATION = {
    # Минимально допустимая цена (защита от 0 и мусора)
    "min_price": 1.0,
    # Максимально допустимый |change| за 1 час (защита от выбросов)
    "max_change_pct_1h": 30.0,
    # Timestamp не должен быть старше N часов (защита от устаревших данных)
    "max_age_hours": 2,
    # Timestamp не должен быть в будущем (защита от глюков API)
    "max_future_minutes": 5,
}

# ============================================================
# TELEGRAM
# ============================================================
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# ============================================================
# ТЕСТ
# ============================================================
if __name__ == "__main__":
    print("📋 ARGUS-Trader CONFIG")
    print("=" * 50)
    print(f"CRYPTO_ROOT: {CRYPTO_ROOT}")
    print(f"DATA_DIR:    {DATA_DIR}")
    print(f"DB_URL:      {'✅ есть' if DB_URL else '❌ нет'}")
    print(f"Symbols:     {SYMBOLS}")
    print(f"Timeframes:  {TIMEFRAMES}")
    print(f"Retention:   {RETENTION['raw_candles']} дней для свечей")
    print(f"Priority OHLCV: {PRIORITY['ohlcv']}")
    print("=" * 50)