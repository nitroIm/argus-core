# crypto/collect/verify_history_binance.py
# v1 - Verify downloaded Binance Vision parquet in vision_out/
# Reports rows, cols, first/last row, missing files.

import sys
import logging
import pandas as pd
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = SCRIPT_DIR / "vision_out"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("verify_binance")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
SUFFIXES = ["spot_1h", "fut_1h", "funding", "metrics"]


def report(sym, suf):
    name = f"{sym}_{suf}"
    p = OUT_DIR / f"{name}.parquet"
    if not p.exists():
        log.warning(f"{name}: MISSING")
        return False
    try:
        df = pd.read_parquet(p)
    except Exception as e:
        log.error(f"{name}: read error {e}")
        return False
    log.info(f"{name}: rows={len(df)} cols={len(df.columns)}")
    log.info(f"  columns={list(df.columns)}")
    if len(df) > 0:
        log.info(f"  first={df.iloc[0].tolist()}")
        log.info(f"  last={df.iloc[-1].tolist()}")
    return True


def main():
    log.info("VERIFY BINANCE v1 start")
    log.info(f"out_dir={OUT_DIR}")
    total = 0
    present = 0
    for sym in SYMBOLS:
        for suf in SUFFIXES:
            total += 1
            if report(sym, suf):
                present += 1
    log.info(f"present={present}/{total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())