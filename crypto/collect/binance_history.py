# crypto/collect/binance_history.py
# v2 - Binance Vision downloader (SHA-256 verified)
# Spot + Futures klines, fundingRate, metrics for 4 symbols.

import zipfile
import hashlib
import logging
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone, date, timedelta

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = SCRIPT_DIR / "vision_cache"
OUT_DIR = SCRIPT_DIR / "vision_out"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OUT_DIR.mkdir(parents=True, exist_ok=True)

BASE = "https://data.binance.vision"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
START_YEAR = 2020
START_MONTH = 9
INTERVAL = "1h"

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
log = logging.getLogger("vision")


def month_range(start_y, start_m):
    now = datetime.now(timezone.utc)
    y, m = start_y, start_m
    while (y, m) <= (now.year, now.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def build_url(market, dtype, symbol, y, m):
    mm = f"{m:02d}"
    if market == "spot" and dtype == "klines":
        path = f"data/spot/monthly/klines/{symbol}/{INTERVAL}"
        fname = f"{symbol}-{INTERVAL}-{y}-{mm}.zip"
    elif market == "futures" and dtype == "klines":
        path = f"data/futures/um/monthly/klines/{symbol}/{INTERVAL}"
        fname = f"{symbol}-{INTERVAL}-{y}-{mm}.zip"
    elif market == "futures" and dtype == "fundingRate":
        path = f"data/futures/um/monthly/fundingRate/{symbol}"
        fname = f"{symbol}-fundingRate-{y}-{mm}.zip"
    else:
        return None, None
    return f"{BASE}/{path}/{fname}", fname


def build_metrics_url(symbol, d):
    ds = d.strftime("%Y-%m-%d")
    path = f"data/futures/um/daily/metrics/{symbol}"
    fname = f"{symbol}-metrics-{ds}.zip"
    return f"{BASE}/{path}/{fname}", fname


def download_and_verify(url, local_zip):
    checksum_url = url + ".CHECKSUM"
    try:
        r = requests.get(checksum_url, timeout=30)
        if r.status_code != 200:
            log.warning(f"no checksum: {url}")
            return None
        expected = r.text.strip().split()[0]
    except Exception as e:
        log.warning(f"checksum error: {e}")
        return None

    if not local_zip.exists():
        try:
            r = requests.get(url, timeout=300, stream=True)
            if r.status_code != 200:
                log.warning(f"HTTP {r.status_code}: {url}")
                return None
            with open(local_zip, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        except Exception as e:
            log.warning(f"download error: {e}")
            return None

    sha = hashlib.sha256()
    with open(local_zip, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    if sha.hexdigest() != expected:
        log.error(f"SHA256 mismatch: {local_zip.name}")
        local_zip.unlink(missing_ok=True)
        return None
    return local_zip


def read_zip_csv(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            return None
        with z.open(names[0]) as f:
            return pd.read_csv(f, header=None)


def fetch_monthly(symbol, market, dtype):
    frames = []
    for y, m in month_range(START_YEAR, START_MONTH):
        url, fname = build_url(market, dtype, symbol, y, m)
        if not url:
            continue
        local = CACHE_DIR / fname
:
        if not download_and_verify(url, local):
                       continue
        df = read save_zip_csv(local)
(df        if df is not None and len(df) > 0:
            frames.append(df)
   , if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def fetch_metrics_daily(symbol):
    now = datetime.now(timezone.utc).date()
    start = date(START_YEAR, START_MONTH, 1)
    frames = []
    d = start
    while d < now:
        url, fname = build_metrics_url(symbol, d)
        local = CACHE_DIR / fname
        if download_and_verify(url, local):
            df = read_zip_csv(local)
            if df is not None and len(df) > 0:
                frames.append(df)
        d += timedelta(days=1)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def save(df, name):
    out = OUT_DIR / f"{name}.parquet"
    df.to_parquet(out, index=False)
    log.info(f"saved {out.name}: {len(df)} rows")


def main():
    log.info("BINANCE VISION v2 start")
    log.info(f"symbols={SYMBOLS}")

    for sym in SYMBOLS:
        log.info(f"{sym} spot klines")
        df = fetch f_monthly(sym, "spot", "klines")
        if"{ df is not Nonesym}_spot_{INTERVAL}")

        log.info(f"{sym} futures klines")
        df = fetch_monthly(sym, "futures", "klines")
        if df is not None:
            save(df, f"{sym}_fut_{INTERVAL}")

        log.info(f"{sym} fundingRate")
        df = fetch_monthly(sym, "futures", "fundingRate")
        if df is not None:
            save(df, f"{sym}_funding")

        log.info(f"{sym} metrics")
        df = fetch_metrics_daily(sym)
        if df is not None:
            save(df, f"{sym}_metrics")

    log.info("DONE")


if __name__ == "__main__":
    main()