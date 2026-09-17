# ============================================================
# ARGUS — СБОР ДАННЫХ С MEXC
# Каждый час забирает свечи и добавляет в историю
# ============================================================

import os
import json
import requests
from datetime import datetime

# ---------- Настройки ----------
SYMBOL = "BTCUSDT"
INTERVAL = "Min60"
LIMIT = 500
DATA_FILE = "data/price_history.json"

# ---------- Загрузка старой истории ----------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
else:
    history = []

print(f"📊 Старых точек: {len(history)}")

# ---------- Запрос к MEXC ----------
url = "https://api.mexc.com/api/v3/klines"
params = {
    "symbol": SYMBOL,
    "interval": INTERVAL,
    "limit": LIMIT
}

try:
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    candles = response.json()
    print(f"📥 Получено свечей: {len(candles)}")

except Exception as e:
    print(f"❌ Ошибка запроса: {e}")
    exit(1)

# ---------- Разбор свечей ----------
# Формат MEXC: [time, open, high, low, close, volume, ...]
existing_times = {point["time"] for point in history}
new_points = 0

for candle in candles:
    ts = int(candle[0])

    if ts in existing_times:
        continue  # уже есть

    history.append({
        "time": ts,
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
        "datetime": datetime.utcfromtimestamp(ts / 1000).isoformat()
    })
    new_points += 1

# ---------- Сортировка по времени ----------
history.sort(key=lambda x: x["time"])

# ---------- Сохранение ----------
os.makedirs("data", exist_ok=True)

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, ensure_ascii=False, indent=2)

print(f"✅ Новых точек: {new_points}")
print(f"📊 Всего в истории: {len(history)}")
if history:
    print(f"   Первая: {history[0]['datetime']}")
    print(f"   Последняя: {history[-1]['datetime']}")