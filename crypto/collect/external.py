# ============================================================
# ARGUS-Trader - EXTERNAL MARKET [PRODUCTION]
# ------------------------------------------------------------
# v1: сбор DXY, SPX, Gold с Yahoo Finance (без ключа).
#     Пишет в таблицу external_market.
# ------------------------------------------------------------
# Требования:
#   pip install requests==2.32.3
# ============================================================

import sys
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.external")

YAHOO_URL = (
    "https://query1.finance.yahoo.com"
    "/v8/finance/chart/{symbol}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
}

# symbol -> (yahoo_code, db_symbol)
EXTERNAL = [
    ("DX-Y.NYB", "DXY"),
    ("^GSPC", "SPX"),
    ("GC=F", "GOLD"),
]

INTERVAL = "1h"
RANGE = "2d"
MAX_ROWS = 48


def fetch_yahoo(symbol_code):
    url = YAHOO_URL.format(symbol=symbol_code)
    params = {
        "interval": INTERVAL,
        "range": RANGE,
    }
    try:
        r = requests.get(
            url,
            params=params,
            headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(
                "%s: HTTP %d",
                symbol_code, r.status_code,
            )
            return []
        data = r.json()
    except Exception as e:
        log.error("%s: %s", symbol_code, e)
        return []

    try:
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"][
            "quote"
        ][0]["close"]
    except Exception as e:
        log.error(
            "%s: parse %s", symbol_code, e,
        )
        return []

    out = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        try:
            ts_dt = datetime.fromtimestamp(
                ts, tz=timezone.utc,
            )
            out.append((ts_dt, float(close)))
        except Exception:
            continue

    return out[-MAX_ROWS:]


def compute_changes(rows):
    """Добавляет change_pct между соседними точками."""
    out = []
    prev = None
    for ts, close in rows:
        change = None
        if prev is not None and prev > 0:
            change = (close - prev) / prev * 100
        out.append((ts, close, change))
        prev = close
    return out


def save_rows(db_symbol, rows):
    if not rows:
        return 0

    added = 0
    sql = (
        "INSERT INTO external_market "
        "(symbol, timestamp, close, "
        "change_pct, source) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp) "
        "DO UPDATE SET "
        "close = EXCLUDED.close, "
        "change_pct = EXCLUDED.change_pct"
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for ts, close, change in rows:
                    try:
                        cur.execute(
                            sql,
                            (
                                db_symbol,
                                ts,
                                close,
                                change,
                                "yahoo",
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning("insert skip: %s", e)
    except Exception as e:
        log.error("save %s: %s", db_symbol, e)
    return added


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EXTERNAL MARKET v1")
    log.info("=" * 60)

    total = 0
    for code, db_symbol in EXTERNAL:
        log.info("%s (%s)", db_symbol, code)
        rows = fetch_yahoo(code)
        if not rows:
            log.warning("  no data")
            continue

        rows = compute_changes(rows)
        added = save_rows(db_symbol, rows)
        total += added
        log.info(
            "  fetched=%d saved=%d",
            len(rows), added,
        )

    log.info("=" * 60)
    log.info("DONE. Total saved: %d", total)
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()