# ============================================================
# ARGUS - MEXC ORDERS v2 [PRODUCTION]
# ------------------------------------------------------------
# v2: убран startTime (MEXC ограничение 7 дней)
# v1: чтение открытых + истории
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
    """История ордеров (MEXC: только 7 дней)."""
    return client.signed_get(
        "/api/v3/allOrders",
        {
            "symbol": symbol,
            "limit": limit,
        },
    )


def fmt_order(o):
    side = o.get("side", "?")
    otype = o.get("type", "?")
    price = o.get("price", "?")
    qty = o.get("origQty", "?")
    status = o.get("status", "?")
    executed = o.get("executedQty", "0")
    return (
        "  " + side + " " + otype
        + " price=" + str(price)
        + " qty=" + str(qty)
        + " exec=" + str(executed)
        + " [" + status + "]"
    )


def main():
    log.info("MEXC orders v2")

    if not is_configured():
        log.error("MEXC_API_KEY / MEXC_API_SECRET not set")
        sys.exit(1)

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        opens = get_open_orders(client, symbol)
        if opens is None:
            log.warning("  Ошибка чтения open orders")
            continue

        if not opens:
            log.info("  Открытых ордеров нет")
        else:
            log.info(
                "  Открытых ордеров: %d", len(opens)
            )
            for o in opens[:10]:
                log.info(fmt_order(o))

        history = get_all_orders(
            client, symbol, limit=100,
        )
        if history:
            log.info(
                "  История: %d ордеров",
                len(history),
            )
            for o in history[:5]:
                log.info(fmt_order(o))
        else:
            log.info("  История пустая")

    log.info("done")


if __name__ == "__main__":
    main()