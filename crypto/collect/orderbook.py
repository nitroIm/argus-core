# ============================================================
# ARGUS-Trader - ORDERBOOK COLLECTOR [PRODUCTION]
# ------------------------------------------------------------
# Сбор стакана MEXC раз в час.
# Пишет в orderbook_snapshots (первая база).
# ============================================================

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(CRYPTO_ROOT / "mexc"))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.orderbook")

TOP_N = 50

SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def get_mexc_client():
    try:
        from client import MexcClient
        return MexcClient()
    except Exception as e:
        log.error("mexc client: %s", e)
        return None


def fetch_book(client, symbol):
    try:
        data = client.public_get(
            "/api/v3/depth",
            {"symbol": symbol, "limit": TOP_N},
        )
        if not data:
            return None
        return data
    except Exception as e:
        log.warning("fetch %s: %s", symbol, e)
        return None


def analyze(book):
    if not book:
        return None

    bids = book.get("bids", [])[:TOP_N]
    asks = book.get("asks", [])[:TOP_N]
    if not bids or not asks:
        return None

    try:
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except Exception:
        return None

    if best_bid <= 0 or best_ask <= 0:
        return None
    if best_bid >= best_ask:
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
    if total <= 0:
        return None

    bid_pct = bid_vol / total * 100
    ask_pct = 100 - bid_pct
    spread_pct = (best_ask - best_bid) / best_bid * 100

    return {
        "bid_vol": round(bid_vol, 6),
        "ask_vol": round(ask_vol, 6),
        "bid_pct": round(bid_pct, 4),
        "ask_pct": round(ask_pct, 4),
        "spread_pct": round(spread_pct, 4),
    }


def save_snapshot(symbol, ts, stats):
    sql = (
        "INSERT INTO orderbook_snapshots "
        "(symbol, timestamp, bid_vol, ask_vol, "
        "bid_pct, ask_pct, spread_pct, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp) DO NOTHING"
    )
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    symbol, ts,
                    stats["bid_vol"], stats["ask_vol"],
                    stats["bid_pct"], stats["ask_pct"],
                    stats["spread_pct"], "mexc",
                ))
                return cur.rowcount if cur.rowcount else 0
    except Exception as e:
        log.error("save %s: %s", symbol, e)
        return 0


def collect_orderbook():
    """Собирает снимок стакана для каждого символа."""
    client = get_mexc_client()
    if not client:
        log.error("no mexc client")
        return 0

    ts = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0,
    )

    total_added = 0
    for symbol in SYMBOLS:
        book = fetch_book(client, symbol)
        stats = analyze(book)
        if not stats:
            log.warning("%s: no stats", symbol)
            continue
        added = save_snapshot(symbol, ts, stats)
        total_added += added
        log.info(
            "[orderbook/%s] bid=%.1f%% ask=%.1f%% "
            "spread=%.4f%% added=%d",
            symbol, stats["bid_pct"],
            stats["ask_pct"], stats["spread_pct"],
            added,
        )

    return total_added