# ============================================================
# ARGUS — СБОР ДАННЫХ (v3)
# v3: фикс datetime для Python 3.12+, import sys, Telegram-уведомление
# MEXC → Binance Vision → CoinGecko
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone

SYMBOL = "BTCUSDT"
LIMIT = 500
DATA_FILE = "data/price_history.json"

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=10,
        )
    except Exception:
        pass  # Тихо игнорируем ошибки нотификации


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
        print(f"⚠️ Binance Vision: {e}")
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
        # Формат CoinGecko OHLC: [time(ms), open, high, low, close]
        candles = []
        for item in data:
            candles.append({
                "time": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": 0.0,  # CoinGecko OHLC не отдает объем
            })
        return candles
    except Exception as e:
        print(f"⚠️ CoinGecko: {e}")
        return None


# ============================================================
# ОСНОВНОЕ
# ============================================================

def main():
    os.makedirs("data", exist_ok=True)
    
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
        except Exception:
            history = []
    else:
        history = []

    print(f"📊 Старых точек в истории: {len(history)}")

    # ---------- Пробуем по очереди ----------
    new_data = None
    source = None

    for fetcher, name in [(fetch_mexc, "MEXC"), (fetch_binance, "Binance Vision"), (fetch_coingecko, "CoinGecko")]:
        new_data = fetcher()
        if new_data:
            source = name
            break

    if not new_data:
        msg = "❌ ARGUS Collect: Все источники данных недоступны."
        print(msg)
        notify(msg)
        sys.exit(1)

    print(f"✅ Данные успешно получены с {source}: {len(new_data)} свечей")

    # ---------- Добавляем новые точки ----------
    existing_times = {p["time"] for p in history}
    new_points = 0

    for candle in new_data:
        if candle["time"] in existing_times:
            continue
        
        # ИСПРАВЛЕНО: безопасная работа с часовыми поясами для Python 3.12+
        candle["datetime"] = datetime.fromtimestamp(candle["time"] / 1000, tz=timezone.utc).isoformat()
        candle["source"] = source
        
        history.append(candle)
        new_points += 1

    # Сортируем по времени и обрезаем хвост, чтобы файл не раздувался
    history.sort(key=lambda x: x["time"])
    if len(history) > 20000:
        history = history[-20000:]

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

    # ---------- Итог и уведомление ----------
    last_price = history[-1]["close"] if history else 0
    first_date = history[0]["datetime"][:10] if history else "N/A"
    last_date = history[-1]["datetime"][:10] if history else "N/A"

    summary_msg = (
        f"📊 <b>ARGUS Market Data Updated</b>\n\n"
        f"💰 <b>BTC/USDT:</b> ${last_price:,.2f}\n"
        f"📡 <b>Источник:</b> {source}\n"
        f"📈 <b>Новых свечей:</b> {new_points}\n"
        f"📚 <b>Всего в базе:</b> {len(history)} (с {first_date} по {last_date})"
    )
    
    print("=" * 50)
    print(f"✅ Новых точек: {new_points}")
    print(f"📊 Всего: {len(history)}")
    print(f"   Первая: {first_date}")
    print(f"   Последняя: {last_date}")
    print("=" * 50)
    
    notify(summary_msg)


if __name__ == "__main__":
    main()
