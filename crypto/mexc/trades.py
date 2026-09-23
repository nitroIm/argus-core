# ============================================================
# ARGUS - MEXC TRADES v1 [PRODUCTION]
# ------------------------------------------------------------
# История реальных сделок (fills).
# Считает комиссии, PnL по факту.
# Требует API-ключ.
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


def get_my_trades(client, symbol, days=30, limit=100):
    """История сделок за N дней."""
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
    """Анализ: buy/sell volume, fees."""
    if not trades:
        return None

    buy_vol = 0
    sell_vol = 0
    fees_total = 0
    fees_asset = None
    count = len(trades)

    for t in trades:
        qty = float(t.get("qty", 0))
        price = float(t.get("price", 0))
        is_buyer = t.get("isBuyer", False)
        commission = float(t.get("commission", 0))
        comm_asset = t.get("commissionAsset", "?")

        if is_buyer:
            buy_vol += qty * price
        else:
            sell_vol += qty * price

        fees_total += commission
        if fees_asset is None:
            fees_asset = comm_asset

    return {
        "count": count,
        "buy_usdt": round(buy_vol, 4),
        "sell_usdt": round(sell_vol, 4),
        "fees": round(fees_total, 8),
        "fees_asset": fees_asset,
    }


def main():
    log.info("MEXC trades v1")

    if not is_configured():
        log.error("MEXC_API_KEY / MEXC_API_SECRET not set")
        sys.exit(1)

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        trades = get_my_trades(
            client, symbol, days=30, limit=100,
        )

        if trades is None:
            log.warning("  Не удалось прочитать")
            continue

        if not trades:
            log.info("  Сделок за 30 дней нет")
            continue

        stats = analyze_trades(trades)
        if stats:
            log.info(
                "  Сделок: %d", stats["count"]
            )
            log.info(
                "  Buy: $%.2f | Sell: $%.2f",
                stats["buy_usdt"],
                stats["sell_usdt"],
            )
            log.info(
                "  Комиссии: %s %s",
                stats["fees"],
                stats["fees_asset"],
            )

    log.info("done")


if __name__ == "__main__":
    main()