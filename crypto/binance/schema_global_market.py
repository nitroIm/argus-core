# ============================================================
# ARGUS - SCHEMA GLOBAL MARKETS (v1.0.1)
# Path: crypto/binance/schema_global_markets.py
# ============================================================

import os
import sys
import psycopg

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

SQL_CREATE = """
CREATE TABLE IF NOT EXISTS market_indices (
    ticker      TEXT NOT NULL,
    name        TEXT,
    region      TEXT,
    country     TEXT,
    timezone    TEXT,
    open_time   TIMESTAMPTZ NOT NULL,
    open        NUMERIC,
    high        NUMERIC,
    low         NUMERIC,
    close       NUMERIC,
    volume      NUMERIC,
    is_open     BOOLEAN,
    PRIMARY KEY (ticker, open_time)
);

CREATE TABLE IF NOT EXISTS market_hours (
    ticker          TEXT PRIMARY KEY,
    region          TEXT,
    timezone        TEXT,
    open_utc        TEXT,
    close_utc       TEXT,
    lunch_start_utc TEXT,
    lunch_end_utc   TEXT,
    trading_days    TEXT
);
"""

# Данные для таблицы market_hours (20 инструментов)
MARKET_HOURS_DATA = [
    # АЗИЯ
    ("^N225", "Asia", "Asia/Tokyo", "00:00", "06:00", "02:30", "03:30", "1-5"), # Nikkei 225
    ("^HSI", "Asia", "Asia/Hong_Kong", "01:30", "08:00", "04:00", "05:00", "1-5"), # Hang Seng
    ("000001.SS", "Asia", "Asia/Shanghai", "01:30", "07:00", "03:30", "05:00", "1-5"), # Shanghai Composite
    ("399001.SZ", "Asia", "Asia/Shanghai", "01:30", "07:00", "03:30", "05:00", "1-5"), # Shenzhen Component
    ("^KS11", "Asia", "Asia/Seoul", "00:00", "06:30", "02:40", "03:30", "1-5"), # KOSPI
    ("^TWII", "Asia", "Asia/Taipei", "01:00", "05:30", None, None, "1-5"), # TAIEX
    ("^STI", "Asia", "Asia/Singapore", "01:00", "09:00", "04:00", "05:00", "1-5"), # Straits Times
    ("^JKSE", "Asia", "Asia/Jakarta", "02:00", "08:50", "05:00", "06:30", "1-5"), # IDX Composite
    ("^KLSE", "Asia", "Asia/Kuala_Lumpur", "01:00", "09:00", "04:30", "06:30", "1-5"), # KLCI
    ("^SET.BK", "Asia", "Asia/Bangkok", "02:30", "09:30", "05:30", "07:00", "1-5"), # SET Index
    ("^BSESN", "Asia", "Asia/Kolkata", "03:45", "10:00", None, None, "1-5"), # BSE Sensex

    # ЕВРОПА (Добавлено)
    ("^GDAXI", "Europe", "Europe/Berlin", "08:00", "16:30", None, None, "1-5"), # DAX
    ("^FTSE", "Europe", "Europe/London", "08:00", "16:30", None, None, "1-5"), # FTSE 100
    ("^FCHI", "Europe", "Europe/Paris", "08:00", "16:30", None, None, "1-5"), # CAC 40
    
    # США
    ("^GSPC", "US", "America/New_York", "14:30", "21:00", None, None, "1-5"), # S&P 500 (лето)
    ("^IXIC", "US", "America/New_York", "14:30", "21:00", None, None, "1-5"), # NASDAQ (лето)
    ("^VIX", "US", "America/New_York", "14:30", "21:00", None, None, "1-5"), # VIX (лето)
    
    # ГЛОБАЛЬНЫЕ
    ("DX-Y.NYB", "Global", "America/New_York", "00:00", "21:00", None, None, "1-5"), # DXY
    ("GC=F", "Global", "America/New_York", "23:00", "22:00", "21:00", "22:00", "1-5"), # Gold
    ("CL=F", "Global", "America/New_York", "23:00", "22:00", "21:00", "22:00", "1-5"), # Oil
]

def main():
    if not DB_URL:
        print("ERR: AIVEN_DB_URL not set")
        sys.exit(1)

    print("Connecting to DB...")
    conn = psycopg.connect(DB_URL, connect_timeout=15)
    
    with conn.cursor() as cur:
        print("Creating tables (if not exists)...")
        cur.execute(SQL_CREATE)
        
        print("Updating market_hours (adding missing Europe)...")
        for row in MARKET_HOURS_DATA:
            # Используем ON CONFLICT DO UPDATE, чтобы ничего не удалять, а только обновлять/добавлять
            cur.execute("""
                INSERT INTO market_hours (ticker, region, timezone, open_utc, close_utc, lunch_start_utc, lunch_end_utc, trading_days)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker) DO UPDATE SET
                    region = EXCLUDED.region,
                    timezone = EXCLUDED.timezone,
                    open_utc = EXCLUDED.open_utc,
                    close_utc = EXCLUDED.close_utc,
                    lunch_start_utc = EXCLUDED.lunch_start_utc,
                    lunch_end_utc = EXCLUDED.lunch_end_utc,
                    trading_days = EXCLUDED.trading_days
            """, row)
            
        # Проверка
        cur.execute("SELECT COUNT(*) FROM market_hours")
        count = cur.fetchone()[0]
        
    conn.commit()
    conn.close()

    print(f"OK: схема глобальных рынков обновлена")
    print(f"Всего торговых расписаний в базе: {count}")

if __name__ == "__main__":
    main()