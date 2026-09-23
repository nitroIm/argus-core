# ============================================================
# ARGUS - MEXC ORDERS v1 [PRODUCTION]
# ------------------------------------------------------------
# История ордеров и открытые ордера.
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
log = logging.getLogger("mexc.orders")


def get_open_orders(client, symbol=None):
    """Открытые ордера."""
    params = {}
    if symbol:
        params["symbol"] = symbol
    return client.signed_get(
        "/api/v3/openOrders", params,
    )


def get_all_orders(client, symbol, days=7, limit=100):
    """История ордеров за N дней."""
    since = (
        datetime.now(timezone.utc)
        - timedelta(days=days)
    )
    start_ms = int(since.timestamp() * 1000)
    return client.signed_get(
        "/api/v3/allOrders",
        {
            "symbol": symbol,
            "startTime": start_ms,
            "limit": limit,
        },
    )


def fmt_order(o):
    """Формат одного ордера."""
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
    log.info("MEXC orders v1")

    if not is_configured():
        log.error("MEXC_API_KEY / MEXC_API_SECRET not set")
        sys.exit(1)

    client = MexcClient()

    for symbol in ["BTCUSDT", "ETHUSDT"]:
        log.info("--- " + symbol)

        opens = get_open_orders(client, symbol)
        if opens is None:
            log.warning("  Не удалось прочитать open orders")
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
            client, symbol, days=7, limit=50,
        )
        if history:
            log.info(
                "  История за 7 дней: %d",
                len(history),
            )
        else:
            log.info("  История пустая")

    log.info("done")


if __name__ == "__main__":
    main()