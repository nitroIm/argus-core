# ============================================================
# ARGUS - MEXC MARKET v1 [PRODUCTION]
# ------------------------------------------------------------
# Публичные данные с MEXC:
#   - Order book (стакан)
#   - Recent trades
#   - 24h ticker
# Без API-ключа.
# ============================================================

import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"

sys.path.insert(0, str(SCRIPT_DIR))

from client import MexcClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mexc.market")


def fetch_order_book(client, symbol, limit=20):
    """Стакан: bids + asks."""
    data = client.public_get(
        "/api/v3/depth",
        {"symbol": symbol, "limit": limit},
    )
    return data


def fetch_recent_trades(client, symbol, limit=50):
    """Последние сделки."""
    data = client.public_get(
        "/api/v3/trades",
        {"symbol": symbol, "limit": limit},
    )
    return data


def fetch_ticker_24h(client, symbol):
    """24h тикер."""
    data = client.public_get(
        "/api/v3/ticker/24hr",
        {"symbol": symbol},
    )
    return data


def analyze_order_book(book):
    """Анализ стакана: бид/аск imbalance."""
    if not book:
        return None

    bids = book.get("bids", [])
    asks = book.get("asks", [])

    bid_vol = sum(
        float(b[1]) for b in bids if len(b) >= 2
    )
    ask_vol = sum(
        float(a[1]) for a in asks if len(a) >= 2
    )

    if bid_vol + ask_vol == 0:
        return None

    bid_pct = bid_vol / (bid_vol + ask_vol) * 100
    ask_pct = 100 - bid_pct

    if bid_pct > 60:
        state = "BID-heavy (покупатели доминируют)"
    elif ask_pct > 60:
        state = "ASK-heavy (продавцы доминируют)"
    else:
        state = "balanced"

    return {
        "bid_vol": round(bid_vol, 4),
        "ask_vol": round(ask_vol, 4),
        "bid_pct": round(bid_pct, 2),
        "ask_pct": round(ask_pct, 2),
        "state": state,
    }


def analyze_trades(trades):
    """Анализ потока: buy vs sell."""
    if not trades:
        return None

    buy_vol = 0
    sell_vol = 0

    for t in trades:
        qty = float(t.get("qty", 0))
        is_buyer = t.get("isBuyerMaker", False)
        # isBuyerMaker=True → продавец агрессивен (sell flow)
        if is_buyer:
            sell_vol += qty
        else:
            buy_vol += qty

    total = buy_vol + sell_vol
    if total == 0:
        return None

    buy_pct = buy_vol / total * 100
    sell_pct = 100 - buy_pct

    if buy_pct > 60:
        state = "buyers aggressive"
    elif sell_pct > 60:
        state = "sellers aggressive"
    else:
        state = "balanced"

    return {
        "buy_vol": round(buy_vol, 4),
        "sell_vol": round(sell_vol, 4),
        "buy_pct": round(buy_pct, 2),
        "sell_pct": round(sell_pct, 2),
        "state": state,
        "trades_count": len(trades),
    }


def main():
    log.info("MEXC market v1")

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        book = fetch_order_book(client, symbol, 50)
        book_stats = analyze_order_book(book)
        if book_stats:
            log.info(
                "  Order book: bid %.1f%% / ask %.1f%% (%s)",
                book_stats["bid_pct"],
                book_stats["ask_pct"],
                book_stats["state"],
            )

        trades = fetch_recent_trades(client, symbol, 100)
        trade_stats = analyze_trades(trades)
        if trade_stats:
            log.info(
                "  Trades: buy %.1f%% / sell %.1f%% (%s) [%d trades]",
                trade_stats["buy_pct"],
                trade_stats["sell_pct"],
                trade_stats["state"],
                trade_stats["trades_count"],
            )

        ticker = fetch_ticker_24h(client, symbol)
        if ticker:
            log.info(
                "  24h: price=%s vol=%s",
                ticker.get("lastPrice"),
                ticker.get("quoteVolume"),
            )

    log.info("done")


if __name__ == "__main__":
    main()