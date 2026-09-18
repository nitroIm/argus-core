# ============================================================
# ARGUS — СБОР ДАННЫХ С MEXC
# Отказоустойчивый: MEXC → Binance → Bybit
# ============================================================

import os
import json
import requests
from datetime import datetime

# ---------- Настройки ----------
SYMBOL = "BTCUSDT"
LIMIT = 500
DATA_FILE = "data/price_history.json"

# ---------- Отказоустойчивая цепочка ----------
EXCHANGES = [
    {
        "name": "MEXC",
        "url": "https://api.mexc.com/api/v3/klines",
        "params": {"symbol": SYMBOL, "interval": "Min60", "limit": LIMIT},
    },
    {
        "name": "Binance",
        "url": "https://api.binance.com/api/v3/klines",
        "params": {"symbol": SYMBOL, "interval": "1h", "limit": LIMIT},
    },
    {
        "name": "Bybit",
        "url": "https://api.bybit.com/v5/market/kline",
        "params": {"category": "spot", "symbol": SYMBOL, "interval": "60", "limit": 200},
        "custom_parse": True,
    },
]


def parse_candle(candle):
    """Единый формат свечи из ответа MEXC/Binance."""
    return {
        "time": int(candle[0]),
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
    }


def parse_bybit(result):
    """Bybit возвращает свечи в другом порядке и формате."""
    candles = []
    for item in result:
        candles.append({
            "time": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    return candles


def fetch(exchange):
    """Пробует получить данные с биржи."""
    try:
        print(f"🔄 {exchange['name']}...")
        r = requests.get(exchange["url"], params=exchange["params"], timeout=20)
        r.raise_for_status()
        data = r.json()

        if exchange.get("custom_parse"):
            # Bybit: данные внутри result.list
            result = data.get("result", {}).get("list", [])
            return parse_bybit(result)
        else:
            return [parse_candle(c) for c in data]

    except Exception as e:
        print(f"⚠️ {exchange['name']} не ответил: {e}")
        return None


# ---------- Загрузка старой истории ----------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
else:
    history = []

print(f"📊 Старых точек: {len(history)}")

# ---------- Получаем данные с первой доступной биржи ----------
new_data = None
source = None
for ex in EXCHANGES:
    new_data = fetch(ex)
    if new_data:
        source = ex["name"]
        break

if not new_data:
    print("❌ Все биржи недоступны. Пропускаем сбор.")
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

# ---------- Сортируем ----------
history.sort(key=lambda x: x["time"])

# ---------- Ограничиваем историю (макс 20 000 точек) ----------
if len(history) > 20000:
    history = history[-20000:]

# ---------- Сохранение ----------
os.makedirs("data", exist_ok=True)
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=2)

# ---------- Вывод ----------
print("=" * 50)
print(f"✅ Новых точек: {new_points}")
print(f"📊 Всего в истории: {len(history)}")
if history:
    print(f"   Первая: {history[0]['datetime']}")
    print(f"   Последняя: {history[-1]['datetime']}")
print("=" * 50)