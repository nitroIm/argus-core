# ============================================================
# ARGUS - MEXC MARKET v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: топ-10 стакана, чистый naming,
#     sanity-check bid<ask.
# Публичные данные, без ключа.
# ============================================================

import sys
import logging
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from client import MexcClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mexc.market")

BOOK_TOP_N = 10


def fetch_order_book(client, symbol, limit=20):
    return client.public_get(
        "/api/v3/depth",
        {"symbol": symbol, "limit": limit},
    )


def fetch_recent_trades(client, symbol, limit=50):
    return client.public_get(
        "/api/v3/trades",
        {"symbol": symbol, "limit": limit},
    )


def fetch_ticker_24h(client, symbol):
    return client.public_get(
        "/api/v3/ticker/24hr",
        {"symbol": symbol},
    )


def analyze_order_book(book, top_n=BOOK_TOP_N):
    if not book:
        return None

    bids = book.get("bids", [])[:top_n]
    asks = book.get("asks", [])[:top_n]

    if not bids or not asks:
        return None

    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return None

    if best_bid >= best_ask:
        log.warning(
            "book cross: bid=%s ask=%s",
            best_bid, best_ask,
        )
        return None

    bid_vol = 0.0
    ask_vol = 0.0
    for b in bids:
        try:
            bid_vol += float(b[1])
        except Exception:
            continue
    for a in asks:
        try:
            ask_vol += float(a[1])
        except Exception:
            continue

    total = bid_vol + ask_vol
    if total == 0:
        return None

    bid_pct = bid_vol / total * 100
    ask_pct = 100 - bid_pct

    if bid_pct > 60:
        state = "BID-heavy"
    elif ask_pct > 60:
        state = "ASK-heavy"
    else:
        state = "balanced"

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_pct": round(
            (best_ask - best_bid)
            / best_bid * 100, 4,
        ),
        "bid_vol": round(bid_vol, 4),
        "ask_vol": round(ask_vol, 4),
        "bid_pct": round(bid_pct, 2),
        "ask_pct": round(ask_pct, 2),
        "state": state,
    }


def analyze_trades(trades):
    if not trades:
        return None

    buy_vol = 0.0
    sell_vol = 0.0

    for t in trades:
        try:
            qty = float(t.get("qty", 0))
        except Exception:
            continue
        # isBuyerMaker=True -> продавец агрессор
        is_buyer_maker = t.get(
            "isBuyerMaker", False,
        )
        if is_buyer_maker:
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
    log.info("MEXC market v2")

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        book = fetch_order_book(
            client, symbol, 20,
        )
        bs = analyze_order_book(book)
        if bs:
            log.info(
                "  Book: bid %.1f%% / ask %.1f%% "
                "(%s) spread=%.4f%%",
                bs["bid_pct"],
                bs["ask_pct"],
                bs["state"],
                bs["spread_pct"],
            )

        trades = fetch_recent_trades(
            client, symbol, 100,
        )
        ts = analyze_trades(trades)
        if ts:
            log.info(
                "  Trades: buy %.1f%% / sell %.1f%% "
                "(%s) [%d]",
                ts["buy_pct"],
                ts["sell_pct"],
                ts["state"],
                ts["trades_count"],
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