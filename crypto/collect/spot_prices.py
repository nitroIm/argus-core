# ============================================================
# ARGUS-Trader — SPOT PRICES
# ------------------------------------------------------------
# Быстрый запрос текущей цены с 5 бирж (fallback chain).
# Не пишет в БД. Используется для оперативных задач
# (бот, быстрые отчёты, разведка).
# ------------------------------------------------------------
# v2: Multi-exchange fallback (MEXC → Binance → Bybit → OKX → CoinGecko)
# v3: pathlib, запись в crypto/data/
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

MARKET_LOG = DATA_DIR / "market_data.jsonl"
MARKET_SUMMARY = DATA_DIR / "market_summary.json"

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


# ============================================================
# БИРЖИ (порядок приоритета)
# ============================================================
EXCHANGES = [
    {
        "name": "MEXC",
        "url": "https://api.mexc.com/api/v3/ticker/price?symbol={symbol}",
        "parse": lambda r: float(r.json()["price"]),
    },
    {
        "name": "Binance",
        "url": "https://api.binance.com/api/v3/ticker/price?symbol={symbol}",
        "parse": lambda r: float(r.json()["price"]),
    },
    {
        "name": "Bybit",
        "url": "https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}",
        "parse": lambda r: float(r.json()["result"]["list"][0]["lastPrice"]),
    },
    {
        "name": "OKX",
        "url": "https://www.okx.com/api/v5/market/ticker?instId={symbol_dash}",
        "parse": lambda r: float(r.json()["data"][0]["last"]),
        "symbol_format": lambda s: s.replace("USDT", "-USDT"),
    },
    {
        "name": "CoinGecko",
        "url": "https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd",
        "parse": lambda r: float(r.json()[r.json().keys().__iter__().__next__()]["usd"]),
        "coin_ids": {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "SOLUSDT": "solana",
        },
    },
]


def fetch_price_from_exchange(exchange: dict, symbol: str) -> float:
    """Пытается получить цену с конкретной биржи."""
    if "coin_ids" in exchange:
        coin_id = exchange["coin_ids"].get(symbol)
        if not coin_id:
            raise ValueError(f"No coin_id for {symbol}")
        url = exchange["url"].format(coin_id=coin_id)
    elif "symbol_format" in exchange:
        symbol_dash = exchange["symbol_format"](symbol)
        url = exchange["url"].format(symbol=symbol_dash)
    else:
        url = exchange["url"].format(symbol=symbol)

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return exchange["parse"](r)


def fetch_price_fallback(symbol: str) -> dict:
    """Перебирает биржи по порядку, возвращает первую успешную + все попытки."""
    attempts = []
    for exchange in EXCHANGES:
        try:
            price = fetch_price_from_exchange(exchange, symbol)
            attempts.append({
                "exchange": exchange["name"],
                "price": price,
                "success": True,
            })
            return {"price": price, "source": exchange["name"], "attempts": attempts}
        except Exception as e:
            attempts.append({
                "exchange": exchange["name"],
                "error": str(e)[:100],
                "success": False,
            })
    return {"price": None, "source": None, "attempts": attempts}


def fetch_fear_greed() -> dict:
    """Индекс страха и жадности (не от биржи)."""
    url = "https://api.alternative.me/fng/?limit=1"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["data"][0]
    return {
        "value": int(data["value"]),
        "classification": data["value_classification"],
        "timestamp": int(data["timestamp"]),
    }


# ============================================================
# ОСНОВНОЕ
# ============================================================
def main():
    print("📡 ARGUS-Trader collect spot_prices...")

    collected_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "assets": {},
        "sentiment": {},
        "sources_tried": [],
    }

    # 1. Сбор цен с fallback
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    for symbol in symbols:
        print(f"\n   🔍 {symbol}:")
        result = fetch_price_fallback(symbol)

        if result["price"]:
            collected_data["assets"][symbol] = result["price"]
            collected_data["sources_tried"].append(f"{symbol}→{result['source']}")
            print(f"   ✅ {symbol}: ${result['price']:,.2f} (от {result['source']})")
        else:
            print(f"   ❌ {symbol}: все биржи недоступны")
            for attempt in result["attempts"]:
                print(f"      - {attempt['exchange']}: {attempt.get('error', 'unknown')}")

    # 2. Индекс страха и жадности
    print("\n   🔍 Fear & Greed Index:")
    try:
        fg = fetch_fear_greed()
        collected_data["sentiment"] = fg
        print(f"   ✅ Fear & Greed: {fg['value']} ({fg['classification']})")
    except Exception as e:
        print(f"   ❌ Fear & Greed недоступен: {e}")

    # 3. Сохранение истории (JSONL, append)
    try:
        with open(MARKET_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(collected_data, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"   ⚠️ Не удалось записать в лог: {e}")

    # 4. Обновление summary (для быстрого чтения)
    try:
        with open(MARKET_SUMMARY, "w", encoding="utf-8") as f:
            json.dump(collected_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"   ⚠️ Не удалось обновить summary: {e}")

    # 5. Отчёт в Telegram
    msg_lines = ["📊 <b>ARGUS Market Collect</b>\n"]

    for sym, price in collected_data["assets"].items():
        msg_lines.append(f"💰 <b>{sym.replace('USDT', '')}:</b> ${price:,.2f}")

    if collected_data["sentiment"]:
        fg = collected_data["sentiment"]
        emoji = "😱" if fg["value"] < 30 else "😐" if fg["value"] < 60 else "🤑"
        msg_lines.append(f"\n{emoji} <b>Fear & Greed:</b> {fg['value']} ({fg['classification']})")

    if collected_data["sources_tried"]:
        msg_lines.append(f"\n<i>Источники: {', '.join(collected_data['sources_tried'])}</i>")

    notify("\n".join(msg_lines))
    print("\n✅ Сбор данных завершён.")


if __name__ == "__main__":
    main()