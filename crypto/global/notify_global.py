# ============================================================
# ARGUS-Trader — NOTIFY GLOBAL (узел global, DB2)
# ------------------------------------------------------------
# v4: asia_market -> global_market. +LABELS macro.
#     Renamed from notify_asia.py.
# v3: Kaliningrad time in alerts.
# v2: alerts include forecasts from asia_patterns.
# v1: при |change_pct| > 2% за час → алерт в TG.
# ============================================================

import os
import sys
import logging
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

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

TZ = ZoneInfo("Europe/Kaliningrad")

THRESHOLD = 2.0
RECENT_HOURS = 2
MIN_SAMPLES = 5
MIN_HIT_RATE = 0.55
MAX_FORECASTS = 4

DB1_URL = (os.getenv("ARGUS_DB_URL") or "").strip()
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()
TG_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

LABELS = {
    # Азия
    "NIKKEI":   "Nikkei 225 JP",
    "SHANGHAI": "Shanghai CN",
    "HANGSENG": "Hang Seng HK",
    "USDCNY":   "USD/CNY",
    # Азия-доп
    "USDJPY":   "USD/JPY",
    "KOSPI":    "KOSPI KR",
    "TAIEX":    "TAIEX TW",
    # Европа
    "DAX":      "DAX DE",
    "SX5E":     "Euro Stoxx 50",
    "FTSE":     "FTSE 100 UK",
    "EURUSD":   "EUR/USD",
    # США
    "VIX":      "VIX",
    "NASDAQ":   "NASDAQ",
    "US10Y":    "US 10Y",
    "US30Y":    "US 30Y",
    # Макро
    "DXY":      "DXY",
    "SPX":      "S&P 500",
    "GOLD":     "Gold",
    "BRENT":    "Brent Oil",
    "COPPER":   "Copper",
}

DB1_SYMBOLS = {"BTCUSDT", "ETHUSDT"}
DB2_SYMBOLS = {"SOLUSDT", "BNBUSDT"}

_DB1_CONN = None


def _db1_conn():
    global _DB1_CONN
    if _DB1_CONN is None or _DB1_CONN.closed:
        import psycopg
        _DB1_CONN = psycopg.connect(
            DB1_URL, connect_timeout=15,
        )
    return _DB1_CONN


def _to_local(ts):
    """UTC datetime -> Kaliningrad string."""
    if ts is None:
        return "?"
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(TZ).strftime("%d.%m %H:%M")


def fetch_candidates():
    since = datetime.now(timezone.utc) - timedelta(
        hours=RECENT_HOURS,
    )
    sql = (
        "SELECT symbol, timestamp, close, change_pct "
        "FROM global_market "
        "WHERE ABS(change_pct) > %s "
        "AND timestamp >= %s "
        "ORDER BY timestamp DESC LIMIT 50"
    )
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (THRESHOLD, since))
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


def fetch_patterns(source_symbol, direction, move):
    """Find forecast rules matching this move."""
    sql = (
        "SELECT target_symbol, condition_pct, "
        "lag_hours, samples, hit_rate, avg_impact_pct "
        "FROM asia_patterns "
        "WHERE source_symbol = %s "
        "AND direction = %s "
        "AND samples >= %s "
        "AND hit_rate >= %s "
        "AND condition_pct <= %s "
        "ORDER BY hit_rate DESC, samples DESC "
        "LIMIT 20"
    )
    abs_move = abs(move)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (
                source_symbol, direction,
                MIN_SAMPLES, MIN_HIT_RATE, abs_move,
            ))
            return cur.fetchall()


def fetch_last_price(symbol):
    """Latest close for a crypto symbol."""
    try:
        if symbol in DB1_SYMBOLS:
            if not DB1_URL:
                return None
            conn = _db1_conn()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close FROM candles "
                    "WHERE symbol = %s AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol,),
                )
                row = cur.fetchone()
                return float(row[0]) if row else None
        else:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT close FROM candles "
                        "WHERE symbol = %s "
                        "AND timeframe = '1h' "
                        "ORDER BY timestamp DESC LIMIT 1",
                        (symbol,),
                    )
                    row = cur.fetchone()
                    return float(row[0]) if row else None
    except Exception as e:
        log.warning("price %s: %s", symbol, e)
        return None


def pick_best_forecasts(rows):
    """One forecast per target, best hit_rate."""
    best = {}
    for (tgt, cond, lag, n, hit, avg) in rows:
        cur = best.get(tgt)
        if cur is None or hit > cur["hit"]:
            best[tgt] = {
                "target": tgt,
                "cond": float(cond),
                "lag": int(lag),
                "n": int(n),
                "hit": float(hit),
                "avg": float(avg),
            }
    return list(best.values())[:MAX_FORECASTS]


def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(p, ",.0f")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def fmt_alert(symbol, ts, change, forecasts):
    name = LABELS.get(symbol, symbol)
    arrow = "UP" if change > 0 else "DOWN"
    sign = "+" if change > 0 else ""

    lines = [
        f"[{arrow}] {name}",
        f"Izmenenie: {sign}{change:.2f}% za chas",
        f"Vremya: {_to_local(ts)} KLG",
    ]

    if forecasts:
        lines.append("--------------------")
        lines.append("Prognoz:")
        for f in forecasts:
            price = fetch_last_price(f["target"])
            if price is None:
                price_str = "n/a"
            else:
                forecast_price = price * (
                    1 + f["avg"] / 100
                )
                price_str = (
                    fmt_price(price)
                    + " -> " + fmt_price(forecast_price)
                )
            direction_sign = "+" if f["avg"] > 0 else ""
            lines.append(
                f"{f['target']} через {f['lag']}ч"
            )
            lines.append(
                f"  {direction_sign}{f['avg']:.2f}% "
                f"({price_str})"
            )
            lines.append(
                f"  tochnost {f['hit']*100:.0f}% "
                f"(N={f['n']})"
            )

    return "\n".join(lines)


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


def main():
    log.info("=" * 60)
    log.info(
        "ARGUS NOTIFY GLOBAL v4 — porog %.1f%%, recent %dh",
        THRESHOLD, RECENT_HOURS,
    )
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
    no_patterns = 0

    for symbol, ts, close, change in rows:
        if change is None:
            continue
        if already_sent(symbol, ts):
            skipped += 1
            continue

        direction = "up" if change > 0 else "down"
        try:
            pats = fetch_patterns(
                symbol, direction, float(change),
            )
        except Exception as e:
            log.warning("patterns %s: %s", symbol, e)
            pats = []

        forecasts = pick_best_forecasts(pats)
        if not forecasts:
            no_patterns += 1

        text = fmt_alert(
            symbol, ts, float(change), forecasts,
        )
        ok = send_tg(text)
        if ok:
            mark_sent(
                symbol, ts, float(change), direction,
            )
            sent += 1
            log.info(
                "  sent: %s %.2f%% (%d forecast)",
                symbol, change, len(forecasts),
            )
        else:
            log.warning("  fail: %s %.2f%%", symbol, change)

    log.info("=" * 60)
    log.info(
        "DONE. sent=%d, skipped=%d, no_patterns=%d",
        sent, skipped, no_patterns,
    )
    log.info("=" * 60)

    if _DB1_CONN and not _DB1_CONN.closed:
        _DB1_CONN.close()
    close_connection()


if __name__ == "__main__":
    main()