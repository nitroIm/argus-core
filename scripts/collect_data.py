# ============================================================
# ARGUS — СБОР ДАННЫХ (v2)
# MEXC → Binance Vision → CoinGecko
# ============================================================

import os
import json
import requests
from datetime import datetime

SYMBOL = "BTCUSDT"
LIMIT = 500
DATA_FILE = "data/price_history.json"


def parse_candle(candle):
    return {
        "time": int(candle[0]),
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
    }


# ---------- MEXC ----------
def fetch_mexc():
    try:
        print("🔄 MEXC...")
        url = "https://api.mexc.com/api/v3/klines"
        # ВАЖНО: интервал '60m', а не 'Min60'
        params = {"symbol": SYMBOL, "interval": "60m", "limit": LIMIT}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        return [parse_candle(c) for c in data]
    except Exception as e:
        print(f"⚠️ MEXC: {e}")
        return None


# ---------- Binance Vision (без геоблока) ----------
def fetch_binance():
    try:
        print("🔄 Binance Vision...")
        url = "https://data-api.binance.vision/api/v3/klines"
        params = {"symbol": SYMBOL, "interval": "1h", "limit": LIMIT}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        return [parse_candle(c) for c in data]
    except Exception as e:
        print(f"⚠️ Binance: {e}")
        return None


# ---------- CoinGecko (агрегатор) ----------
def fetch_coingecko():
    try:
        print("🔄 CoinGecko...")
        url = "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc"
        params = {"vs_currency": "usd", "days": "30"}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        # Формат: [time, open, high, low, close]
        candles = []
        for item in data:
            candles.append({
                "time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": 0.0,
            })
        return candles
    except Exception as e:
        print(f"⚠️ CoinGecko: {e}")
        return None


# ---------- Загрузка истории ----------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
else:
    history = []

print(f"📊 Старых точек: {len(history)}")

# ---------- Пробуем по очереди ----------
new_data = None
source = None

for fetcher, name in [(fetch_mexc, "MEXC"), (fetch_binance, "Binance"), (fetch_coingecko, "CoinGecko")]:
    new_data = fetcher()
    if new_data:
        source = name
        break

if not new_data:
    print("❌ Все источники недоступны.")
    exit(1)

print(f"✅ Данные получены с {source}: {len(new_data)} свечей")

# ---------- Добавляем новые точки ----------
existing_times = {p["time"] for p in history}
new_points = 0

for candle in new_data:
    if candle["time"] in existing_times:
        continue
    candle["datetime"] = datetime.utcfromtimestamp(candle["time"] / 1000).isoformat()
    candle["source"] = source
    history.append(candle)
    new_points += 1

history.sort(key=lambda x: x["time"])
if len(history) > 20000:
    history = history[-20000:]

os.makedirs("data", exist_ok=True)
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=2)

print("=" * 50)
print(f"✅ Новых точек: {new_points}")
print(f"📊 Всего: {len(history)}")
if history:
    print(f"   Первая: {history[0]['datetime']}")
    print(f"   Последняя: {history[-1]['datetime']}")
print("=" * 50)