# ============================================================
# ARGUS-Trader - MACRO COLLECTOR [PRODUCTION]
# ------------------------------------------------------------
# 10Y Treasury Yield (^TNX) с Yahoo Finance.
# Без ключа, раз в час.
# ============================================================

import sys
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from db import get_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.macro")

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}

# symbol -> (yahoo_code, db_symbol)
MACRO = [
    ("^TNX", "US10Y"),
]


def fetch_yahoo(code, interval="1h", rng="2d"):
    try:
        r = requests.get(
            YAHOO.format(sym=code),
            params={"interval": interval, "range": rng},
            headers=HEADERS, timeout=15,
        )
        if r.status_code != 200:
            log.warning("%s: HTTP %d", code, r.status_code)
            return []
        data = r.json()
    except Exception as e:
        log.error("%s: %s", code, e)
        return []

    try:
        result = data["chart"]["result"][0]
        tss = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except Exception as e:
        log.error("%s parse: %s", code, e)
        return []

    out = []
    for ts, c in zip(tss, closes):
        if c is None:
            continue
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            out.append((dt, float(c)))
        except Exception:
            continue
    return out[-48:]


def compute_changes(rows):
    out = []
    prev = None
    for ts, c in rows:
        ch = None
        if prev is not None and prev > 0:
            ch = (c - prev) / prev * 100
        out.append((ts, c, ch))
        prev = c
    return out


def save_rows(db_symbol, rows):
    if not rows:
        return 0
    sql = (
        "INSERT INTO macro_metrics "
        "(symbol, timestamp, close, change_pct, source) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (symbol, timestamp) DO UPDATE SET "
        "close = EXCLUDED.close, "
        "change_pct = EXCLUDED.change_pct"
    )
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for ts, c, ch in rows:
                    try:
                        cur.execute(sql, (
                            db_symbol, ts, c, ch,
                            "yahoo",
                        ))
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning("skip: %s", e)
    except Exception as e:
        log.error("save %s: %s", db_symbol, e)
    return added


def collect_macro():
    total = 0
    for code, sym in MACRO:
        rows = fetch_yahoo(code)
        if not rows:
            log.warning("%s: no data", sym)
            continue
        rows = compute_changes(rows)
        added = save_rows(sym, rows)
        total += added
        log.info(
            "[macro/%s] fetched=%d added=%d",
            sym, len(rows), added,
        )
    return total


if __name__ == "__main__":
    log.info("=" * 50)
    log.info("MACRO test")
    log.info("=" * 50)
    n = collect_macro()
    log.info("done, added=%d", n)