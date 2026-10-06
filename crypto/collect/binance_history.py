# crypto/collect/binance_history.py
# v6 - accept files without checksum (current month),
#      fix metrics last-day skip, strip UTF-8 BOM,
#      strict column-count validation.
# Downloads spot+futures klines, fundingRate, metrics
# for BTC/ETH/SOL/BNB. Saves parquet to vision_out/.

import io
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

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume",
    "close_time", "quote_volume", "count",
    "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]
FUNDING_COLS = [
    "calc_time", "funding_interval_hours", "last_funding_rate",
]
METRICS_COLS = [
    "create_time", "symbol", "sum_open_interest",
    "sum_open_interest_value",
    "count_toptrader_long_short_ratio",
    "sum_toptrader_long_short_ratio",
    "count_long_short_ratio",
    "sum_taker_long_short_vol_ratio",
]

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


def cache_path(market, fname):
    return CACHE_DIR / f"{market}_{fname}"


def fetch_checksum(url):
    try:
        r = requests.get(url + ".CHECKSUM", timeout=30)
        if r.status_code == 200:
            return r.text.strip().split()[0]
    except Exception as e:
        log.warning(f"checksum fetch error: {e}")
    return None


def download_and_verify(url, local_zip):
    expected = fetch_checksum(url)
    if expected is None:
        log.warning(f"no checksum, accept as-is: {url}")

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

    if expected is not None:
        sha = hashlib.sha256()
        with open(local_zip, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                sha.update(chunk)
        if sha.hexdigest() != expected:
            log.error(f"SHA256 mismatch: {local_zip.name}")
            local_zip.unlink(missing_ok=True)
            return None
    return local_zip


def read_zip_csv(zip_path, cols):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist()
                 if n.endswith(".csv")]
        if not names:
            return None
        with z.open(names[0]) as f:
            raw = f.read().decode("utf-8-sig",
                                  errors="replace")
    lines = raw.splitlines()
    skip = 0
    if lines and lines[0].startswith(cols[0]):
        skip = 1
    buf = io.StringIO("\n".join(lines))
    df = pd.read_csv(buf, header=None, skiprows=skip)
    if len(df.columns) != len(cols):
        log.warning(f"{zip_path.name}: expected "
                    f"{len(cols)} cols, got "
                    f"{len(df.columns)}")
        return None
    df.columns = cols
    return df


def fetch_monthly(symbol, market, dtype, cols):
    frames = []
    for y, m in month_range(START_YEAR, START_MONTH):
        url, fname = build_url(market, dtype, symbol, y, m)
        if not url:
            continue
        local = cache_path(market, fname)
        if not download_and_verify(url, local):
            continue
        df = read_zip_csv(local, cols)
        if df is not None and len(df) > 0:
            frames.append(df)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def fetch_metrics_daily(symbol):
    now = datetime.now(timezone.utc).date()
    start = date(START_YEAR, START_MONTH, 1)
    frames = []
    d = start
    while d <= now:
        url, fname = build_metrics_url(symbol, d)
        local = cache_path("futures", fname)
        if download_and_verify(url, local):
            df = read_zip_csv(local, METRICS_COLS)
            if df is not None and len(df) > 0:
                frames.append(df)
        d += timedelta(days=1)
    if not frames:
        return None
    return pd.concat(frames, ignore_index=True)


def save(df, name):
    out = OUT_DIR / f"{name}.parquet"
    df.to_parquet(out, index=False)
    log.info(f"saved {out.name} rows={len(df)}")


def main():
    log.info("BINANCE VISION v6 start")
    log.info(f"symbols={SYMBOLS}")

    for sym in SYMBOLS:
        log.info(f"{sym} spot klines")
        df = fetch_monthly(sym, "spot", "klines", KLINE_COLS)
        if df is not None:
            save(df, f"{sym}_spot_{INTERVAL}")

        log.info(f"{sym} futures klines")
        df = fetch_monthly(sym, "futures", "klines",
                           KLINE_COLS)
        if df is not None:
            save(df, f"{sym}_fut_{INTERVAL}")

        log.info(f"{sym} fundingRate")
        df = fetch_monthly(sym, "futures", "fundingRate",
                           FUNDING_COLS)
        if df is not None:
            save(df, f"{sym}_funding")

        log.info(f"{sym} metrics daily")
        df = fetch_metrics_daily(sym)
        if df is not None:
            save(df, f"{sym}_metrics")

    log.info("DONE")


if __name__ == "__main__":
    main()