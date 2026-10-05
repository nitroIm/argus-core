# ============================================================
# ARGUS-Trader — SPOT PRICES
# ------------------------------------------------------------
# v4: + BNBUSDT in symbols list and CoinGecko coin_ids.
#     + SOLUSDT added to coin_ids (was missing).
# v3: pathlib, writes to crypto/data/.
# v2: Multi-exchange fallback (MEXC -> Binance -> Bybit
#     -> OKX -> CoinGecko).
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

MARKET_LOG = DATA_DIR / "market_data.jsonl"
MARKET_SUMMARY = DATA_DIR / "market_summary.json"

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            (
                "https://api.telegram.org/bot"
                + BOT_TOKEN + "/sendMessage"
            ),
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


EXCHANGES = [
    {
        "name": "MEXC",
        "url": (
            "https://api.mexc.com/api/v3"
            "/ticker/price?symbol={symbol}"
        ),
        "parse": lambda r: float(r.json()["price"]),
    },
    {
        "name": "Binance",
        "url": (
            "https://api.binance.com/api/v3"
            "/ticker/price?symbol={symbol}"
        ),
        "parse": lambda r: float(r.json()["price"]),
    },
    {
        "name": "Bybit",
        "url": (
            "https://api.bybit.com/v5/market"
            "/tickers?category=spot&symbol={symbol}"
        ),
        "parse": lambda r: float(
            r.json()["result"]["list"][0]["lastPrice"]
        ),
    },
    {
        "name": "OKX",
        "url": (
            "https://www.okx.com/api/v5/market"
            "/ticker?instId={symbol_dash}"
        ),
        "parse": lambda r: float(
            r.json()["data"][0]["last"]
        ),
        "symbol_format": lambda s: s.replace(
            "USDT", "-USDT"
        ),
    },
    {
        "name": "CoinGecko",
        "url": (
            "https://api.coingecko.com/api/v3"
            "/simple/price?ids={coin_id}"
            "&vs_currencies=usd"
        ),
        "parse": lambda r: float(
            next(iter(r.json().values()))["usd"]
        ),
        "coin_ids": {
            "BTCUSDT": "bitcoin",
            "ETHUSDT": "ethereum",
            "SOLUSDT": "solana",
            "BNBUSDT": "binancecoin",
        },
    },
]


def fetch_price_from_exchange(
    exchange: dict, symbol: str,
) -> float:
    if "coin_ids" in exchange:
        coin_id = exchange["coin_ids"].get(symbol)
        if not coin_id:
            raise ValueError(
                "No coin_id for " + symbol
            )
        url = exchange["url"].format(
            coin_id=coin_id,
        )
    elif "symbol_format" in exchange:
        symbol_dash = exchange["symbol_format"](
            symbol
        )
        url = exchange["url"].format(
            symbol=symbol_dash,
        )
    else:
        url = exchange["url"].format(
            symbol=symbol,
        )

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return exchange["parse"](r)


def fetch_price_fallback(symbol: str) -> dict:
    attempts = []
    for exchange in EXCHANGES:
        try:
            price = fetch_price_from_exchange(
                exchange, symbol,
            )
            attempts.append({
                "exchange": exchange["name"],
                "price": price,
                "success": True,
            })
            return {
                "price": price,
                "source": exchange["name"],
                "attempts": attempts,
            }
        except Exception as e:
            attempts.append({
                "exchange": exchange["name"],
                "error": str(e)[:100],
                "success": False,
            })
    return {
        "price": None,
        "source": None,
        "attempts": attempts,
    }


def fetch_fear_greed() -> dict:
    url = "https://api.alternative.me/fng/?limit=1"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    data = r.json()["data"][0]
    return {
        "value": int(data["value"]),
        "classification": data[
            "value_classification"
        ],
        "timestamp": int(data["timestamp"]),
    }


def main():
    print("collect spot_prices...")

    collected_data = {
        "timestamp": datetime.now(
            timezone.utc
        ).isoformat(),
        "assets": {},
        "sentiment": {},
        "sources_tried": [],
    }

    symbols = [
        "BTCUSDT", "ETHUSDT",
        "SOLUSDT", "BNBUSDT",
    ]
    for symbol in symbols:
        print("  " + symbol + ":")
        result = fetch_price_fallback(symbol)

        if result["price"]:
            collected_data["assets"][symbol] = (
                result["price"]
            )
            collected_data["sources_tried"].append(
                symbol + "->" + result["source"]
            )
            print(
                "   OK " + symbol + ": $"
                + format(result["price"], ",.2f")
                + " (" + result["source"] + ")"
            )
        else:
            print(
                "   FAIL " + symbol
                + ": all exchanges down"
            )
            for a in result["attempts"]:
                print(
                    "      - " + a["exchange"]
                    + ": " + a.get(
                        "error", "unknown"
                    )
                )

    print("  Fear & Greed Index:")
    try:
        fg = fetch_fear_greed()
        collected_data["sentiment"] = fg
        print(
            "   OK Fear & Greed: "
            + str(fg["value"])
            + " (" + fg["classification"] + ")"
        )
    except Exception as e:
        print(
            "   FAIL Fear & Greed: "
            + str(e)
        )

    try:
        with open(
            MARKET_LOG, "a", encoding="utf-8",
        ) as f:
            f.write(
                json.dumps(
                    collected_data,
                    ensure_ascii=False,
                ) + "\n"
            )
    except Exception as e:
        print("   log write: " + str(e))

    try:
        with open(
            MARKET_SUMMARY, "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                collected_data, f,
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        print("   summary write: " + str(e))

    msg_lines = [
        "<b>ARGUS Market Collect</b>\n"
    ]

    for sym, price in collected_data[
        "assets"
    ].items():
        short = sym.replace("USDT", "")
        msg_lines.append(
            "<b>" + short + ":</b> $"
            + format(price, ",.2f")
        )

    if collected_data["sentiment"]:
        fg = collected_data["sentiment"]
        if fg["value"] < 30:
            emoji = "[F]"
        elif fg["value"] < 60:
            emoji = "[N]"
        else:
            emoji = "[G]"
        msg_lines.append(
            "\n" + emoji
            + " <b>Fear & Greed:</b> "
            + str(fg["value"])
            + " (" + fg["classification"] + ")"
        )

    if collected_data["sources_tried"]:
        msg_lines.append(
            "\n<i>Sources: "
            + ", ".join(
                collected_data["sources_tried"]
            ) + "</i>"
        )

    notify("\n".join(msg_lines))
    print("done.")


if __name__ == "__main__":
    main()