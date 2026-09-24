# ============================================================
# ARGUS-Trader — FEATURES
# ------------------------------------------------------------
# v4.1: + ls_ratio, taker_ratio
# v4: + oi_change_pct, funding_trend,
#     + next_change_pct, next_direction
# v3.2: ON CONFLICT DO UPDATE
# ============================================================

import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYPTO_ROOT))

from config import SYMBOLS
from db import get_connection, close_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.features")

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
    "funding_rate": 5.0,
    "oi_change_pct": 100.0,
    "ls_ratio": 20.0,
    "taker_ratio": 5.0,
    "next_change_pct": 50.0,
}

MAX_FUTURE_MIN = 5
MAX_FUNDING_AGE_H = 24
MAX_OI_AGE_H = 2
MAX_LS_AGE_H = 2
MAX_TAKER_AGE_H = 2


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
    if ts < now - timedelta(days=365):
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

    pattern_bit = 1 if c > o else 0

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
        "pattern_bit": pattern_bit,
        "volume": v,
        "close": c,
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


def fetch_candles(symbol, timeframe="1h", limit=500):
    try:
        with get_connection() as conn:
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
        log.error(f"fetch_candles: {e}")
        return []


def fetch_funding(symbol):
    try:
        with get_connection() as conn:
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
                            ts = ts.replace(tzinfo=timezone.utc)
                        result.append((ts, rate))
                return result
    except Exception as e:
        log.error(f"fetch_funding: {e}")
        return []


def fetch_open_interest(symbol):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, oi_value "
                    "FROM open_interest "
                    "WHERE symbol = %s "
                    "AND oi_value IS NOT NULL "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    val = float(r[1]) if r[1] else None
                    if ts and val is not None:
                        if ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        result.append((ts, val))
                return result
    except Exception as e:
        log.error(f"fetch_oi: {e}")
        return []


def fetch_long_short(symbol):
    try:
        with get_connection() as conn:
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
                            ts = ts.replace(tzinfo=timezone.utc)
                        result.append((ts, val))
                return result
    except Exception as e:
        log.error(f"fetch_ls: {e}")
        return []


def fetch_taker(symbol):
    try:
        with get_connection() as conn:
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
                            ts = ts.replace(tzinfo=timezone.utc)
                        result.append((ts, bv, sv))
                return result
    except Exception as e:
        log.error(f"fetch_taker: {e}")
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
    age = ts - result[0]
    if age > timedelta(hours=MAX_FUNDING_AGE_H):
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
    if ts - last[0] > timedelta(hours=MAX_FUNDING_AGE_H):
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
    if ts - result[0] > timedelta(hours=MAX_OI_AGE_H):
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
    if ts - result[0] > timedelta(hours=MAX_LS_AGE_H):
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
    if ts - result[0] > timedelta(hours=MAX_TAKER_AGE_H):
        return None
    total = result[1] + result[2]
    if total <= 0:
        return None
    return result[1] / total


def save_features(symbol, features):
    if not features:
        return 0

    added = 0
    now_utc = datetime.now(timezone.utc)

    sql = (
        "INSERT INTO features_hourly "
        "(symbol, timestamp, "
        "change_pct, range_pct, body_pct, "
        "upper_wick_pct, lower_wick_pct, "
        "volume_ratio_24h, volatility_24h, "
        "volatility_7d, change_4h, "
        "change_24h, change_7d, "
        "funding_rate, funding_trend, "
        "oi_change_pct, ls_ratio, taker_ratio, "
        "next_change_pct, next_direction, "
        "computed_at) "
        "VALUES (%s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s, %s, "
        "%s, %s, %s) "
        "ON CONFLICT (symbol, timestamp) "
        "DO UPDATE SET "
        "change_pct = EXCLUDED.change_pct, "
        "range_pct = EXCLUDED.range_pct, "
        "body_pct = EXCLUDED.body_pct, "
        "upper_wick_pct = "
        "EXCLUDED.upper_wick_pct, "
        "lower_wick_pct = "
        "EXCLUDED.lower_wick_pct, "
        "volume_ratio_24h = "
        "EXCLUDED.volume_ratio_24h, "
        "volatility_24h = "
        "EXCLUDED.volatility_24h, "
        "volatility_7d = "
        "EXCLUDED.volatility_7d, "
        "change_4h = EXCLUDED.change_4h, "
        "change_24h = EXCLUDED.change_24h, "
        "change_7d = EXCLUDED.change_7d, "
        "funding_rate = "
        "EXCLUDED.funding_rate, "
        "funding_trend = "
        "EXCLUDED.funding_trend, "
        "oi_change_pct = "
        "EXCLUDED.oi_change_pct, "
        "ls_ratio = EXCLUDED.ls_ratio, "
        "taker_ratio = "
        "EXCLUDED.taker_ratio, "
        "next_change_pct = "
        "EXCLUDED.next_change_pct, "
        "next_direction = "
        "EXCLUDED.next_direction, "
        "computed_at = "
        "EXCLUDED.computed_at"
    )

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for f in features:
                    try:
                        cur.execute(
                            sql,
                            (
                                symbol,
                                f["timestamp"],
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
                                f.get("funding_rate"),
                                f.get("funding_trend"),
                                f.get("oi_change_pct"),
                                f.get("ls_ratio"),
                                f.get("taker_ratio"),
                                f.get("next_change_pct"),
                                f.get("next_direction"),
                                now_utc,
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(f"INSERT skip: {e}")
    except Exception as e:
        log.error(f"save_features: {e}")
    return added


def process_symbol(symbol, timeframe="1h"):
    log.info(f"📊 {symbol} — загружаю свечи")
    candles = fetch_candles(symbol, timeframe, limit=500)
    if not candles:
        log.warning(f"{symbol}: свечей нет")
        return 0

    log.info(f"   Свечей: {len(candles)}")

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
        log.warning(
            f"   Пропущено по timestamp: {skipped_ts}"
        )

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
        f["vol =atility_7d"] = safe_val(
 None            round(vol7d,

 4)
            if vol7d        is not None else None,
            LIMITS f["volatility_7d"],
        )

        ch4 = rolling_sum(base_features, idx, ["4)
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

    for idx, f in enumerate(base_features):
        if idx + 1 < len(base_features):
            c_now = f.get("close")
            c_next = base_features[idx + 1].get("close")
            if c_now and c_next and c_now > 0:
                nxt = (
                    (c_next - c_now) / c_now * 100
                )
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

    funding = fetch_funding(symbol)
    log.info(f"   funding точек: {len(funding)}")

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
            f["funding_rate"]funding_trend"] = funding_trend_at(
            funding, f["timestamp"]
        )
        if f["funding_trend"] is not None:
            filled_t += 1

    log.info(
        f"   funding: rate={filled_f} "
        f"trend={filled_t}"
    )

    oi = fetch_open_interest(symbol)
    log.info(f"   OI точек: {len(oi)}")

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

    log.info(f"   OI change: {filled_oi}")

    ls = fetch_long_short(symbol)
    log.info(f"   LS точек: {len(ls)}")

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

    log.info(f"   LS ratio: {filled_ls}")

    taker = fetch_taker(symbol)
    log.info(f"   Taker точек: {len(taker)}")

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

    log.info(f"   Taker ratio: {filled_tk}")

    added = save_features(symbol, base_features)
    log.info(f"   ✅ Записано: {added}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧮 ARGUS-Trader FEATURES v4.1")
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        n = process_symbol(symbol, "1h")
        total += n

    log.info("=" * 60)
    log.info(f"✅ DONE. Всего: {total}")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()