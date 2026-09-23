# ============================================================
# ARGUS-Trader — FEATURES
# ------------------------------------------------------------
# v3: полная защита.
#     - fix avg_volume_24h (per-point rolling)
#     - валидация ВСЕХ полей перед INSERT
#     - проверка timestamp (не в будущем)
#     - funding только свежий (<24ч)
# v2: + funding_rate
# v1: базовые признаки
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

# --- Лимиты валидации ---
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
}

MAX_FUTURE_MIN = 5
MAX_FUNDING_AGE_H = 24


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
    """Timestamp не в будущем и не старше 1 года."""
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


# ============================================================
# BASE
# ============================================================
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


# ============================================================
# ROLLING (per-point)
# ============================================================
def rolling_avg(features_list, idx, window, field):
    """Среднее field за window свечей ДО idx."""
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
    var = sum((x - mean) ** 2 for x in values) / len(values)
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


# ============================================================
# FETCH
# ============================================================
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
                    "SELECT timestamp, funding_rate "
                    "FROM funding_rates "
                    "WHERE symbol = %s "
                    "AND funding_rate IS NOT NULL "
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


def funding_at(funding_list, ts):
    """Последний funding <= ts, не старше 24ч."""
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


# ============================================================
# SAVE
# ============================================================
def save_features(symbol, features):
    if not features:
        return 0

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for f in features:
                    try:
                        cur.execute(
                            "INSERT INTO features_hourly "
                            "(symbol, timestamp, "
                            "change_pct, range_pct, body_pct, "
                            "upper_wick_pct, lower_wick_pct, "
                            "volume_ratio_24h, volatility_24h, "
                            "volatility_7d, change_4h, "
                            "change_24h, change_7d, "
                            "funding_rate) "
                            "VALUES (%s, %s, %s, %s, %s, "
                            "%s, %s, %s, %s, %s, %s, %s, "
                            "%s, %s) "
                            "ON CONFLICT (symbol, timestamp) "
                            "DO NOTHING",
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
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(
                            f"INSERT skip: {e}"
                        )
    except Exception as e:
        log.error(f"save_features: {e}")
    return added


# ============================================================
# PROCESS
# ============================================================
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
        log.warning(f"   Пропущено по timestamp: {skipped_ts}")

    if not base_features:
        return 0

    # --- Per-point rolling ---
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
            round(vol24, 4) if vol24 is not None else None,
            LIMITS["volatility_24h"],
        )

        vol7d = rolling_volatility(
            base_features, idx, 168
        )
        f["volatility_7d"] = safe_val(
            round(vol7d, 4) if vol7d is not None else None,
            LIMITS["volatility_7d"],
        )

        ch4 = rolling_sum(base_features, idx, 4)
        f["change_4h"] = safe_val(
            round(ch4, 4) if ch4 is not None else None,
            LIMITS["change_4h"],
        )

        ch24 = rolling_sum(base_features, idx, 24)
        f["change_24h"] = safe_val(
            round(ch24, 4) if ch24 is not None else None,
            LIMITS["change_24h"],
        )

        ch7d = rolling_sum(base_features, idx, 168)
        f["change_7d"] = safe_val(
            round(ch7d, 4) if ch7d is not None else None,
            LIMITS["change_7d"],
        )

    # --- Funding ---
    funding = fetch_funding(symbol)
    log.info(f"   funding точек: {len(funding)}")

    filled = 0
    for f in base_features:
        rate = funding_at(funding, f["timestamp"])
        if rate is not None:
            rate_pct = rate * 100
            rate_pct = safe_val(
                rate_pct, LIMITS["funding_rate"]
            )
            if rate_pct is not None:
                filled += 1
            f["funding_rate"] = rate_pct
        else:
            f["funding_rate"] = None

    log.info(f"   funding заполнен: {filled}")

    added = save_features(symbol, base_features)
    log.info(f"   ✅ Добавлено: {added}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧮 ARGUS-Trader FEATURES v3")
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