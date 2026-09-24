# ============================================================
# ARGUS - MEXC ORDERS v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: фильтр по статусу, orderId в выводе.
# v2: убран startTime (MEXC ограничение 7 дней).
# ============================================================

import sys
import logging
from pathlib import Path

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
log = logging.getLogger("mexc.orders")


def get_open_orders(client, symbol=None):
    params = {}
    if symbol:
        params["symbol"] = symbol
    return client.signed_get(
        "/api/v3/openOrders", params,
    )


def get_all_orders(client, symbol, limit=100):
    """История (MEXC: только 7 дней)."""
    return client.signed_get(
        "/api/v3/allOrders",
        {
            "symbol": symbol,
            "limit": limit,
        },
    )


def filter_by_status(orders, status):
    if not orders:
        return []
    return [
        o for o in orders
        if o.get("status") == status
    ]


def fmt_order(o):
    oid = str(o.get("orderId", "?"))[-8:]
    side = o.get("side", "?")
    otype = o.get("type", "?")
    price = o.get("price", "?")
    qty = o.get("origQty", "?")
    status = o.get("status", "?")
    executed = o.get("executedQty", "0")
    return (
        "  #" + oid
        + " " + side + " " + otype
        + " price=" + str(price)
        + " qty=" + str(qty)
        + " exec=" + str(executed)
        + " [" + status + "]"
    )


def main():
    log.info("MEXC orders v3")

    if not is_configured():
        log.error("Keys not set")
        sys.exit(1)

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        opens = get_open_orders(client, symbol)
        if opens is None:
            log.warning("  read error")
            continue

        if not opens:
            log.info("  no open orders")
        else:
            log.info(
                "  open: %d", len(opens),
            )
            for o in opens[:10]:
                log.info(fmt_order(o))

        history = get_all_orders(
            client, symbol, limit=100,
        )
        if history:
            filled = filter_by_status(
                history, "FILLED",
            )
            log.info(
                "  history: %d (filled: %d)",
                len(history), len(filled),
            )
            for o in filled[:5]:
                log.info(fmt_order(o))
        else:
            log.info("  history empty")

    log.info("done")


if __name__ == "__main__":
    main()