# ============================================================
# ARGUS-Trader — EVENTS v5
# ------------------------------------------------------------
# v5: market_type='futures' in fetch_data.
#     English logs (no emoji).
# v4: DB routing via symbol_conn.
# ============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from config import SYMBOLS as CONFIG_SYMBOLS
from db import get_connection, close_connection

DB2_OK = False
get_conn_db2 = None
close_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import get_connection as get_conn_db2
        from db2 import close_connection as close_conn_db2
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

DATA_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_FILE = DATA_DIR / "events_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.events")

DEFAULT_SYMBOLS = (
    list(CONFIG_SYMBOLS) if CONFIG_SYMBOLS
    else ["BTCUSDT", "ETHUSDT"]
)
SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if s.strip()
]
DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS") or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning("db2 conn %s: %s", symbol, e)
    return get_connection()


THRESHOLD_WINDOW_DAYS = 30
THRESHOLD_LOOKBACK_HOURS = THRESHOLD_WINDOW_DAYS * 24

PCT_MOVE = 90
PCT_VOLUME = 90
PCT_FUNDING = 90
PCT_OI = 90
PCT_LS_HIGH = 85
PCT_LS_LOW = 15
PCT_RSI_HIGH = 85
PCT_RSI_LOW = 15

MIN_SAMPLES_FOR_QUANTILE = 100

FALLBACK_RISE_1H = 1.5
FALLBACK_RISE_4H = 3.0
FALLBACK_VOLUME = 3.0
FALLBACK_FUNDING = 0.05
FALLBACK_OI = 5.0
FALLBACK_LS_HIGH = 1.5
FALLBACK_LS_LOW = 0.7
FALLBACK_RSI_HIGH = 75
FALLBACK_RSI_LOW = 25

RSI_PERIOD = 14


def percentile(values, p):
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def fetch_data(symbol, limit=2000):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "AND market_type = 'futures' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                candles_rows = list(reversed(cur.fetchall()))

                cur.execute(
                    "SELECT timestamp, change_pct, volume_ratio_24h, "
                    "funding_rate, oi_change_pct, ls_ratio "
                    "FROM features_hourly WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                features_rows = list(reversed(cur.fetchall()))

        fmap = {}
        for r in features_rows:
            fmap[r[0]] = {
                "change_pct": float(r[1]) if r[1] is not None else 0,
                "volume_ratio": float(r[2]) if r[2] is not None else 0,
                "funding_rate": float(r[3]) if r[3] is not None else None,
                "oi_change_pct": float(r[4]) if r[4] is not None else None,
                "ls_ratio": float(r[5]) if r[5] is not None else None,
            }

        result = []
        for r in candles_rows:
            ts = r[0]
            f = fmap.get(ts, {})
            result.append({
                "timestamp": ts,
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": float(r[5]),
                "change_pct": f.get("change_pct", 0),
                "volume_ratio": f.get("volume_ratio", 0),
                "funding_rate": f.get("funding_rate"),
                "oi_change_pct": f.get("oi_change_pct"),
                "ls_ratio": f.get("ls_ratio"),
            })
        return result
    except Exception as e:
        log.error("fetch_data: %s", e)
        return []


def compute_rsi_series(closes, period=RSI_PERIOD):
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out

    gains = []
    losses = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100 - 100 / (1 + rs)

    for i in range(period + 1, n):
        idx = i - 1
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - 100 / (1 + rs)
    return out


def compute_thresholds(data):
    if len(data) < MIN_SAMPLES_FOR_QUANTILE:
        log.warning(
            "   few data (%d < %d), fallback thresholds",
            len(data), MIN_SAMPLES_FOR_QUANTILE,
        )
        return {
            "source": "fallback",
            "samples": len(data),
            "rise_1h_pct": FALLBACK_RISE_1H,
            "rise_4h_pct": FALLBACK_RISE_4H,
            "volume_ratio": FALLBACK_VOLUME,
            "funding_pct": FALLBACK_FUNDING,
            "oi_pct": FALLBACK_OI,
            "ls_high": FALLBACK_LS_HIGH,
            "ls_low": FALLBACK_LS_LOW,
            "rsi_high": FALLBACK_RSI_HIGH,
            "rsi_low": FALLBACK_RSI_LOW,
        }

    changes = [abs(d["change_pct"]) for d in data]
    changes = [c for c in changes if c > 0]

    sums_4h = []
    for i in range(3, len(data)):
        s = sum(data[j]["change_pct"] for j in range(i - 3, i + 1))
        sums_4h.append(abs(s))
    sums_4h = [s for s in sums_4h if s > 0]

    volumes = [d["volume_ratio"] for d in data if d["volume_ratio"]]
    fundings = [
        abs(d["funding_rate"]) for d in data
        if d["funding_rate"] is not None
    ]
    ois = [
        abs(d["oi_change_pct"]) for d in data
        if d["oi_change_pct"] is not None
    ]
    lsr = [
        d["ls_ratio"] for d in data
        if d["ls_ratio"] is not None
    ]

    closes_all = [d["close"] for d in data]
    rsi_all = compute_rsi_series(closes_all, RSI_PERIOD)
    rsi_valid = [r for r in rsi_all if r is not None]

    return {
        "source": "quantile",
        "samples": len(data),
        "rise_1h_pct": round(
            percentile(changes, PCT_MOVE), 4
        ) if changes else FALLBACK_RISE_1H,
        "rise_4h_pct": round(
            percentile(sums_4h, PCT_MOVE), 4
        ) if sums_4h else FALLBACK_RISE_4H,
        "volume_ratio": round(
            percentile(volumes, PCT_VOLUME), 4
        ) if volumes else FALLBACK_VOLUME,
        "funding_pct": round(
            percentile(fundings, PCT_FUNDING), 6
        ) if fundings else FALLBACK_FUNDING,
        "oi_pct": round(
            percentile(ois, PCT_OI), 4
        ) if ois else FALLBACK_OI,
        "ls_high": round(
            percentile(lsr, PCT_LS_HIGH), 4
        ) if lsr else FALLBACK_LS_HIGH,
        "ls_low": round(
            percentile(lsr, PCT_LS_LOW), 4
        ) if lsr else FALLBACK_LS_LOW,
        "rsi_high": round(
            percentile(rsi_valid, PCT_RSI_HIGH), 2
        ) if rsi_valid else FALLBACK_RSI_HIGH,
        "rsi_low": round(
            percentile(rsi_valid, PCT_RSI_LOW), 2
        ) if rsi_valid else FALLBACK_RSI_LOW,
    }


def fetch_existing_events(symbol):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, event_type FROM events "
                    "WHERE symbol = %s",
                    (symbol,),
                )
                return {(r[0], r[1]) for r in cur.fetchall()}
    except Exception as e:
        log.error("fetch_existing_events: %s", e)
        return set()


def detect_events(symbol, data, th):
    events = []
    if len(data) < 2:
        return events

    rise_1h = th["rise_1h_pct"]
    rise_4h = th["rise_4h_pct"]
    vol_th = th["volume_ratio"]
    fund_th = th["funding_pct"]
    oi_th = th["oi_pct"]
    ls_high = th["ls_high"]
    ls_low = th["ls_low"]
    rsi_high = th["rsi_high"]
    rsi_low = th["rsi_low"]

    closes = [d["close"] for d in data]
    rsi_series = compute_rsi_series(closes, RSI_PERIOD)

    for i in range(len(data)):
        row = data[i]
        ts = row["timestamp"]

        if row["change_pct"] >= rise_1h:
            events.append({
                "timestamp": ts,
                "event_type": "rise_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["change_pct"], 4),
                "duration_hours": 1,
                "threshold": rise_1h,
            })
        elif row["change_pct"] <= -rise_1h:
            events.append({
                "timestamp": ts,
                "event_type": "fall_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(abs(row["change_pct"]), 4),
                "duration_hours": 1,
                "threshold": rise_1h,
            })

        if i >= 3:
            sum_4h = sum(
                data[j]["change_pct"] for j in range(i - 3, i + 1)
            )
            if sum_4h >= rise_4h:
                events.append({
                    "timestamp": ts,
                    "event_type": "rise_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(sum_4h, 4),
                    "duration_hours": 4,
                    "threshold": rise_4h,
                })
            elif sum_4h <= -rise_4h:
                events.append({
                    "timestamp": ts,
                    "event_type": "fall_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(abs(sum_4h), 4),
                    "duration_hours": 4,
                    "threshold": rise_4h,
                })

        if i >= 167:
            prev_high = max(
                data[j]["high"] for j in range(i - 167, i)
            )
            if row["high"] > prev_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_high_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(row["high"] - prev_high, 4),
                    "duration_hours": 168,
                    "threshold": None,
                })
            prev_low = min(
                data[j]["low"] for j in range(i - 167, i)
            )
            if row["low"] < prev_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_low_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(prev_low - row["low"], 4),
                    "duration_hours": 168,
                    "threshold": None,
                })

        if row["volume_ratio"] >= vol_th:
            events.append({
                "timestamp": ts,
                "event_type": "volume_spike",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["volume_ratio"], 4),
                "duration_hours": 1,
                "threshold": vol_th,
            })

        fr = row.get("funding_rate")
        if fr is not None:
            if fr >= fund_th:
                events.append({
                    "timestamp": ts,
                    "event_type": "funding_spike_pos",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(fr, 6),
                    "duration_hours": 1,
                    "threshold": fund_th,
                })
            elif fr <= -fund_th:
                events.append({
                    "timestamp": ts,
                    "event_type": "funding_spike_neg",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(abs(fr), 6),
                    "duration_hours": 1,
                    "threshold": fund_th,
                })

        oic = row.get("oi_change_pct")
        if oic is not None and abs(oic) >= oi_th:
            events.append({
                "timestamp": ts,
                "event_type": "oi_spike",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(abs(oic), 4),
                "duration_hours": 1,
                "threshold": oi_th,
            })

        lsr = row.get("ls_ratio")
        if lsr is not None:
            if lsr >= ls_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "ls_long_extreme",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(lsr, 4),
                    "duration_hours": 1,
                    "threshold": ls_high,
                })
            elif lsr <= ls_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "ls_short_extreme",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(lsr, 4),
                    "duration_hours": 1,
                    "threshold": ls_low,
                })

        rsi_val = rsi_series[i] if i < len(rsi_series) else None
        if rsi_val is not None:
            if rsi_val >= rsi_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "rsi_overbought",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(rsi_val, 2),
                    "duration_hours": 1,
                    "threshold": rsi_high,
                })
            elif rsi_val <= rsi_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "rsi_oversold",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(rsi_val, 2),
                    "duration_hours": 1,
                    "threshold": rsi_low,
                })

    return events


def save_events(symbol, events):
    if not events:
        return 0

    sql_head = (
        "INSERT INTO events "
        "(symbol, timestamp, event_type, "
        "change_pct, magnitude, duration_hours) VALUES "
    )
    placeholders = ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(events))
    sql = sql_head + placeholders

    params = []
    for e in events:
        params.extend([
            symbol, e["timestamp"], e["event_type"],
            e["change_pct"], e["magnitude"],
            e["duration_hours"],
        ])

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.rowcount or 0
    except Exception as e:
        log.error("save_events batch failed: %s", e)
        return save_events_single(symbol, events)


def save_events_single(symbol, events):
    added = 0
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                for i, e in enumerate(events):
                    sp = "sp_ev_" + str(i)
                    try:
                        cur.execute("SAVEPOINT " + sp)
                        cur.execute(
                            "INSERT INTO events "
                            "(symbol, timestamp, event_type, "
                            "change_pct, magnitude, duration_hours) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                symbol, e["timestamp"],
                                e["event_type"],
                                e["change_pct"], e["magnitude"],
                                e["duration_hours"],
                            ),
                        )
                        n = cur.rowcount or 0
                        cur.execute("RELEASE SAVEPOINT " + sp)
                        added += n
                    except Exception as ex:
                        try:
                            cur.execute(
                                "ROLLBACK TO SAVEPOINT " + sp
                            )
                        except Exception:
                            pass
                        log.warning("row %d skip: %s", i, ex)
    except Exception as e:
        log.error("save_events_single: %s", e)
    return added


def analyze_symbol(symbol):
    log.info("%s -- detect events", symbol)
    data = fetch_data(symbol, limit=THRESHOLD_LOOKBACK_HOURS)
    if not data:
        log.warning("%s: no data", symbol)
        return None

    log.info("   candles=%d", len(data))

    th = compute_thresholds(data)
    log.info("   thresholds (%s):", th["source"])
    log.info("      rise_1h  = %s%%", th["rise_1h_pct"])
    log.info("      rise_4h  = %s%%", th["rise_4h_pct"])
    log.info("      volume   = x%s", th["volume_ratio"])
    log.info("      funding  = %s%%", th["funding_pct"])
    log.info("      oi       = %s%%", th["oi_pct"])

    existing = fetch_existing_events(symbol)
    log.info("   already in DB: %d", len(existing))

    all_events = detect_events(symbol, data, th)

    new_events = [
        e for e in all_events
        if (e["timestamp"], e["event_type"]) not in existing
    ]

    log.info("   found total: %d", len(all_events))
    log.info("   new to save: %d", len(new_events))

    by_type = {}
    for e in all_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1

    saved = save_events(symbol, new_events)
    log.info("   added to DB: %d", saved)

    return {
        "symbol": symbol,
        "total_events": len(all_events),
        "new_events": len(new_events),
        "saved": saved,
        "by_type": by_type,
        "thresholds": th,
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader EVENTS v5")
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    all_analysis = {}
    for symbol in SYMBOLS:
        analysis = analyze_symbol(symbol)
        if analysis:
            all_analysis[symbol] = analysis
        log.info("")

    try:
        with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info("%s saved", ANALYSIS_FILE.name)
    except Exception as e:
        log.error("save analysis: %s", e)

    log.info("=" * 60)
    log.info("EVENTS DONE")
    log.info("=" * 60)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()