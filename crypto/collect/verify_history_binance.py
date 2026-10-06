# crypto/collect/verify_history.py
# v1 - Verify downloaded Binance Vision parquet files
# Checks: row count, date range, OHLCV sanity, gaps, duplicates.

import sys
import logging
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "vision_out"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("verify")

GAP_TOLERANCE_HOURS = 25


def load(name):
    path = OUT_DIR / f"{name}.parquet"
    if not path.exists():
        log.warning(f"missing: {name}.parquet")
        return None
    return pd.read_parquet(path)


def check_klines(df, name):
    log.info(f"--- {name} ---")
    log.info(f"  rows={len(df)} cols={list(df.columns)}")

    if df.empty:
        log.error("  EMPTY")
        return False

    ts_col = None
    for c in df.columns:
        if "time" in c.lower() or "timestamp" in c.lower():
            ts_col = c
            break
    if ts_col is None:
        log.error("  no timestamp column")
        return False

    df = df.copy()
    df["_dt"] = pd.to_datetime(df[ts_col], unit="ms", utc=True)
    log.info(f"  range: {df['_dt'].min()} .. {df['_dt'].max()}")
    log.info(f"  span_days={ (df['_dt'].max()-df['_dt'].min()).days }")

    if "open" in df.columns:
        o, h, l, c = df["open"], df["high"], df["low"], df["close"]
        bad = ((h < l) | (h < o) | (h < c) | (l > o) | (l > c)).sum()
        log.info(f"  ohlc_violations={bad}")
        if bad > 0:
            return False

    dup = df["_dt"].duplicated().sum()
    log.info(f"  duplicates={dup}")

    df = df.sort_values("_dt")
    diffs = df["_dt"].diff().dropna()
    if len(diffs) > 0:
        med = diffs.median()
        gaps = (diffs > med * GAP_TOLERANCE_HOURS / 24 * 1.5).sum()
        log.info(f"  median_step={med} gaps_gt_2x={gaps}")

    return True


def check_generic(df, name):
    log.info(f"--- {name} ---")
    if df is None or df.empty:
        log.error("  EMPTY")
        return False
    log.info(f"  rows={len(df)} cols={list(df.columns)}")
    return True


def main():
    log.info("=" * 50)
    log.info("VERIFY HISTORY v1")
    log.info("=" * 50)

    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
    ok = True

    for sym in symbols:
        for suffix in ["spot_1h", "fut_1h", "funding", "metrics",
                       "bookDepth", "bookTicker"]:
            name = f"{sym}_{suffix}"
            df = load(name)
            if df is None:
                ok = False
                continue
            if suffix in ("spot_1h", "fut_1h"):
                if not check_klines(df, name):
                    ok = False
            else:
                if not check_generic(df, name):
                    ok = False

    log.info("=" * 50)
    if ok:
        log.info("ALL CHECKS PASSED")
    else:
        log.error("SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())