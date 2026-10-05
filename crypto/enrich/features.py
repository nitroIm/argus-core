# ============================================================
# ARGUS-Trader - FEATURES v9
# ------------------------------------------------------------
# v9: BOOTSTRAP LIMIT 3500 -> 9000 (полная история candles).
# v8: DB routing via symbol_conn (DB1: BTC/ETH, DB2: SOL/BNB).
#     SYMBOLS from env, fallback to config.SYMBOLS.
#     Автопоиск db2.py (как в dataset.py v7.1).
# v7: BOOTSTRAP env — read up to 3500 candles (one-time).
# v6: batch INSERT in save_features.
# v5: + EMA9/21/50, MACD, BB, dist high/low, session.
# ============================================================

import os
import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

# Auto-locate db2.py (same trick as dataset.py v7.1)
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.features")

BOOTSTRAP = (
    os.getenv("FEATURES_BOOTSTRAP", "").strip() == "1"
)
LIMIT = 9000 if BOOTSTRAP else 500

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

LIMITS = {
    "change_pct": 50.0,
    "range_pct": 100.0,
    "body_pct": 100.0,
    "upper_wick_pct": 100.0,
    "lower_wick_pct": 100.0,
    "volume_ratio_24h": 100.0,
    "volatility_24h": 20.0,
    "volatility_7d": 20.0,
    "change_4h": 100.0,
    "change_24h": 200.0,
    "change_7d": 500.0,
    "change_1d": 50.0,
    "change_3d": 100.0,
    "funding_rate": 5.0,
    "oi_change_pct": 100.0,
    "ls_ratio": 20.0,
    "taker_ratio": 5.0,
    "next_change_pct": 50.0,
    "ema9_dist_pct": 20.0,
    "ema21_dist_pct": 30.0,
    "ema50_dist_pct": 50.0,
    "macd": 5000.0,
    "macd_signal": 5000.0,
    "bb_upper_dist": 20.0,
    "bb_lower_dist": 20.0,
    "bb_width_pct": 30.0,
    "dist_high_24h_pct": 20.0,
    "dist_low_24h_pct": 20.0,
}

MAX_FUTURE_MIN = 5
MAX_FUNDING_AGE_H = 24
MAX_OI_AGE_H = 2
MAX_LS_AGE_H = 2
MAX_TAKER_AGE_H = 2


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning("db2 conn %s: %s", symbol, e)
    return get_connection()


def is_valid(val, limit):
    if val is None:
        return False
    try:
        v = float(val)
    except (TypeError, ValueError):
        return False
    if v != v:
        return False
    if abs(v) > limit:
        return False
    return True


def safe_val(val, limit):
    return val if is_valid(val, limit) else None


def ts_is_sane(ts):
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if ts > now + timedelta(minutes=MAX_FUTURE_MIN):
        return False
    if ts < now - timedelta(days=365 * 2):
        return False
    return True


def compute_features_from_row(row):
    o = row["open"]
    h = row["high"]
    l = row["low"]
    c = row["close"]
    v = row["volume"]

    if not o or not h or not l or not c:
        return None
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    if h < l:
        return None

    change_pct = round((c - o) / o * 100, 4)
    rng = h - l
    range_pct = round(rng / o * 100, 4) if o else 0
    body_pct = round(
        abs(c - o) / rng * 100, 4
    ) if rng > 0 else 0
    upper_wick_pct = round(
        (h - max(o, c)) / rng * 100, 4
    ) if rng > 0 else 0
    lower_wick_pct = round(
        (min(o, c) - l) / rng * 100, 4
    ) if rng > 0 else 0

    return {
        "change_pct": safe_val(
            change_pct, LIMITS["change_pct"]
        ),
        "range_pct": safe_val(
            range_pct, LIMITS["range_pct"]
        ),
        "body_pct": safe_val(
            body_pct, LIMITS["body_pct"]
        ),
        "upper_wick_pct": safe_val(
            upper_wick_pct, LIMITS["upper_wick_pct"]
        ),
        "lower_wick_pct": safe_val(
            lower_wick_pct, LIMITS["lower_wick_pct"]
        ),
        "volume": v,
        "close": c,
        "high": h,
        "low": l,
        "open": o,
    }


def rolling_avg(features_list, idx, window, field):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        features_list[i].get(field)
        for i in range(start, idx)
    ]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values) / len(values)


def rolling_volatility(features_list, idx, window):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        features_list[i].get("change_pct")
        for i in range(start, idx)
    ]
    values = [v for v in values if v is not None]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum(
        (x - mean) ** 2 for x in values
    ) / len(values)
    return var ** 0.5


def rolling_sum(features_list, idx, window):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        features_list[i].get("change_pct")
        for i in range(start, idx)
    ]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return sum(values)


def compute_ema(closes, period):
    if not closes:
        return []
    k = 2.0 / (period + 1)
    ema = [closes[0]]
    for i in range(1, len(closes)):
        ema.append(
            closes[i] * k + ema[-1] * (1 - k)
        )
    return ema


def compute_macd(closes):
    ema12 = compute_ema(closes, 12)
    ema26 = compute_ema(closes, 26)
    n = min(len(ema12), len(ema26))
    macd = [
        ema12[i] - ema26[i]
        for i in range(n)
    ]
    signal = compute_ema(macd, 9)
    return macd, signal


def compute_bbands(closes, period=20):
    up = [None] * len(closes)
    lo = [None] * len(closes)
    wd = [None] * len(closes)
    for i in range(len(closes)):
        if i < period - 1:
            continue
        window = closes[i - period + 1:i + 1]
        mean = sum(window) / period
        var = sum(
            (x - mean) ** 2 for x in window
        ) / period
        std = var ** 0.5
        up[i] = mean + 2 * std
        lo[i] = mean - 2 * std
        wd[i] = (up[i] - lo[i]) / mean * 100
    return up, lo, wd


def compute_consecutive(closes):
    out = [0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = max(0, out[i - 1]) + 1
        else:
            out[i] = min(0, out[i - 1]) - 1
    return out


def get_session(ts):
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    h = ts.hour
    if 0 <= h < 8:
        return 0
    if 8 <= h < 16:
        return 1
    return 2


def fetch_candles(symbol, timeframe="1h", limit=None):
    if limit is None:
        limit = LIMIT
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, "
                    "close, volume FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, timeframe, limit),
                )
                rows = cur.fetchall()
                rows = list(reversed(rows))
                return [
                    {
                        "timestamp": r[0],
                        "open": float(r[1]) if r[1] else 0,
                        "high": float(r[2]) if r[2] else 0,
                        "low": float(r[3]) if r[3] else 0,
                        "close": float(r[4]) if r[4] else 0,
                        "volume": float(r[5]) if r[5] else 0,
                    }
                    for r in rows
                ]
    except Exception as e:
        log.error("fetch_candles: %s", e)
        return []


def fetch_daily_candles(symbol, limit=500):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, close FROM candles "
                    "WHERE symbol = %s "
                    "AND timeframe = '1d' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                rows = cur.fetchall()
                rows = list(reversed(rows))
                out = []
                for r in rows:
                    ts = r[0]
                    c = float(r[1]) if r[1] else None
                    if ts and c is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        out.append((ts, c))
                return out
    except Exception as e:
        log.error("fetch_daily_candles: %s", e)
        return []


def fetch_funding(symbol):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, rate "
                    "FROM funding_rates "
                    "WHERE symbol = %s "
                    "AND rate IS NOT NULL "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    rate = float(r[1]) if r[1] else None
                    if ts and rate is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        result.append((ts, rate))
                return result
    except Exception as e:
        log.error("fetch_funding: %s", e)
        return []


def fetch_open_interest(symbol):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, oi, oi_value "
                    "FROM open_interest "
                    "WHERE symbol = %s "
                    "AND (oi IS NOT NULL "
                    "OR oi_value IS NOT NULL) "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    val = None
                    if r[2] is not None:
                        val = float(r[2])
                    elif r[1] is not None:
                        val = float(r[1])
                    if ts and val is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        result.append((ts, val))
                return result
    except Exception as e:
        log.error("fetch_oi: %s", e)
        return []


def fetch_long_short(symbol):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, ls_ratio "
                    "FROM long_short_ratio "
                    "WHERE symbol = %s "
                    "AND ls_ratio IS NOT NULL "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    val = float(r[1]) if r[1] else None
                    if ts and val is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        result.append((ts, val))
                return result
    except Exception as e:
        log.error("fetch_ls: %s", e)
        return []


def fetch_taker(symbol):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, buy_vol, "
                    "sell_vol FROM taker_flow "
                    "WHERE symbol = %s "
                    "AND buy_vol IS NOT NULL "
                    "AND sell_vol IS NOT NULL "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    bv = float(r[1]) if r[1] else None
                    sv = float(r[2]) if r[2] else None
                    if ts and bv is not None and sv is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(
                                tzinfo=timezone.utc
                            )
                        result.append((ts, bv, sv))
                return result
    except Exception as e:
        log.error("fetch_taker: %s", e)
        return []


def funding_at(funding_list, ts):
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    result = None
    for f_ts, f_rate in funding_list:
        if f_ts <= ts:
            result = (f_ts, f_rate)
        else:
            break
    if result is None:
        return None
    if ts - result[0] > timedelta(
        hours=MAX_FUNDING_AGE_H
    ):
        return None
    return result[1]


def funding_trend_at(funding_list, ts):
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    relevant = [
        f for f in funding_list if f[0] <= ts
    ]
    if len(relevant) < 2:
        return None
    last = relevant[-1]
    prev = relevant[-2]
    if ts - last[0] > timedelta(
        hours=MAX_FUNDING_AGE_H
    ):
        return None
    diff = last[1] - prev[1]
    if abs(diff) < 1e-9:
        return 0
    return 1 if diff > 0 else -1


def oi_at(oi_list, ts):
    if not oi_list:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    result = None
    for o_ts, o_val in oi_list:
        if o_ts <= ts:
            result = (o_ts, o_val)
        else:
            break
    if result is None:
        return None
    if ts - result[0] > timedelta(
        hours=MAX_OI_AGE_H
    ):
        return None
    return result[1]


def oi_at_ago(oi_list, ts, hours):
    return oi_at(oi_list, ts - timedelta(hours=hours))


def ls_at(ls_list, ts):
    if not ls_list:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    result = None
    for l_ts, l_val in ls_list:
        if l_ts <= ts:
            result = (l_ts, l_val)
        else:
            break
    if result is None:
        return None
    if ts - result[0] > timedelta(
        hours=MAX_LS_AGE_H
    ):
        return None
    return result[1]


def taker_at(taker_list, ts):
    if not taker_list:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    result = None
    for t_ts, bv, sv in taker_list:
        if t_ts <= ts:
            result = (t_ts, bv, sv)
        else:
            break
    if result is None:
        return None
    if ts - result[0] > timedelta(
        hours=MAX_TAKER_AGE_H
    ):
        return None
    total = result[1] + result[2]
    if total <= 0:
        return None
    return result[1] / total


def daily_index_before(daily_list, target_dt):
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)
    idx = None
    for i, (d_ts, _) in enumerate(daily_list):
        if d_ts <= target_dt:
            idx = i
        else:
            break
    return idx


def compute_daily_features(daily_list, ts):
    out = {
        "change_1d": None,
        "change_3d": None,
        "trend_up": None,
    }
    if not daily_list:
        return out
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    target = ts - timedelta(days=1)
    idx = daily_index_before(daily_list, target)
    if idx is None:
        return out
    c_now = daily_list[idx][1]
    if idx >= 1:
        c_prev = daily_list[idx - 1][1]
        if c_prev and c_prev > 0:
            val = (c_now - c_prev) / c_prev * 100
            out["change_1d"] = safe_val(
                round(val, 4),
                LIMITS["change_1d"],
            )
    if idx >= 3:
        c_3d = daily_list[idx - 3][1]
        if c_3d and c_3d > 0:
            val = (c_now - c_3d) / c_3d * 100
            out["change_3d"] = safe_val(
                round(val, 4),
                LIMITS["change_3d"],
            )
    if idx >= 7:
        c_7d = daily_list[idx - 7][1]
        if c_7d and c_7d > 0:
            out["trend_up"] = 1 if c_now > c_7d else 0
    return out


FEATURES_COLS = (
    "symbol, timestamp, "
    "change_pct, range_pct, body_pct, "
    "upper_wick_pct, lower_wick_pct, "
    "volume_ratio_24h, volatility_24h, "
    "volatility_7d, change_4h, "
    "change_24h, change_7d, "
    "change_1d, change_3d, trend_up, "
    "hour_of_day, day_of_week, "
    "funding_rate, funding_trend, "
    "oi_change_pct, ls_ratio, taker_ratio, "
    "ema9_dist_pct, ema21_dist_pct, "
    "ema50_dist_pct, macd, macd_signal, "
    "bb_upper_dist, bb_lower_dist, "
    "bb_width_pct, dist_high_24h_pct, "
    "dist_low_24h_pct, consecutive_up, "
    "session, "
    "next_change_pct, next_direction, "
    "computed_at"
)

FEATURES_CONFLICT = (
    " ON CONFLICT (symbol, timestamp) DO UPDATE SET "
    "change_pct = EXCLUDED.change_pct, "
    "range_pct = EXCLUDED.range_pct, "
    "body_pct = EXCLUDED.body_pct, "
    "upper_wick_pct = EXCLUDED.upper_wick_pct, "
    "lower_wick_pct = EXCLUDED.lower_wick_pct, "
    "volume_ratio_24h = EXCLUDED.volume_ratio_24h, "
    "volatility_24h = EXCLUDED.volatility_24h, "
    "volatility_7d = EXCLUDED.volatility_7d, "
    "change_4h = EXCLUDED.change_4h, "
    "change_24h = EXCLUDED.change_24h, "
    "change_7d = EXCLUDED.change_7d, "
    "change_1d = EXCLUDED.change_1d, "
    "change_3d = EXCLUDED.change_3d, "
    "trend_up = EXCLUDED.trend_up, "
    "hour_of_day = EXCLUDED.hour_of_day, "
    "day_of_week = EXCLUDED.day_of_week, "
    "funding_rate = EXCLUDED.funding_rate, "
    "funding_trend = EXCLUDED.funding_trend, "
    "oi_change_pct = EXCLUDED.oi_change_pct, "
    "ls_ratio = EXCLUDED.ls_ratio, "
    "taker_ratio = EXCLUDED.taker_ratio, "
    "ema9_dist_pct = EXCLUDED.ema9_dist_pct, "
    "ema21_dist_pct = EXCLUDED.ema21_dist_pct, "
    "ema50_dist_pct = EXCLUDED.ema50_dist_pct, "
    "macd = EXCLUDED.macd, "
    "macd_signal = EXCLUDED.macd_signal, "
    "bb_upper_dist = EXCLUDED.bb_upper_dist, "
    "bb_lower_dist = EXCLUDED.bb_lower_dist, "
    "bb_width_pct = EXCLUDED.bb_width_pct, "
    "dist_high_24h_pct = EXCLUDED.dist_high_24h_pct, "
    "dist_low_24h_pct = EXCLUDED.dist_low_24h_pct, "
    "consecutive_up = EXCLUDED.consecutive_up, "
    "session = EXCLUDED.session, "
    "next_change_pct = EXCLUDED.next_change_pct, "
    "next_direction = EXCLUDED.next_direction, "
    "computed_at = EXCLUDED.computed_at"
)

FEATURES_PLACEHOLDER = (
    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
    "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)


def _feature_row_tuple(symbol, f, now_utc):
    return (
        symbol, f["timestamp"],
        f.get("change_pct"),
        f.get("range_pct"),
        f.get("body_pct"),
        f.get("upper_wick_pct"),
        f.get("lower_wick_pct"),
        f.get("volume_ratio_24h"),
        f.get("volatility_24h"),
        f.get("volatility_7d"),
        f.get("change_4h"),
        f.get("change_24h"),
        f.get("change_7d"),
        f.get("change_1d"),
        f.get("change_3d"),
        f.get("trend_up"),
        f.get("hour_of_day"),
        f.get("day_of_week"),
        f.get("funding_rate"),
        f.get("funding_trend"),
        f.get("oi_change_pct"),
        f.get("ls_ratio"),
        f.get("taker_ratio"),
        f.get("ema9_dist_pct"),
        f.get("ema21_dist_pct"),
        f.get("ema50_dist_pct"),
        f.get("macd"),
        f.get("macd_signal"),
        f.get("bb_upper_dist"),
        f.get("bb_lower_dist"),
        f.get("bb_width_pct"),
        f.get("dist_high_24h_pct"),
        f.get("dist_low_24h_pct"),
        f.get("consecutive_up"),
        f.get("session"),
        f.get("next_change_pct"),
        f.get("next_direction"),
        now_utc,
    )


def save_features(symbol, features):
    if not features:
        return 0

    now_utc = datetime.now(timezone.utc)
    CHUNK = 200
    total_added = 0

    for start in range(0, len(features), CHUNK):
        chunk = features[start:start + CHUNK]
        placeholders = ",".join(
            [FEATURES_PLACEHOLDER] * len(chunk)
        )
        sql = (
            "INSERT INTO features_hourly (" + FEATURES_COLS
            + ") VALUES " + placeholders
            + FEATURES_CONFLICT
        )
        params = []
        for f in chunk:
            params.extend(
                _feature_row_tuple(symbol, f, now_utc)
            )

        try:
            with symbol_conn(symbol) as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    n = cur.rowcount or 0
                    total_added += n
        except Exception as e:
            log.error(
                "chunk %d failed: %s", start, e,
            )

    return total_added


def process_symbol(symbol, timeframe="1h"):
    log.info("%s - loading candles (limit=%d)",
             symbol, LIMIT)
    candles = fetch_candles(symbol, timeframe, limit=LIMIT)
    if not candles:
        log.warning("%s: no candles", symbol)
        return 0

    log.info("   candles: %d", len(candles))

    base_features = []
    skipped_ts = 0
    for c in candles:
        if not ts_is_sane(c["timestamp"]):
            skipped_ts += 1
            continue
        f = compute_features_from_row(c)
        if f:
            f["timestamp"] = c["timestamp"]
            base_features.append(f)

    if skipped_ts:
        log.warning("   skipped by ts: %d", skipped_ts)
    if not base_features:
        return 0

    for idx, f in enumerate(base_features):
        avg_vol = rolling_avg(
            base_features, idx, 24, "volume"
        )
        if avg_vol and avg_vol > 0:
            ratio = f["volume"] / avg_vol
            f["volume_ratio_24h"] = safe_val(
                round(ratio, 3),
                LIMITS["volume_ratio_24h"],
            )
        else:
            f["volume_ratio_24h"] = None

        vol24 = rolling_volatility(
            base_features, idx, 24
        )
        f["volatility_24h"] = safe_val(
            round(vol24, 4)
            if vol24 is not None else None,
            LIMITS["volatility_24h"],
        )

        vol7d = rolling_volatility(
            base_features, idx, 168
        )
        f["volatility_7d"] = safe_val(
            round(vol7d, 4)
            if vol7d is not None else None,
            LIMITS["volatility_7d"],
        )

        ch4 = rolling_sum(base_features, idx, 4)
        f["change_4h"] = safe_val(
            round(ch4, 4) if ch4 is not None else None,
            LIMITS["change_4h"],
        )
        ch24 = rolling_sum(base_features, idx, 24)
        f["change_24h"] = safe_val(
            round(ch24, 4)
            if ch24 is not None else None,
            LIMITS["change_24h"],
        )
        ch7d = rolling_sum(base_features, idx, 168)
        f["change_7d"] = safe_val(
            round(ch7d, 4)
            if ch7d is not None else None,
            LIMITS["change_7d"],
        )

    closes = [f["close"] for f in base_features]
    ema9 = compute_ema(closes, 9)
    ema21 = compute_ema(closes, 21)
    ema50 = compute_ema(closes, 50)
    macd, macd_sig = compute_macd(closes)
    bb_up, bb_lo, bb_wd = compute_bbands(closes, 20)
    consec = compute_consecutive(closes)

    for idx, f in enumerate(base_features):
        c = f["close"]

        f["ema9_dist_pct"] = safe_val(
            round((c - ema9[idx]) / c * 100, 4),
            LIMITS["ema9_dist_pct"],
        )
        f["ema21_dist_pct"] = safe_val(
            round((c - ema21[idx]) / c * 100, 4),
            LIMITS["ema21_dist_pct"],
        )
        f["ema50_dist_pct"] = safe_val(
            round((c - ema50[idx]) / c * 100, 4),
            LIMITS["ema50_dist_pct"],
        )

        m = macd[idx] if idx < len(macd) else None
        ms = macd_sig[idx] if idx < len(macd_sig) else None
        f["macd"] = safe_val(
            round(m, 4) if m is not None else None,
            LIMITS["macd"],
        )
        f["macd_signal"] = safe_val(
            round(ms, 4) if ms is not None else None,
            LIMITS["macd_signal"],
        )

        if bb_up[idx] is not None:
            f["bb_upper_dist"] = safe_val(
                round((c - bb_up[idx]) / c * 100, 4),
                LIMITS["bb_upper_dist"],
            )
            f["bb_lower_dist"] = safe_val(
                round((c - bb_lo[idx]) / c * 100, 4),
                LIMITS["bb_lower_dist"],
            )
            f["bb_width_pct"] = safe_val(
                round(bb_wd[idx], 4),
                LIMITS["bb_width_pct"],
            )
        else:
            f["bb_upper_dist"] = None
            f["bb_lower_dist"] = None
            f["bb_width_pct"] = None

        start = max(0, idx - 24)
        window = base_features[start:idx + 1]
        h24 = max(
            (x["high"] for x in window), default=None
        )
        l24 = min(
            (x["low"] for x in window), default=None
        )
        if h24 and h24 > 0:
            f["dist_high_24h_pct"] = safe_val(
                round((c - h24) / c * 100, 4),
                LIMITS["dist_high_24h_pct"],
            )
        else:
            f["dist_high_24h_pct"] = None
        if l24 and l24 > 0:
            f["dist_low_24h_pct"] = safe_val(
                round((c - l24) / c * 100, 4),
                LIMITS["dist_low_24h_pct"],
            )
        else:
            f["dist_low_24h_pct"] = None

        f["consecutive_up"] = consec[idx]

        ts = f["timestamp"]
        if isinstance(ts, datetime):
            f["session"] = get_session(ts)
        else:
            f["session"] = None

    for idx, f in enumerate(base_features):
        if idx + 1 < len(base_features):
            c_now = f.get("close")
            c_next = base_features[idx + 1].get("close")
            if c_now and c_next and c_now > 0:
                nxt = (c_next - c_now) / c_now * 100
                f["next_change_pct"] = safe_val(
                    round(nxt, 4),
                    LIMITS["next_change_pct"],
                )
                if f["next_change_pct"] is not None:
                    f["next_direction"] = (
                        1 if nxt > 0 else 0
                    )
                else:
                    f["next_direction"] = None
            else:
                f["next_change_pct"] = None
                f["next_direction"] = None
        else:
            f["next_change_pct"] = None
            f["next_direction"] = None

    daily = fetch_daily_candles(symbol, limit=500)
    log.info("   daily points: %d", len(daily))
    filled_d1 = 0
    for f in base_features:
        d_feat = compute_daily_features(
            daily, f["timestamp"]
        )
        f["change_1d"] = d_feat["change_1d"]
        f["change_3d"] = d_feat["change_3d"]
        f["trend_up"] = d_feat["trend_up"]
        if f["change_1d"] is not None:
            filled_d1 += 1
    log.info("   daily change_1d: %d", filled_d1)

    for f in base_features:
        ts = f["timestamp"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts_utc = ts.replace(tzinfo=timezone.utc)
            else:
                ts_utc = ts
            f["hour_of_day"] = ts_utc.hour
            f["day_of_week"] = ts_utc.weekday()
        else:
            f["hour_of_day"] = None
            f["day_of_week"] = None

    funding = fetch_funding(symbol)
    log.info("   funding points: %d", len(funding))
    filled_f = 0
    filled_t = 0
    for f in base_features:
        rate = funding_at(funding, f["timestamp"])
        if rate is not None:
            rate_pct = rate * 100
            rate_pct = safe_val(
                rate_pct, LIMITS["funding_rate"]
            )
            if rate_pct is not None:
                filled_f += 1
            f["funding_rate"] = rate_pct
        else:
            f["funding_rate"] = None
        f["funding_trend"] = funding_trend_at(
            funding, f["timestamp"]
        )
        if f["funding_trend"] is not None:
            filled_t += 1
    log.info(
        "   funding: rate=%d trend=%d",
        filled_f, filled_t,
    )

    oi = fetch_open_interest(symbol)
    log.info("   OI points: %d", len(oi))
    filled_oi = 0
    for f in base_features:
        oi_now = oi_at(oi, f["timestamp"])
        oi_1h = oi_at_ago(oi, f["timestamp"], 1)
        if oi_now and oi_1h and oi_1h > 0:
            pct = (oi_now - oi_1h) / oi_1h * 100
            f["oi_change_pct"] = safe_val(
                round(pct, 4),
                LIMITS["oi_change_pct"],
            )
            if f["oi_change_pct"] is not None:
                filled_oi += 1
        else:
            f["oi_change_pct"] = None
    log.info("   OI change: %d", filled_oi)

    ls = fetch_long_short(symbol)
    log.info("   LS points: %d", len(ls))
    filled_ls = 0
    for f in base_features:
        val = ls_at(ls, f["timestamp"])
        if val is not None:
            f["ls_ratio"] = safe_val(
                round(val, 4),
                LIMITS["ls_ratio"],
            )
            if f["ls_ratio"] is not None:
                filled_ls += 1
        else:
            f["ls_ratio"] = None
    log.info("   LS ratio: %d", filled_ls)

    taker = fetch_taker(symbol)
    log.info("   Taker points: %d", len(taker))
    filled_tk = 0
    for f in base_features:
        val = taker_at(taker, f["timestamp"])
        if val is not None:
            f["taker_ratio"] = safe_val(
                round(val, 4),
                LIMITS["taker_ratio"],
            )
            if f["taker_ratio"] is not None:
                filled_tk += 1
        else:
            f["taker_ratio"] = None
    log.info("   Taker ratio: %d", filled_tk)

    added = save_features(symbol, base_features)
    log.info("   saved: %d", added)
    return added


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader FEATURES v9")
    log.info("BOOTSTRAP=%s, LIMIT=%d", BOOTSTRAP, LIMIT)
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        n = process_symbol(symbol, "1h")
        total += n

    log.info("=" * 60)
    log.info("DONE. Total: %d", total)
    log.info("=" * 60)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()