# ============================================================
# ARGUS-Trader — NOTIFY ASIA + EUROPE (узел global)
# ------------------------------------------------------------
# v1: при |change_pct| > 2% за час → алерт в TG.
# ============================================================

import os
import sys
import logging
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))
sys.path.insert(0, str(SCRIPT_DIR))

from db2 import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("global.notify")
THRESHOLD = 2.0
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
LABELS = {
    "NIKKEI":   "Nikkei 225 JP",
    "SHANGHAI": "Shanghai CN",
    "HANGSENG": "Hang Seng HK",
    "USDCNY":   "USD/CNY",
    "DAX":      "DAX DE",
    "SX5E":     "Euro Stoxx 50",
    "FTSE":     "FTSE 100 UK",
    "EURUSD":   "EUR/USD",
    "VIX":      "VIX",
    "NASDAQ":   "NASDAQ",
    "US10Y":    "US 10Y",
    "USDJPY":   "USD/JPY",
    "KOSPI":    "KOSPI KR",
    "TAIEX":    "TAIEX TW",
}

def fetch_candidates():
    sql = (
        "SELECT symbol, timestamp, close, "
        "change_pct FROM asia_market "
        "WHERE ABS(change_pct) > %s "
        "ORDER BY timestamp DESC LIMIT 50"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (THRESHOLD,))
            return cur.fetchall()

def already_sent(symbol, ts):
    sql = (
        "SELECT 1 FROM asia_alerts "
        "WHERE symbol = %s AND timestamp = %s "
        "LIMIT 1"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, ts))
            return cur.fetchone() is not None

def mark_sent(symbol, ts, change, direction):
    sql = (
        "INSERT INTO asia_alerts "
        "(symbol, timestamp, change_pct, direction) "
        "VALUES (%s,%s,%s,%s)"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (symbol, ts, change, direction))

def send_tg(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TG not configured")
        return False
    try:
        r = requests.post(
            TG_URL,
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("TG: HTTP %d", r.status_code)
            return False
        return True
    except Exception as e:
        log.error("TG: %s", e)
        return False

def fmt_alert(symbol, ts, change):
    name = LABELS.get(symbol, symbol)
    arrow = "UP" if change > 0 else "DOWN"
    sign = "+" if change > 0 else ""
    return (
        f"[{arrow}] {name}\n"
        f"Izmenenie: {sign}{change:.2f}% za chas\n"
        f"Vremya (UTC): {ts.strftime('%d.%m %H:%M')}"
    )

def main():
    log.info("=" * 60)
    log.info("ARGUS NOTIFY - porog %.1f%%", THRESHOLD)
    log.info("=" * 60)

    try:
        rows = fetch_candidates()
    except Exception as e:
        log.error("fetch: %s", e)
        close_connection()
        return

    log.info("Kandidatov: %d", len(rows))
    sent = 0
    skipped = 0

    for symbol, ts, close, change in rows:
        if change is None:
            continue
        if already_sent(symbol, ts):
            skipped += 1
            continue

        text = fmt_alert(symbol, ts, float(change))
        ok = send_tg(text)
        if ok:
            mark_sent(
                symbol, ts, float(change),
                "up" if change > 0 else "down",
            )
            sent += 1
            log.info("  sent: %s %.2f%%", symbol, change)
        else:
            log.warning("  fail: %s %.2f%%", symbol, change)

    log.info("=" * 60)
    log.info("DONE. sent=%d, skipped=%d", sent, skipped)
    log.info("=" * 60)

    close_connection()

if __name__ == "__main__":
    main()