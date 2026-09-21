# ============================================================
# ARGUS-Trader — HISTORICAL CANDLES
# ------------------------------------------------------------
# Массовая загрузка истории свечей (500 шт за раз) с fallback.
# Пишет накопительно в crypto/data/price_history.json.
# Используется для бэктестов и анализа истории.
# ------------------------------------------------------------
# v3: fix datetime для Python 3.12+, import sys, Telegram
# v4: pathlib, запись в crypto/data/
# Источники: MEXC → Binance Vision → CoinGecko
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути (pathlib, от файла) ---
SCRIPT_DIR = Path(__file__).resolve().parent        # crypto/collect/
CRYPTO_ROOT = SCRIPT_DIR.parent                     # crypto/
DATA_DIR = CRYPTO_ROOT / "data"                     # crypto/data/
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATA_FILE = DATA_DIR / "price_history.json"

# --- Параметры ---
SYMBOL = "BTCUSDT"
LIMIT = 500
MAX_HISTORY = 20000      # максимум точек в файле

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
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
    except Exception:
        pass


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
        # CoinGecko OHLC: [time(ms), open, high, low, close]
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


# ============================================================
# ОСНОВНОЕ
# ============================================================
def main():
    # Загружаем существующую историю
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
            if not isinstance(history, list):
                history = []
        except Exception as e:
            print(f"⚠️ Не читается {DATA_FILE.name}: {e}")
            history = []
    else:
        history = []

    print(f"📊 Старых точек в истории: {len(history)}")

    # ---------- Пробуем источники по очереди ----------
    new_data = None
    source = None

    for fetcher, name in [
        (fetch_mexc, "MEXC"),
        (fetch_binance, "Binance Vision"),
        (fetch_coingecko, "CoinGecko"),
    ]:
        new_data = fetcher()
        if new_data:
            source = name
            break

    if not new_data:
        msg = "❌ ARGUS-Trader Collect: Все источники данных недоступны."
        print(msg)
        notify(msg)
        sys.exit(1)

    print(f"✅ Данные получены с {source}: {len(new_data)} свечей")

    # ---------- Добавляем новые точки ----------
    existing_times = {p["time"] for p in history}
    new_points = 0

    for candle in new_data:
        if candle["time"] in existing_times:
            continue

        # Безопасная работа с часовыми поясами для Python 3.12+
        candle["datetime"] = datetime.fromtimestamp(
            candle["time"] / 1000, tz=timezone.utc
        ).isoformat()
        candle["source"] = source

        history.append(candle)
        new_points += 1

    # Сортируем по времени и обрезаем хвост
    history.sort(key=lambda x: x["time"])
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]

    # Сохраняем
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не удалось записать {DATA_FILE.name}: {e}")

    # ---------- Итог ----------
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