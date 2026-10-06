# crypto/collect/binance_history.py
# v2 - Binance Vision downloader (SHA-256 verified)
# Spot + Futures: klines, fundingRate, metrics, bookDepth, bookTicker
# for BTC/ETH/SOL/BNB. No API key. Saves parquet.

import os
import io
import zipfile
import hashlib
import logging
import requests
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

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


def day_range(start_y, start_m, end_y, end_m):
    from datetime import date, timedelta
    d = date(start_y, start_m, 1)
    end = date(end_y, end_m, 1)
    while d < end:
        yield d
        d += timedelta(days=1)


def build_url(market, dtype, symbol, y, m, interval=None):
    mm = f"{m:02d}"
    if market == "spot":
        if dtype == "klines":
            path = f"data/spot/monthly/klines/{symbol}/{interval}"
            fname = f"{symbol}-{interval}-{y}-{mm}.zip"
        elif dtype == "bookDepth":
            path = f"data/spot/monthly/bookDepth/{symbol}"
            fname = f"{symbol}-bookDepth-{y}-{mm}.zip"
        elif dtype == "bookTicker":
            path = f"data/spot/monthly/bookTicker/{symbol}"
            fname = f"{symbol}-bookTicker-{y}-{mm}.zip"
        else:
            return None, None
    elif market == "futures":
        if dtype == "klines":
            path = f"data/futures/um/monthly/klines/{symbol}/{interval}"
            fname = f"{symbol}-{interval}-{y}-{mm}.zip"
        elif dtype == "fundingRate":
            path = f"data/futures/um/monthly/fundingRate/{symbol}"
            fname = f"{symbol}-fundingRate-{y}-{mm}.zip"
        elif dtype == "metrics":
            path = f"data/futures/um/daily/metrics/{symbol}"
            fname = f"{symbol}-metrics-{y}-{mm}-"
        else:
            return None, None
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
            log.warning(f"no checksum: {checksum_url}")
            return None
        expected = r.text.strip().split()[0]
    except Exception as e:
        log.warning(f"checksum fetch failed: {e}")
        return None

    if local_zip.exists():
        log.info(f"cached: {local_zip.name}")
    else:
        log.info(f"downloading: {url}")
        try:
            r = requests.get(url, timeout=300, stream=True)
            if r.status_code != 200:
                log.warning(f"HTTP {r.status_code}: {url}")
                return None
            with open(local_zip, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        except Exception as e:
            log.warning(f"download failed: {e}")
            return None

    sha = hashlib.sha256()
    with open(local_zip, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    actual = sha.hexdigest()
    if actual != expected:
        log.error(f"SHA256 mismatch: {local_zip.name}")
        local_zip.unlink(missing_ok=True)
        return None
    log.info(f"verified: {local_zip.name}")
    return local_zip


def read_zip_csv(zip_path, header=None):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if not names:
            return None
        with z.open(names[0]) as f:
            df = pd.read_csv(f, header=header)
        return df


def fetch_monthly(symbol, market, dtype, interval=None):
    frames = []
    for y, m in month_range(START_YEAR, START_MONTH):
        url, fname = build_url(market, dtype, symbol, y, m, interval)
        if not url:
            continue
        local = CACHE_DIR / fname
        if not download_and_verify(url, local):
            continue
        df = read_zip_csv(local)
        if df is not None and len(df) > 0:
            frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def fetch_metrics_daily(symbol):
    from datetime import date, timedelta
    now = datetime.now(timezone.utc).date()
    start = date(2020, 9, 1)
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
    log.info("=" * 50)
    log.info("BINANCE VISION HISTORY v2")
    log.info(f"symbols={SYMBOLS}")
    log.info(f"out={OUT_DIR}")
    log.info("=" * 50)

    for sym in SYMBOLS:
        log.info(f"--- {sym} spot {INTERVAL} ---")
        df = fetch_monthly(sym, "spot", "klines", INTERVAL)
        if df is not None:
            save(df, f"{sym}_spot_{INTERVAL}")

        log.info(f"--- {sym} futures {INTERVAL} ---")
        df = fetch_monthly(sym, "futures", "klines", INTERVAL)
        if df is not None:
            save(df, f"{sym}_fut_{INTERVAL}")

        log.info(f"--- {sym} fundingRate ---")
        df = fetch_monthly(sym, "futures", "fundingRate")
        if df is not None:
            save(df, f"{sym}_funding")

        log.info(f"--- {sym} metrics (daily) ---")
        df = fetch_metrics_daily(sym)
        if df is not None:
            save(df, f"{sym}_metrics")

        log.info(f"--- {sym} spot bookDepth ---")
        df = fetch_monthly(sym, "spot", "bookDepth")
        if df is not None:
            save(df, f"{sym}_bookDepth")

        log.info(f"--- {sym} spot bookTicker ---")
        df = fetch_monthly(sym, "spot", "bookTicker")
        if df is not None:
            save(df, f"{sym}_bookTicker")

    log.info("DONE")


if __name__ == "__main__":
    main()