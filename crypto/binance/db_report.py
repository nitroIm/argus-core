# ============================================================
# ARGUS — DB REPORT
# Path: crypto/binance/db_report.py
# ------------------------------------------------------------
# Читает таблицу candles и печатает отчёт:
#  - размер таблицы на диске
#  - сколько строк всего
#  - разбивка по symbol / market_type / timeframe
#  - диапазон времени (min / max open_time)
#  - количество свечей по дням (последние 14)
#  - сколько NULL в каждой колонке
#  - последние 10 строк
# ============================================================

import os
import sys
import logging
from datetime import datetime, timezone

try:
    import psycopg
except ImportError:
    print("ERR: psycopg not installed")
    sys.exit(1)

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("db.report")


def hr(title):
    print("")
    print("=" * 62)
    print(title)
    print("=" * 62)


def get_conn():
    if not DB_URL:
        raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(
        DB_URL, connect_timeout=15, application_name="argus-report",
    )


def report():
    conn = get_conn()
    cur = conn.cursor()

    # ---------- 1. Размер таблицы ----------
    hr("1. TABLE SIZE")
    cur.execute("""
        SELECT
            pg_size_pretty(pg_total_relation_size('candles')),
            pg_size_pretty(pg_relation_size('candles')),
            pg_size_pretty(pg_indexes_size('candles'))
    """)
    row = cur.fetchone()
    print("total size   : " + str(row[0]))
    print("table only   : " + str(row[1]))
    print("indexes only : " + str(row[2]))

    # ---------- 2. Всего строк ----------
    hr("2. TOTAL ROWS")
    cur.execute("SELECT COUNT(*) FROM candles")
    total = cur.fetchone()[0]
    print("candles rows : " + str(total))

    if total == 0:
        print("")
        print("таблица пустая — собирать ещё нечего")
        cur.close()
        conn.close()
        return

    # ---------- 3. Разбивка ----------
    hr("3. BREAKDOWN")
    cur.execute("""
        SELECT symbol, market_type, timeframe, COUNT(*)
        FROM candles
        GROUP BY symbol, market_type, timeframe
        ORDER BY symbol, market_type, timeframe
    """)
    print("{:<12} {:<10} {:<8} {:>10}".format(
        "symbol", "market", "tf", "rows"
    ))
    print("-" * 42)
    for sym, mt, tf, cnt in cur.fetchall():
        print("{:<12} {:<10} {:<8} {:>10}".format(
            str(sym), str(mt), str(tf), cnt
        ))

    # ---------- 4. Диапазон времени ----------
    hr("4. TIME RANGE")
    cur.execute("""
        SELECT
            MIN(open_time),
            MAX(open_time),
            MAX(created_at)
        FROM candles
    """)
    row = cur.fetchone()
    print("first open_time : " + str(row[0]))
    print("last  open_time : " + str(row[1]))
    print("last  created_at: " + str(row[2]))

    # ---------- 5. Свечей по дням (последние 14) ----------
    hr("5. ROWS PER DAY (last 14)")
    cur.execute("""
        SELECT
            DATE_TRUNC('day', open_time) AS d,
            COUNT(*)
        FROM candles
        GROUP BY d
        ORDER BY d DESC
        LIMIT 14
    """)
    print("{:<14} {:>8}".format("date (UTC)", "rows"))
    print("-" * 24)
    for d, cnt in cur.fetchall():
        print("{:<14} {:>8}".format(str(d)[:10], cnt))

    # ---------- 6. NULL по колонкам ----------
    hr("6. NULL COUNTS")
    cols = [
        "symbol", "market_type", "timeframe", "open_time",
        "open", "high", "low", "close", "volume",
        "taker_buy_volume", "quote_volume", "count",
    ]
    print("{:<22} {:>10}".format("column", "nulls"))
    print("-" * 34)
    for c in cols:
        cur.execute("SELECT COUNT(*) FROM candles WHERE " + c + " IS NULL")
        n = cur.fetchone()[0]
        mark = "" if n == 0 else "  <-- !"
        print("{:<22} {:>10}{}".format(c, n, mark))

    # ---------- 7. Дубликаты ----------
    hr("7. DUPLICATES CHECK")
    cur.execute("""
        SELECT COUNT(*) FROM (
            SELECT symbol, market_type, timeframe, open_time
            FROM candles
            GROUP BY symbol, market_type, timeframe, open_time
            HAVING COUNT(*) > 1
        ) t
    """)
    dups = cur.fetchone()[0]
    print("duplicate keys : " + str(dups))
    if dups == 0:
        print("OK — ключ уникален")

    # ---------- 8. Пропуски по 15m ----------
    hr("8. GAPS (15m, BTCUSDT, spot)")
    cur.execute("""
        WITH t AS (
            SELECT open_time,
                   LAG(open_time) OVER (ORDER BY open_time) AS prev
            FROM candles
            WHERE symbol='BTCUSDT'
              AND market_type='spot'
              AND timeframe='15m'
        )
        SELECT COUNT(*) FROM t
        WHERE prev IS NOT NULL
          AND open_time - prev > INTERVAL '15 minutes'
    """)
    gaps = cur.fetchone()[0]
    print("gaps : " + str(gaps))
    if gaps == 0:
        print("OK — пропусков нет")

    # ---------- 9. Последние 10 строк ----------
    hr("9. LAST 10 ROWS")
    cur.execute("""
        SELECT open_time, market_type, open, high, low, close,
               volume, taker_buy_volume, quote_volume, count
        FROM candles
        ORDER BY open_time DESC
        LIMIT 10
    """)
    print("{:<20} {:<8} {:>10} {:>10} {:>10} {:>10}".format(
        "open_time", "market", "open", "high", "low", "close"
    ))
    print("-" * 74)
    for r in cur.fetchall():
        print("{:<20} {:<8} {:>10} {:>10} {:>10} {:>10}".format(
            str(r[0])[:19], str(r[1]),
            str(r[2]), str(r[3]), str(r[4]), str(r[5]),
        ))

    cur.close()
    conn.close()
    hr("DONE")


if __name__ == "__main__":
    report()