# ============================================================
# ARGUS - MEXC TRADES v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: days=7 (MEXC myTrades limit, as with allOrders).
#     count moved inside try (only valid rows).
# v2: группировка комиссий по asset,
#     ясный комментарий isBuyer.
# ============================================================

import sys
import logging
from pathlib import Path
from datetime import datetime, timezone
from datetime import timedelta

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from client import MexcClient
from client import is_configured

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mexc.trades")

MAX_DAYS = 7


def get_my_trades(client, symbol, days=MAX_DAYS, limit=100):
    since = (
        datetime.now(timezone.utc)
        - timedelta(days=days)
    )
    start_ms = int(since.timestamp() * 1000)
    return client.signed_get(
        "/api/v3/myTrades",
        {
            "symbol": symbol,
            "startTime": start_ms,
            "limit": limit,
        },
    )


def analyze_trades(trades):
    """
    isBuyer=True  -> это МОЯ покупка
    isBuyer=False -> это МОЯ продажа
    (не путать с isBuyerMaker в market)
    """
    if not trades:
        return None

    buy_usdt = 0.0
    sell_usdt = 0.0
    fees_by_asset = {}
    count = 0
    skipped = 0

    for t in trades:
        try:
            qty = float(t.get("qty", 0))
            price = float(t.get("price", 0))
            commission = float(
                t.get("commission", 0)
            )
        except Exception:
            skipped += 1
            continue

        is_buyer = t.get("isBuyer", False)
        comm_asset = t.get(
            "commissionAsset", "?",
        )

        if is_buyer:
            buy_usdt += qty * price
        else:
            sell_usdt += qty * price

        if comm_asset not in fees_by_asset:
            fees_by_asset[comm_asset] = 0.0
        fees_by_asset[comm_asset] += commission
        count += 1

    fees_clean = {
        k: round(v, 8)
        for k, v in fees_by_asset.items()
    }

    return {
        "count": count,
        "skipped": skipped,
        "buy_usdt": round(buy_usdt, 4),
        "sell_usdt": round(sell_usdt, 4),
        "fees_by_asset": fees_clean,
    }


def main():
    log.info("MEXC trades v3")

    if not is_configured():
        log.error("Keys not set")
        sys.exit(1)

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        trades = get_my_trades(
            client, symbol,
            days=MAX_DAYS, limit=100,
        )

        if trades is None:
            log.warning("  read error")
            continue

        if not trades:
            log.info("  no trades %dd", MAX_DAYS)
            continue

        stats = analyze_trades(trades)
        if stats:
            log.info(
                "  trades: %d (skip %d)",
                stats["count"], stats["skipped"],
            )
            log.info(
                "  buy: $%.2f | sell: $%.2f",
                stats["buy_usdt"],
                stats["sell_usdt"],
            )
            for asset, amt in (
                stats["fees_by_asset"].items()
            ):
                log.info(
                    "  fee: %s %s", amt, asset,
                )

    log.info("done")


if __name__ == "__main__":
    main()