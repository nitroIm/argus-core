 # ============================================================
# ARGUS — INIT DB GLOBAL CRYPTO SCHEMA
# Path: crypto/binance/schema_global_crypto.py
# ============================================================

import os
import sys
import psycopg

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS global_metrics (
    last_updated        TIMESTAMPTZ PRIMARY KEY,
    btc_dominance       NUMERIC,
    eth_dominance       NUMERIC,
    total_market_cap    NUMERIC,
    total_volume_24h    NUMERIC,
    altcoin_market_cap  NUMERIC,
    stablecoin_market_cap NUMERIC,
    defi_market_cap     NUMERIC
);

CREATE TABLE IF NOT EXISTS btc_network (
    chart_time  TIMESTAMPTZ NOT NULL,
    chart_name  TEXT NOT NULL,
    value       NUMERIC,
    PRIMARY KEY (chart_name, chart_time)
);

CREATE TABLE IF NOT EXISTS stablecoins (
    symbol      TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL,
    name        TEXT,
    circulating NUMERIC,
    price       NUMERIC,
    peg_type    TEXT,
    PRIMARY KEY (symbol, timestamp)
);

CREATE TABLE IF NOT EXISTS fear_greed (
    timestamp      TIMESTAMPTZ PRIMARY KEY,
    value          INTEGER,
    classification TEXT
);
"""

def main():
    if not DB_URL:
        print("ERR: AIVEN_DB_URL not set")
        sys.exit(1)

    conn = psycopg.connect(DB_URL, connect_timeout=15)
    with conn.cursor() as cur:
        cur.execute(SQL_CREATE)
        
        # Проверяем, что таблицы создались
        cur.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_name IN ('global_metrics', 'btc_network', 'stablecoins', 'fear_greed')
        """)
        tables = cur.fetchall()
    conn.commit()
    conn.close()

    print("OK: схема глобальных метрик готова")
    print(f"Создано таблиц: {len(tables)}")
    for t in tables:
        print(f"  - {t[0]}")

if __name__ == "__main__":
    main()