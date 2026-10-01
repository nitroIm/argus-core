# ============================================================
# ARGUS-Trader — COLLECT SOL/BNB (узел global)
# ------------------------------------------------------------
# v1: OHLCV + funding + OI для SOLUSDT и BNBUSDT.
#     Пишет в DB2 (ARGUS_DB_URL_2).
#     Основную базу НЕ трогает.
# ============================================================

import sys
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from collect.exchanges import CLIENTS
from db2 import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("global.sol_bnb")

SYMBOLS = ["SOLUSDT", "BNBUSDT"]
TF = "1h"
LIMIT = 5
SOURCES = ["okx", "bitget", "gate"]

SQL_CANDLES = (
    "INSERT INTO candles "
    "(symbol, timeframe, timestamp, open, high, "
    "low, close, volume, source) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timeframe, timestamp) "
    "DO UPDATE SET "
    "open=EXCLUDED.open, high=EXCLUDED.high, "
    "low=EXCLUDED.low, close=EXCLUDED.close, "
    "volume=EXCLUDED.volume, source=EXCLUDED.source"
)

SQL_FUNDING = (
    "INSERT INTO funding_rates "
    "(symbol, timestamp, rate, source) "
    "VALUES (%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO UPDATE SET "
    "rate=EXCLUDED.rate, source=EXCLUDED.source"
)

SQL_OI = (
    "INSERT INTO open_interest "
    "(symbol, timestamp, oi, oi_value, source) "
    "VALUES (%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO UPDATE SET "
    "oi=EXCLUDED.oi, oi_value=EXCLUDED.oi_value, "
    "source=EXCLUDED.source"
)


def save(sql, rows, fields):
    if not rows:
        return 0
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for r in rows:
                    try:
                        vals = tuple(
                            r.get(f) for f in fields
                        )
                        cur.execute(sql, vals)
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning("skip: %s", e)
    except Exception as e:
        log.error("save: %s", e)
    return added


def try_sources(symbol, fn):
    """Перебирает источники, возвращает (name, rows)."""
    for name in SOURCES:
        c = CLIENTS.get(name)
        if not c:
            continue
        try:
            rows = fn(c)
            if rows:
                return name, rows
        except Exception as e:
            log.warning("%s: %s", name, e)
    return None, []


def log_run(job, status, n=0, err=None):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO collect_log "
                    "(job_name, metric, started_at, "
                    "finished_at, records_added, "
                    "status, error) "
                    "VALUES (%s,%s,%s,NOW(),%s,%s,%s)",
                    (
                        job, None,
                        datetime.now(timezone.utc),
                        n, status, err,
                    ),
                )
    except Exception as e:
        log.warning("log_run: %s", e)


def collect_symbol(symbol):
    log.info("%s — start", symbol)
    total = 0

    # OHLCV
    name, rows = try_sources(
        symbol,
        lambda c: c.fetch_ohlcv(symbol, TF, LIMIT),
    )
    if rows:
        n = save(SQL_CANDLES, rows, [
            "symbol", "timeframe", "timestamp",
            "open", "high", "low", "close",
            "volume", "source",
        ])
        log.info("  candles[%s]: %d", name, n)
        total += n
    else:
        log.warning("  candles: no data")

    # Funding
    name, rows = try_sources(
        symbol,
        lambda c: c.fetch_funding(symbol, LIMIT),
    )
    if rows:
        n = save(SQL_FUNDING, rows, [
            "symbol", "timestamp", "rate", "source",
        ])
        log.info("  funding[%s]: %d", name, n)
        total += n
    else:
        log.warning("  funding: no data")

    # OI
    name, rows = try_sources(
        symbol,
        lambda c: c.fetch_oi(symbol, LIMIT),
    )
    if rows:
        n = save(SQL_OI, rows, [
            "symbol", "timestamp", "oi",
            "oi_value", "source",
        ])
        log.info("  oi[%s]: %d", name, n)
        total += n
    else:
        log.warning("  oi: no data")

    return total


def main():
    log.info("=" * 60)
    log.info("ARGUS COLLECT SOL/BNB — DB2")
    log.info("=" * 60)

    total = 0
    for sym in SYMBOLS:
        try:
            total += collect_symbol(sym)
        except Exception as e:
            log.error("%s: %s", sym, e)
        log.info("")

    log.info("=" * 60)
    log.info("DONE. Total saved: %d", total)
    log.info("=" * 60)

    log_run("collect_sol_bnb", "ok", total)
    close_connection()


if __name__ == "__main__":
    main()