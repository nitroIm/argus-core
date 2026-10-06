# ============================================================
# ARGUS-Trader — COLLECT ASIA + EUROPE + USA (узел global)
# ------------------------------------------------------------
# v6: COALESCE on change_pct — prevents NULL overwrite.
#     get_prev_close from DB for first row of window.
#     Same fix as external.py v3, macro.py v2.
# v5: batch insert per symbol, SAVEPOINT fallback.
# v4: RANGE=30d, MAX_ROWS=300.
# ============================================================

import sys
import logging
import requests
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
log = logging.getLogger("global.collect")

YAHOO_URL = (
    "https://query1.finance.yahoo.com"
    "/v8/finance/chart/{symbol}"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64)",
}

ASIA = [
    ("^N225",     "NIKKEI"),
    ("000001.SS", "SHANGHAI"),
    ("^HSI",      "HANGSENG"),
    ("CNY=X",     "USDCNY"),
]

EUROPE = [
    ("^GDAXI",    "DAX"),
    ("^STOXX50E", "SX5E"),
    ("^FTSE",     "FTSE"),
    ("EURUSD=X",  "EURUSD"),
]

USA = [
    ("^VIX",      "VIX"),
    ("^IXIC",     "NASDAQ"),
    ("^TNX",      "US10Y"),
]

ASIA_EXTRA = [
    ("JPY=X",     "USDJPY"),
    ("^KS11",     "KOSPI"),
    ("^TWII",     "TAIEX"),
]

INTERVAL = "1h"
RANGE = "30d"
MAX_ROWS = 300

SQL_HEAD = (
    "INSERT INTO asia_market "
    "(symbol, timestamp, close, change_pct, source) "
    "VALUES "
)

SQL_TAIL = (
    " ON CONFLICT (symbol, timestamp) "
    "DO UPDATE SET "
    "close=EXCLUDED.close, "
    "change_pct=COALESCE("
    "  EXCLUDED.change_pct, "
    "  asia_market.change_pct"
    ")"
)


def fetch_yahoo(code):
    url = YAHOO_URL.format(symbol=code)
    params = {"interval": INTERVAL, "range": RANGE}
    try:
        r = requests.get(
            url, params=params, headers=HEADERS,
            timeout=15,
        )
        if r.status_code != 200:
            log.warning(
                "%s: HTTP %d", code, r.status_code,
            )
            return []
        data = r.json()
    except Exception as e:
        log.error("%s: %s", code, e)
        return []

    try:
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"][
            "quote"
        ][0]["close"]
    except Exception as e:
        log.error("%s: parse %s", code, e)
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


def get_prev_close(db_symbol, first_ts):
    """Last close in DB before first_ts.
    Used to compute change_pct for the very first
    row of the Yahoo window (which has no prev).
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close FROM asia_market "
                    "WHERE symbol = %s "
                    "AND timestamp < %s "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (db_symbol, first_ts),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return float(row[0])
    except Exception as e:
        log.warning(
            "prev_close %s: %s", db_symbol, e,
        )
    return None


def compute_changes(rows, prev_close=None):
    out = []
    prev = prev_close
    for ts, close in rows:
        change = None
        if prev is not None and prev > 0:
            change = (close - prev) / prev * 100
        out.append((ts, close, change))
        prev = close
    return out


def save_rows_batch(db_symbol, rows):
    if not rows:
        return 0, True
    placeholders = ",".join(
        ["(%s,%s,%s,%s,%s)"] * len(rows)
    )
    sql = SQL_HEAD + placeholders + SQL_TAIL
    params = []
    for ts, close, change in rows:
        params.extend([
            db_symbol, ts, close, change, "yahoo",
        ])
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                n = cur.rowcount or 0
                return n, True
    except Exception as e:
        log.warning(
            "batch failed %s: %s", db_symbol, e,
        )
        return 0, False


def save_rows_savepoint(db_symbol, rows):
    if not rows:
        return 0
    sql = (
        SQL_HEAD + "(%s,%s,%s,%s,%s)" + SQL_TAIL
    )
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for i, (ts, close, change) in (
                    enumerate(rows)
                ):
                    sp = "sp_row_" + str(i)
                    try:
                        cur.execute(
                            "SAVEPOINT " + sp
                        )
                        cur.execute(sql, (
                            db_symbol, ts, close,
                            change, "yahoo",
                        ))
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
        log.error("save %s: %s", db_symbol, e)
    return added


def save_rows(db_symbol, rows):
    if not rows:
        return 0
    n, ok = save_rows_batch(db_symbol, rows)
    if ok:
        return n
    log.info(
        "  fallback to SAVEPOINT for %s",
        db_symbol,
    )
    return save_rows_savepoint(db_symbol, rows)


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


def process_one(code, db_symbol):
    rows = fetch_yahoo(code)
    if not rows:
        log.warning("  no data")
        return 0, True

    prev_close = get_prev_close(
        db_symbol, rows[0][0],
    )
    if prev_close is not None:
        log.info(
            "  prev_close from DB: %.4f",
            prev_close,
        )

    rows = compute_changes(rows, prev_close)
    n = save_rows(db_symbol, rows)

    filled = sum(
        1 for _, _, c in rows if c is not None
    )
    log.info(
        "  fetched=%d saved=%d with_change=%d",
        len(rows), n, filled,
    )
    return n, False


def fetch_group(name, lst):
    log.info("--- %s ---", name)
    total = 0
    failed = 0
    for code, db_symbol in lst:
        log.info("%s (%s)", db_symbol, code)
        n, err = process_one(code, db_symbol)
        total += n
        if err:
            failed += 1
    return total, failed


def main():
    log.info("=" * 60)
    log.info("ARGUS COLLECT ASIA+EU+USA — DB2 v6")
    log.info("=" * 60)

    total = 0
    failed = 0

    n, f = fetch_group("ASIA", ASIA)
    total += n
    failed += f

    n, f = fetch_group("EUROPE", EUROPE)
    total += n
    failed += f

    n, f = fetch_group("USA", USA)
    total += n
    failed += f

    n, f = fetch_group("ASIA_EXTRA", ASIA_EXTRA)
    total += n
    failed += f

    log.info("=" * 60)
    log.info("DONE. Total saved: %d", total)
    log.info("=" * 60)

    status = "partial" if failed > 0 else "ok"
    log_run("collect_asia", status, total)
    close_connection()


if __name__ == "__main__":
    main()