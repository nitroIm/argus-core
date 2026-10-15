# ============================================================
# ARGUS — INIT DB (create candles table)
# Path: crypto/binance/init_db.py
# ============================================================

import os
import sys
import psycopg

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS candles (
    id               BIGSERIAL PRIMARY KEY,
    symbol           TEXT        NOT NULL,
    market_type      TEXT        NOT NULL,
    timeframe        TEXT        NOT NULL,
    open_time        TIMESTAMPTZ NOT NULL,
    open             NUMERIC     NOT NULL,
    high             NUMERIC     NOT NULL,
    low              NUMERIC     NOT NULL,
    close            NUMERIC     NOT NULL,
    volume           NUMERIC     NOT NULL,
    taker_buy_volume NUMERIC,
    quote_volume     NUMERIC,
    count            INTEGER,
    source           TEXT        NOT NULL DEFAULT 'binance',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT candles_unique
        UNIQUE (symbol, market_type, timeframe, open_time)
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON candles (symbol, market_type, timeframe, open_time DESC);
"""


def main():
    if not DB_URL:
        print("ERR: AIVEN_DB_URL not set")
        sys.exit(1)

    conn = psycopg.connect(DB_URL, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute(SQL_CREATE)
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'candles'
            ORDER BY ordinal_position;
        """)
        cols = cur.fetchall()
    conn.commit()
    conn.close()

    print("OK: таблица candles готова")
    print(f"Колонок: {len(cols)}")
    for name, dtype in cols:
        print(f"  - {name}: {dtype}")


if __name__ == "__main__":
    main()