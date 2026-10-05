# ============================================================
# ARGUS-Trader — COLLECT SOL/BNB (узел global)
# ------------------------------------------------------------
# v5: COALESCE on oi_value — OKX returns None for oi_value,
#     old DO UPDATE nulled it. Same fix as external.py v3.
#     SOURCES split per metric (removed bitget.fetch_oi,
#     removed gate.fetch_taker — methods do not exist).
# v4: + LS + taker.
# v3: SAVEPOINT per row.
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

SOURCES_OHLCV = ["okx", "bitget", "gate"]
SOURCES_FUNDING = ["okx", "bitget", "gate"]
SOURCES_OI = ["okx", "gate"]
SOURCES_LS = ["okx", "bitget", "gate"]
SOURCES_TAKER = ["okx"]

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
    "oi=EXCLUDED.oi, "
    "oi_value=COALESCE("
    "  EXCLUDED.oi_value, "
    "  open_interest.oi_value"
    "), "
    "source=EXCLUDED.source"
)

SQL_LS = (
    "INSERT INTO long_short_ratio "
    "(symbol, timestamp, ls_ratio, long_pct, "
    "short_pct, source) "
    "VALUES (%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO UPDATE SET "
    "ls_ratio=EXCLUDED.ls_ratio, "
    "long_pct=EXCLUDED.long_pct, "
    "short_pct=EXCLUDED.short_pct, "
    "source=EXCLUDED.source"
)

SQL_TAKER = (
    "INSERT INTO taker_flow "
    "(symbol, timestamp, buy_vol, sell_vol, source) "
    "VALUES (%s,%s,%s,%s,%s) "
    "ON CONFLICT (symbol, timestamp) "
    "DO UPDATE SET "
    "buy_vol=EXCLUDED.buy_vol, "
    "sell_vol=EXCLUDED.sell_vol, "
    "source=EXCLUDED.source"
)


def save(sql, rows, fields):
    if not rows:
        return 0
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for i, r in enumerate(rows):
                    sp = "sp_row_" + str(i)
                    try:
                        cur.execute("SAVEPOINT " + sp)
                        vals = tuple(
                            r.get(f) for f in fields
                        )
                        cur.execute(sql, vals)
                        n = cur.rowcount or 0
                        cur.execute(
                            "RELEASE SAVEPOINT " + sp
                        )
                        added += n
                    except Exception as e:
                        try:
                            cur.execute(
                                "ROLLBACK TO SAVEPOINT "
                                + sp
                            )
                        except Exception:
                            pass
                        log.warning(
                            "row %d skip: %s", i, e,
                        )
    except Exception as e:
        log.error("save: %s", e)
    return added


def try_sources(symbol, sources, fn):
    for name in sources:
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


def fresh_only(rows, limit):
    if not rows:
        return []
    try:
        rows = sorted(
            rows,
            key=lambda x: x["timestamp"],
            reverse=True,
        )
    except Exception as e:
        log.warning("sort: %s", e)
        return rows[:limit]
    return rows[:limit]


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
    failed = []

    name, rows = try_sources(
        symbol, SOURCES_OHLCV,
        lambda c: c.fetch_ohlcv(symbol, TF, LIMIT),
    )
    if rows:
        rows = fresh_only(rows, LIMIT)
        n = save(SQL_CANDLES, rows, [
            "symbol", "timeframe", "timestamp",
            "open", "high", "low", "close",
            "volume", "source",
        ])
        log.info("  candles[%s]: %d", name, n)
        total += n
    else:
        log.warning("  candles: no data")
        failed.append("candles")

    name, rows = try_sources(
        symbol, SOURCES_FUNDING,
        lambda c: c.fetch_funding(symbol, LIMIT),
    )
    if rows:
        rows = fresh_only(rows, LIMIT)
        n = save(SQL_FUNDING, rows, [
            "symbol", "timestamp", "rate", "source",
        ])
        log.info("  funding[%s]: %d", name, n)
        total += n
    else:
        log.warning("  funding: no data")
        failed.append("funding")

    name, rows = try_sources(
        symbol, SOURCES_OI,
        lambda c: c.fetch_oi(symbol, LIMIT),
    )
    if rows:
        before = len(rows)
        rows = fresh_only(rows, LIMIT)
        n = save(SQL_OI, rows, [
            "symbol", "timestamp", "oi",
            "oi_value", "source",
        ])
        log.info(
            "  oi[%s]: %d (from %d)",
            name, n, before,
        )
        total += n
    else:
        log.warning("  oi: no data")
        failed.append("oi")

    name, rows = try_sources(
        symbol, SOURCES_LS,
        lambda c: c.fetch_ls(symbol, LIMIT),
    )
    if rows:
        before = len(rows)
        rows = fresh_only(rows, LIMIT)
        n = save(SQL_LS, rows, [
            "symbol", "timestamp", "ls_ratio",
            "long_pct", "short_pct", "source",
        ])
        log.info(
            "  ls[%s]: %d (from %d)",
            name, n, before,
        )
        total += n
    else:
        log.warning("  ls: no data")
        failed.append("ls")

    name, rows = try_sources(
        symbol, SOURCES_TAKER,
        lambda c: c.fetch_taker(symbol, LIMIT),
    )
    if rows:
        before = len(rows)
        rows = fresh_only(rows, LIMIT)
        n = save(SQL_TAKER, rows, [
            "symbol", "timestamp", "buy_vol",
            "sell_vol", "source",
        ])
        log.info(
            "  taker[%s]: %d (from %d)",
            name, n, before,
        )
        total += n
    else:
        log.warning("  taker: no data")
        failed.append("taker")

    return total, failed


def main():
    log.info("=" * 60)
    log.info("ARGUS COLLECT SOL/BNB — DB2 v5")
    log.info("=" * 60)

    total = 0
    all_failed = []
    for sym in SYMBOLS:
        try:
            n, failed = collect_symbol(sym)
            total += n
            for f in failed:
                all_failed.append(sym + "/" + f)
        except Exception as e:
            log.error("%s: %s", sym, e)
            all_failed.append(sym + "/exception")
        log.info("")

    log.info("=" * 60)
    log.info("DONE. Total saved: %d", total)
    log.info("=" * 60)

    if all_failed:
        status = "partial"
        err = "failed: " + ", ".join(all_failed)
    else:
        status = "ok"
        err = None
    log_run("collect_sol_bnb", status, total, err)
    close_connection()


if __name__ == "__main__":
    main()