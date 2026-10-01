# ============================================================
# ARGUS-Trader — RETENTION (узел global)
# ------------------------------------------------------------
# v1: через 90 дней сырьё → дневные агрегаты.
#     Сжимаем ×24. История не теряется.
# ============================================================

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db2 import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("global.retention")

KEEP_DAYS = 90


def log_action(table, action, n, older):
    sql = (
        "INSERT INTO retention_log "
        "(table_name, action, rows_affected, "
        "older_than) VALUES (%s,%s,%s,%s)"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (table, action, n, older))


def aggregate_candles():
    """candles → candles_daily."""
    sql_agg = (
        "INSERT INTO candles_daily "
        "(symbol, timestamp, open, high, low, "
        "close, volume) "
        "SELECT symbol, "
        "date_trunc('day', timestamp) as d, "
        "(array_agg(open ORDER BY timestamp))[1], "
        "MAX(high), MIN(low), "
        "(array_agg(close ORDER BY timestamp DESC))[1], "
        "SUM(volume) "
        "FROM candles "
        "WHERE timestamp < NOW() - "
        "INTERVAL '%s days' "
        "GROUP BY symbol, d "
        "ON CONFLICT (symbol, timestamp) "
        "DO UPDATE SET "
        "open = EXCLUDED.open, "
        "high = EXCLUDED.high, "
        "low = EXCLUDED.low, "
        "close = EXCLUDED.close, "
        "volume = EXCLUDED.volume"
    ) % KEEP_DAYS

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_agg)
            n = cur.rowcount or 0
    log.info("candles → candles_daily: %d", n)
    return n


def aggregate_asia():
    """asia_market → asia_market_daily."""
    sql_agg = (
        "INSERT INTO asia_market_daily "
        "(symbol, timestamp, close, change_pct) "
        "SELECT symbol, "
        "date_trunc('day', timestamp) as d, "
        "(array_agg(close ORDER BY timestamp DESC))[1], "
        "AVG(change_pct) "
        "FROM asia_market "
        "WHERE timestamp < NOW() - "
        "INTERVAL '%s days' "
        "GROUP BY symbol, d "
        "ON CONFLICT (symbol, timestamp) "
        "DO UPDATE SET "
        "close = EXCLUDED.close, "
        "change_pct = EXCLUDED.change_pct"
    ) % KEEP_DAYS

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql_agg)
            n = cur.rowcount or 0
    log.info("asia → asia_daily: %d", n)
    return n


def delete_old(table):
    """Удаляет старые строки из сырья."""
    sql = (
        "DELETE FROM %s "
        "WHERE timestamp < NOW() - "
        "INTERVAL '%s days'"
    ) % (table, KEEP_DAYS)

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            n = cur.rowcount or 0
    log.info("delete %s: %d", table, n)
    return n


def main():
    log.info("=" * 60)
    log.info("ARGUS RETENTION — keep %d days",
             KEEP_DAYS)
    log.info("=" * 60)

    total = 0

    # 1. Агрегация
    try:
        n = aggregate_candles()
        total += n
        log_action("candles", "aggregate", n, None)
    except Exception as e:
        log.error("agg candles: %s", e)

    try:
        n = aggregate_asia()
        total += n
        log_action("asia_market", "aggregate", n, None)
    except Exception as e:
        log.error("agg asia: %s", e)

    # 2. Удаление старого сырья
    for tbl in ["candles", "funding_rates",
                "open_interest", "asia_market"]:
        try:
            n = delete_old(tbl)
            total += n
            log_action(tbl, "delete", n, None)
        except Exception as e:
            log.error("del %s: %s", tbl, e)

    log.info("=" * 60)
    log.info("DONE. Total rows: %d", total)
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()