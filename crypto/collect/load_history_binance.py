# ============================================================
# ARGUS — LOAD BINANCE VISION HISTORY
# ------------------------------------------------------------
# v2: preflight check of 16 parquet files.
#     commit after each batch (idle timeout guard).
# v1: one-shot backfill from crypto/collect/vision_out/.
#     Routes BTC/ETH -> DB1, SOL/BNB -> DB2.
#     ON CONFLICT DO NOTHING everywhere.
#     Handles ms/us timestamp mix in klines.
# ============================================================

import sys
import logging
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection as db1_conn
from db2 import get_connection as db2_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("load_history")

VISION_DIR = SCRIPT_DIR / "vision_out"
DB1_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
DB2_SYMBOLS = ["SOLUSDT", "BNBUSDT"]
ALL_SYMBOLS = DB1_SYMBOLS + DB2_SYMBOLS
SOURCE = "binance_vision"
BATCH = 500

EXPECTED = []
for sym in ALL_SYMBOLS:
    EXPECTED += [
        f"{sym}_spot_1h.parquet",
        f"{sym}_fut_1h.parquet",
        f"{sym}_funding.parquet",
        f"{sym}_metrics.parquet",
    ]

SQL_CANDLES = """
    INSERT INTO candles
      (symbol, market_type, timeframe, timestamp,
       open, high, low, close, volume, source)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON CONFLICT (symbol, market_type,
                 timeframe, timestamp) DO NOTHING
"""

SQL_FUNDING = """
    INSERT INTO funding_rates
      (symbol, timestamp, rate, source)
    VALUES (%s,%s,%s,%s)
    ON CONFLICT (symbol, timestamp) DO NOTHING
"""

SQL_OI = """
    INSERT INTO open_interest
      (symbol, timestamp, oi, oi_value, source)
    VALUES (%s,%s,%s,%s,%s)
    ON CONFLICT (symbol, timestamp) DO NOTHING
"""

SQL_LS = """
    INSERT INTO long_short_ratio
      (symbol, timestamp, ls_ratio,
       long_pct, short_pct, source)
    VALUES (%s,%s,%s,%s,%s,%s)
    ON CONFLICT (symbol, timestamp) DO NOTHING
"""


def preflight():
    log.info("preflight: checking %d files",
             len(EXPECTED))
    missing = []
    for name in EXPECTED:
        p = VISION_DIR / name
        if not p.exists():
            missing.append(name)
            log.warning(f"  MISSING: {name}")
        else:
            size = p.stat().st_size
            log.info(f"  OK: {name} ({size:,} bytes)")
    if missing:
        log.error(f"missing {len(missing)} files, "
                  f"abort")
        return False
    log.info("preflight: all 16 present")
    return True


def read_parquet(name):
    p = VISION_DIR / f"{name}.parquet"
    if not p.exists():
        log.warning(f"missing {p.name}")
        return None
    return pd.read_parquet(p)


def norm_ms(v):
    """Normalize ms/us timestamp to ms."""
    try:
        v = int(v)
    except Exception:
        return None
    if v <= 0:
        return None
    if v > 10 ** 14:
        return v // 1000
    return v


def ms_to_dt(ms):
    return pd.Timestamp(
        ms, unit="ms", tz="UTC"
    ).to_pydatetime()


def insert_batch(conn, sql, rows, label):
    if not rows:
        log.info(f"{label}: nothing to insert")
        return 0
    added = 0
    total = len(rows)
    with conn.cursor() as cur:
        for i in range(0, total, BATCH):
            chunk = rows[i:i + BATCH]
            try:
                cur.executemany(sql, chunk)
                added += cur.rowcount or 0
                conn.commit()
            except Exception as e:
                log.warning(
                    f"{label} batch {i}: "
                    f"{str(e)[:80]}"
                )
                try:
                    conn.rollback()
                except Exception:
                    pass
    log.info(
        f"{label}: attempted={total} added={added}"
    )
    return added


def load_candles(conn, sym, market, mtype):
    df = read_parquet(f"{sym}_{market}_1h")
    if df is None:
        return 0
    rows = []
    for _, r in df.iterrows():
        ts = norm_ms(r["open_time"])
        if ts is None:
            continue
        rows.append((
            sym, mtype, "1h", ms_to_dt(ts),
            float(r["open"]),
            float(r["high"]),
            float(r["low"]),
            float(r["close"]),
            float(r["volume"]),
            SOURCE,
        ))
    label = f"candles[{sym}/{mtype}]"
    return insert_batch(conn, SQL_CANDLES, rows, label)


def load_funding(conn, sym):
    df = read_parquet(f"{sym}_funding")
    if df is None:
        return 0
    rows = []
    for _, r in df.iterrows():
        ts = norm_ms(r["calc_time"])
        if ts is None:
            continue
        rows.append((
            sym, ms_to_dt(ts),
            float(r["last_funding_rate"]),
            SOURCE,
        ))
    return insert_batch(
        conn, SQL_FUNDING, rows,
        f"funding[{sym}]",
    )


def load_metrics(conn, sym):
    df = read_parquet(f"{sym}_metrics")
    if df is None:
        return 0, 0
    df = df.copy()
    df["ts"] = pd.to_datetime(
        df["create_time"], utc=True
    )
    df = df.sort_values("ts")
    df = df.set_index("ts")
    agg = df.resample("1h").last()
    agg = agg.dropna(subset=["sum_open_interest"])
    agg = agg.reset_index()

    oi_rows = []
    ls_rows = []
    for _, r in agg.iterrows():
        t = r["ts"].to_pydatetime()
        oi = r["sum_open_interest"]
        ov = r["sum_open_interest_value"]
        if pd.notna(oi):
            oi_rows.append((
                sym, t, float(oi),
                float(ov) if pd.notna(ov) else None,
                SOURCE,
            ))
        lsr = r["count_long_short_ratio"]
        if pd.notna(lsr):
            ls_rows.append((
                sym, t, float(lsr),
                None, None, SOURCE,
            ))

    n_oi = insert_batch(
        conn, SQL_OI, oi_rows,
        f"open_interest[{sym}]",
    )
    n_ls = insert_batch(
        conn, SQL_LS, ls_rows,
        f"long_short_ratio[{sym}]",
    )
    return n_oi, n_ls


def load_symbol(conn, sym):
    log.info(f"=== {sym} ===")
    load_candles(conn, sym, "spot", "spot")
    load_candles(conn, sym, "fut", "futures")
    load_funding(conn, sym)
    load_metrics(conn, sym)


def main():
    log.info("=" * 60)
    log.info("LOAD BINANCE VISION HISTORY v2")
    log.info(f"vision_dir={VISION_DIR}")
    log.info(f"db1={DB1_SYMBOLS}")
    log.info(f"db2={DB2_SYMBOLS}")
    log.info("=" * 60)

    if not preflight():
        sys.exit(1)

    log.info("--- DB1 ---")
    with db1_conn() as c1:
        for sym in DB1_SYMBOLS:
            load_symbol(c1, sym)

    log.info("--- DB2 ---")
    with db2_conn() as c2:
        for sym in DB2_SYMBOLS:
            load_symbol(c2, sym)

    log.info("=" * 60)
    log.info("DONE")
    log.info("=" * 60)


if __name__ == "__main__":
    main()