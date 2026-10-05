# ============================================================
# ARGUS-Trader — BOOTSTRAP CANDLES
# ------------------------------------------------------------
# РАЗОВЫЙ скрипт: загружает историю свечей ДО текущего
# MIN(timestamp) в БД. Существующие данные не трогаются.
#
# После bootstrap — recurring collectors досыпают новые
# свечи как обычно. Bootstrap больше не запускается.
# ============================================================

import sys
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from collect.exchanges import CLIENTS
from db import get_connection, close_connection

try:
    from db2 import get_connection as get_conn_db2
    from db2 import close_connection as close_conn_db2
    DB2_OK = True
except Exception as e:
    print("db2 fail: " + str(e))
    DB2_OK = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bootstrap")


# ============================================================
# НАСТРОЙКИ
# ============================================================
DAYS_BACK_1H = 120       # 120 дней истории 1h
DAYS_BACK_1D = 365       # 365 дней истории 1d
DAYS_BACK_FUNDING = 120
DAYS_BACK_OI = 120

# Источники по приоритету (первый успешный)
SOURCES = ["gate", "okx", "bitget"]

SYMBOLS_DB1 = ["BTCUSDT", "ETHUSDT"]
SYMBOLS_DB2 = ["SOLUSDT", "BNBUSDT"]

SQL_CANDLES = (
    "INSERT INTO candles "
    "(symbol, timeframe, timestamp, open, high, "
    "low, close, volume, source) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timeframe, timestamp) "
    "DO NOTHING"
)

SQL_FUNDING = (
    "INSERT INTO funding_rates "
    "(symbol, timestamp, rate, source) "
    "VALUES (%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO NOTHING"
)

SQL_OI = (
    "INSERT INTO open_interest "
    "(symbol, timestamp, oi, oi_value, source) "
    "VALUES (%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO NOTHING"
)


# ============================================================
# DB HELPERS
# ============================================================
def get_conn(symbol):
    if symbol in SYMBOLS_DB2:
        return get_conn_db2()
    return get_connection()


def min_ts_in(symbol, table, timeframe=None):
    """MIN(timestamp) в таблице. None если пусто."""
    try:
        with get_conn(symbol) as conn:
            with conn.cursor() as cur:
                if timeframe:
                    cur.execute(
                        "SELECT MIN(timestamp) FROM " + table
                        + " WHERE symbol=%s AND "
                        "timeframe=%s",
                        (symbol, timeframe),
                    )
                else:
                    cur.execute(
                        "SELECT MIN(timestamp) FROM " + table
                        + " WHERE symbol=%s",
                        (symbol,),
                    )
                row = cur.fetchone()
                if row and row[0]:
                    ts = row[0]
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    return ts
    except Exception as e:
        log.warning("min_ts %s.%s: %s", symbol, table, e)
    return None


def save_batch(symbol, sql, rows, fields):
    """Batch INSERT with SAVEPOINT fallback."""
    if not rows:
        return 0
    added = 0
    try:
        with get_conn(symbol) as conn:
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
        log.error("save %s: %s", symbol, e)
    return added


# ============================================================
# FETCH
# ============================================================
def try_fetch_ohlcv_range(symbol, timeframe,
                          start_ms, end_ms):
    """Try sources in priority, first non-empty wins."""
    for src in SOURCES:
        client = CLIENTS.get(src)
        if not client:
            continue
        try:
            rows = client.fetch_ohlcv_range(
                symbol, timeframe, start_ms, end_ms,
            )
            if rows:
                return src, rows
        except Exception as e:
            log.warning("[%s] %s: %s", src, symbol, e)
    return None, []


def try_fetch_funding(symbol, limit=1000):
    for src in SOURCES:
        client = CLIENTS.get(src)
        if not client:
            continue
        try:
            rows = client.fetch_funding(symbol, limit)
            if rows:
                return src, rows
        except Exception as e:
            log.warning("[%s] %s funding: %s",
                        src, symbol, e)
    return None, []


def try_fetch_oi(symbol, limit=1000):
    for src in SOURCES:
        client = CLIENTS.get(src)
        if not client:
            continue
        try:
            rows = client.fetch_oi(symbol, limit)
            if rows:
                return src, rows
        except Exception as e:
            log.warning("[%s] %s oi: %s",
                        src, symbol, e)
    return None, []


# ============================================================
# BOOTSTRAP ONE SYMBOL
# ============================================================
def bootstrap_symbol(symbol):
    log.info("=" * 60)
    log.info("%s — bootstrap", symbol)
    log.info("=" * 60)

    now = datetime.now(timezone.utc)

    # --- 1h ---
    bound_1h = min_ts_in(symbol, "candles", "1h")
    if bound_1h is None:
        bound_1h = now
        log.warning(
            "  candles 1h empty — using now as bound"
        )
    end_1h = bound_1h
    start_1h = end_1h - timedelta(days=DAYS_BACK_1H)
    log.info(
        "  1h target: %s .. %s (%d days)",
        start_1h.strftime("%Y-%m-%d"),
        end_1h.strftime("%Y-%m-%d"),
        DAYS_BACK_1H,
    )

    src, rows = try_fetch_ohlcv_range(
        symbol, "1h",
        int(start_1h.timestamp() * 1000),
        int(end_1h.timestamp() * 1000),
    )
    if rows:
        n = save_batch(symbol, SQL_CANDLES, rows, [
            "symbol", "timeframe", "timestamp",
            "open", "high", "low", "close",
            "volume", "source",
        ])
        log.info("  1h [%s]: fetched=%d saved=%d",
                 src, len(rows), n)
    else:
        log.warning("  1h: no data from sources")

    time.sleep(1)

    # --- 1d ---
    bound_1d = min_ts_in(symbol, "candles", "1d")
    if bound_1d is None:
        bound_1d = now
        log.warning(
            "  candles 1d empty — using now as bound"
        )
    end_1d = bound_1d
    start_1d = end_1d - timedelta(days=DAYS_BACK_1D)
    log.info(
        "  1d target: %s .. %s (%d days)",
        start_1d.strftime("%Y-%m-%d"),
        end_1d.strftime("%Y-%m-%d"),
        DAYS_BACK_1D,
    )

    src, rows = try_fetch_ohlcv_range(
        symbol, "1d",
        int(start_1d.timestamp() * 1000),
        int(end_1d.timestamp() * 1000),
    )
    if rows:
        n = save_batch(symbol, SQL_CANDLES, rows, [
            "symbol", "timeframe", "timestamp",
            "open", "high", "low", "close",
            "volume", "source",
        ])
        log.info("  1d [%s]: fetched=%d saved=%d",
                 src, len(rows), n)
    else:
        log.warning("  1d: no data from sources")

    time.sleep(1)

    # --- funding ---
    bound_f = min_ts_in(symbol, "funding_rates")
    if bound_f is None:
        bound_f = now
    cutoff_f = bound_f - timedelta(days=DAYS_BACK_FUNDING)
    src, rows = try_fetch_funding(symbol, limit=1000)
    if rows:
        # Только до bound_f (не пересекаем существующие)
        rows = [
            r for r in rows
            if r["timestamp"] < bound_f
        ]
        n = save_batch(symbol, SQL_FUNDING, rows, [
            "symbol", "timestamp", "rate", "source",
        ])
        log.info("  funding [%s]: fetched=%d saved=%d",
                 src, len(rows), n)
    else:
        log.warning("  funding: no data")

    time.sleep(1)

    # --- OI ---
    bound_oi = min_ts_in(symbol, "open_interest")
    if bound_oi is None:
        bound_oi = now
    src, rows = try_fetch_oi(symbol, limit=1000)
    if rows:
        rows = [
            r for r in rows
            if r["timestamp"] < bound_oi
        ]
        n = save_batch(symbol, SQL_OI, rows, [
            "symbol", "timestamp", "oi",
            "oi_value", "source",
        ])
        log.info("  OI [%s]: fetched=%d saved=%d",
                 src, len(rows), n)
    else:
        log.warning("  OI: no data")

    log.info("")


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 60)
    log.info("BOOTSTRAP CANDLES — разовая загрузка истории")
    log.info("DAYS_BACK_1H=%d, DAYS_BACK_1D=%d",
             DAYS_BACK_1H, DAYS_BACK_1D)
    log.info("sources=%s", SOURCES)
    log.info("=" * 60)

    for sym in SYMBOLS_DB1:
        try:
            bootstrap_symbol(sym)
        except Exception as e:
            log.error("%s: %s", sym, e)

    if DB2_OK:
        for sym in SYMBOLS_DB2:
            try:
                bootstrap_symbol(sym)
            except Exception as e:
                log.error("%s: %s", sym, e)

    log.info("=" * 60)
    log.info("BOOTSTRAP DONE")
    log.info("=" * 60)

    close_connection()
    if DB2_OK:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()