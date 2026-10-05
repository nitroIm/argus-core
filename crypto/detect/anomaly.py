# ============================================================
# ARGUS-Trader — DETECT / ANOMALY v4
# ------------------------------------------------------------
# v4: SYMBOLS from env (not config) — config has hardcoded
#     [BTC,ETH], env {SOL,BNB} was ignored.
#     log_anomaly writes to the right DB per symbol
#     (was always DB1, explorer reads DB2 for SOL/BNB).
# v3: symbol_conn — SOL/BNB candles from DB2.
# ============================================================

import os
import sys
import logging
from datetime import (
    datetime, timezone, timedelta,
)
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from config import SYMBOLS as CONFIG_SYMBOLS
from config import TELEGRAM_BOT_TOKEN
from config import TELEGRAM_CHAT_ID
from db import get_connection
from db import close_connection

DB2_OK = False
get_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import (
            get_connection as get_conn_db2,
        )
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.anomaly")

# v4: SYMBOLS from env, fallback to config.
SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or ",".join(CONFIG_SYMBOLS or [])
    ).split(",")
    if s.strip()
]

DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS")
        or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning(
                "db2 conn %s: %s", symbol, e,
            )
    return get_connection()


def _is_db2(symbol):
    return symbol in DB2_SYMBOLS and DB2_OK


PUMP_PCT = 5.0
PUMP_VOL_RATIO = 3.0
DUMP_PCT = -3.0
WASH_VOL_RATIO = 10.0
WASH_RANGE_PCT = 0.5
WICK_PCT = 50.0
CROSS_DIFF_PCT = 1.2


def notify(text):
    if not TELEGRAM_BOT_TOKEN:
        return
    if not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = (
            "https://api.telegram.org/bot"
            + TELEGRAM_BOT_TOKEN
            + "/sendMessage"
        )
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
    except Exception as e:
        log.warning("telegram: " + str(e))


# v4: write anomaly to the right DB per symbol.
def write_anomaly(symbol, ts, atype, sev, details):
    import json as _json
    details_json = _json.dumps(
        details or {},
        ensure_ascii=False,
        default=str,
    )
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO anomaly_log "
                    "(symbol, timestamp, anomaly_type, "
                    "severity, details) "
                    "VALUES (%s,%s,%s,%s,%s) "
                    "RETURNING id",
                    (
                        symbol, ts, atype,
                        sev, details_json,
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else 0
    except Exception as e:
        log.error(
            "write_anomaly %s: %s", symbol, e,
        )
        return 0


def fetch_candles(symbol, hours=48):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc)
                start = start - timedelta(hours=hours)
                sql = (
                    "SELECT timestamp, open, high, "
                    "low, close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "AND timestamp >= %s "
                    "ORDER BY timestamp"
                )
                cur.execute(sql, (symbol, start))
                rows = cur.fetchall()
                result = []
                for r in rows:
                    result.append({
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    })
                return result
    except Exception as e:
        log.error("fetch_candles: " + str(e))
        return []


def fetch_avg_volume(symbol, hours=24):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc)
                start = start - timedelta(hours=hours)
                sql = (
                    "SELECT AVG(volume) FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "AND timestamp >= %s"
                )
                cur.execute(sql, (symbol, start))
                row = cur.fetchone()
                if row and row[0]:
                    return float(row[0])
                return 0
    except Exception:
        return 0


def fetch_cross(symbol, hours=1):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc)
                start = start - timedelta(hours=hours)
                sql = (
                    "SELECT timestamp, diff_pct, "
                    "price_primary, price_secondary "
                    "FROM cross_check "
                    "WHERE symbol = %s "
                    "AND timestamp >= %s "
                    "ORDER BY timestamp DESC LIMIT 5"
                )
                cur.execute(sql, (symbol, start))
                rows = cur.fetchall()
                result = []
                for r in rows:
                    result.append({
                        "timestamp": r[0],
                        "diff_pct": (
                            float(r[1]) if r[1] else 0
                        ),
                        "primary": (
                            float(r[2]) if r[2] else 0
                        ),
                        "secondary": (
                            float(r[3]) if r[3] else 0
                        ),
                    })
                return result
    except Exception:
        return []


def exists(symbol, atype, hours=24):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc)
                start = start - timedelta(hours=hours)
                sql = (
                    "SELECT COUNT(*) FROM anomaly_log "
                    "WHERE symbol = %s "
                    "AND anomaly_type = %s "
                    "AND created_at >= %s"
                )
                cur.execute(sql, (symbol, atype, start))
                return cur.fetchone()[0] > 0
    except Exception:
        return False


def detect_pump_dump(candles, avg_vol):
    if len(candles) < 2:
        return None
    start_idx = max(0, len(candles) - 3)
    for i in range(start_idx, len(candles) - 1):
        c = candles[i]
        if c["open"] == 0:
            continue
        change = (
            c["close"] - c["open"]
        ) / c["open"] * 100
        if change < PUMP_PCT:
            continue
        if avg_vol <= 0:
            continue
        vol_ratio = c["volume"] / avg_vol
        if vol_ratio < PUMP_VOL_RATIO:
            continue
        if i + 1 >= len(candles):
            continue
        nc = candles[i + 1]
        if nc["open"] == 0:
            continue
        nc_change = nc["close"] - nc["open"]
        nc_change = nc_change / nc["open"] * 100
        if nc_change <= DUMP_PCT:
            return {
                "timestamp": c["timestamp"],
                "change_pct": round(change, 2),
                "next_change": round(nc_change, 2),
                "volume_ratio": round(vol_ratio, 2),
            }
    return None


def detect_cross(cross_data):
    for cc in cross_data:
        if abs(cc["diff_pct"]) > CROSS_DIFF_PCT:
            return {
                "timestamp": cc["timestamp"],
                "diff_pct": cc["diff_pct"],
                "primary": cc["primary"],
                "secondary": cc["secondary"],
            }
    return None


def detect_wash(candles, avg_vol):
    if not candles or avg_vol <= 0:
        return None
    for c in candles[-6:]:
        if c["open"] == 0:
            continue
        rng = (
            c["high"] - c["low"]
        ) / c["open"] * 100
        vol_ratio = c["volume"] / avg_vol
        if vol_ratio >= WASH_VOL_RATIO:
            if rng < WASH_RANGE_PCT:
                return {
                    "timestamp": c["timestamp"],
                    "range_pct": round(rng, 3),
                    "volume_ratio": round(
                        vol_ratio, 2,
                    ),
                }
    return None


def detect_stop_hunt(candles):
    if not candles:
        return None
    for c in candles[-3:]:
        rng = c["high"] - c["low"]
        if rng <= 0:
            continue
        up_wick = c["high"] - max(
            c["open"], c["close"]
        )
        low_wick = min(
            c["open"], c["close"]
        ) - c["low"]
        up_pct = up_wick / rng * 100
        low_pct = low_wick / rng * 100
        if up_pct >= WICK_PCT:
            return {
                "timestamp": c["timestamp"],
                "wick_type": "upper",
                "wick_pct": round(up_pct, 1),
                "price": c["close"],
            }
        if low_pct >= WICK_PCT:
            return {
                "timestamp": c["timestamp"],
                "wick_type": "lower",
                "wick_pct": round(low_pct, 1),
                "price": c["close"],
            }
    return None


def process_symbol(symbol):
    log.info("--- " + symbol + " ---")
    candles = fetch_candles(symbol, hours=48)
    if not candles:
        log.warning("   no candles")
        return 0

    avg_vol = fetch_avg_volume(symbol, hours=24)
    log.info("   candles: " + str(len(candles)))
    log.info(
        "   avg_vol: " + str(round(avg_vol, 2))
    )

    found = 0

    pd = detect_pump_dump(candles, avg_vol)
    if pd and not exists(symbol, "pump_dump"):
        write_anomaly(
            symbol, pd["timestamp"],
            "pump_dump", "high", pd,
        )
        msg = "PUMP_AND_DUMP " + symbol
        msg += "\nup " + str(pd["change_pct"]) + "%"
        msg += (
            "\ndown " + str(pd["next_change"]) + "%"
        )
        notify(msg)
        found += 1
        log.info("   pump_dump FOUND")

    cross_data = fetch_cross(symbol, hours=1)
    ce = detect_cross(cross_data)
    if ce and not exists(symbol, "cross_exchange"):
        write_anomaly(
            symbol, ce["timestamp"],
            "cross_exchange", "medium", ce,
        )
        msg = "CROSS_EXCHANGE " + symbol
        msg += "\ndiff " + str(ce["diff_pct"]) + "%"
        notify(msg)
        found += 1
        log.info("   cross_exchange FOUND")

    wt = detect_wash(candles, avg_vol)
    if wt and not exists(symbol, "wash_trading"):
        write_anomaly(
            symbol, wt["timestamp"],
            "wash_trading", "high", wt,
        )
        msg = "WASH_TRADING " + symbol
        msg += (
            "\nvol x" + str(wt["volume_ratio"])
        )
        notify(msg)
        found += 1
        log.info("   wash_trading FOUND")

    sh = detect_stop_hunt(candles)
    if sh and not exists(symbol, "stop_hunting"):
        write_anomaly(
            symbol, sh["timestamp"],
            "stop_hunting", "medium", sh,
        )
        found += 1
        log.info("   stop_hunting FOUND")

    log.info("   total: " + str(found))
    return found


def main():
    log.info("=" * 60)
    log.info("ANOMALY DETECTORS v4")
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        total += process_symbol(symbol)
        log.info("")

    log.info("=" * 60)
    log.info("DONE. found: " + str(total))
    log.info("=" * 60)

    close_connection()
    if DB2_OK:
        try:
            from db2 import (
                close_connection as db2c,
            )
            db2c()
        except Exception:
            pass


if __name__ == "__main__":
    main()