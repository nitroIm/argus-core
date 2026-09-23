# ============================================================
# ARGUS-Trader — FEATURES
# ------------------------------------------------------------
# Считает признаки из свечей OHLCV.
# Подтягивает funding_rate из funding_rates.
# Записывает в features_hourly (Supabase).
# ------------------------------------------------------------
# v2: + funding_rate (последний <= timestamp)
#     + валидация значений
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
MAX_CHANGE_PCT = 50.0
MAX_RANGE_PCT = 100.0
MAX_FUNDING_PCT = 5.0


def is_valid(val, limit):
    """True если значение в разумных пределах."""
    if val is None:
        return False
    try:
        v = float(val)
    except (TypeError, ValueError):
        return False
    if v != v:  # NaN
        return False
    if abs(v) > limit:
        return False
    return True


def safe_val(val, limit):
    """Возвращает val если валидно, иначе None."""
    return val if is_valid(val, limit) else None


# ============================================================
# BASE FEATURES
# ============================================================
def compute_features_from_row(row):
    o = row["open"]
    h = row["high"]
    l = row["low"]
    c = row["close"]
    v = row["volume"]

    if not o or not h or not l or not c:
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

    # Валидация
    change_pct = safe_val(change_pct, MAX_CHANGE_PCT)
    range_pct = safe_val(range_pct, MAX_RANGE_PCT)

    return {
        "change_pct": change_pct,
        "range_pct": range_pct,
        "body_pct": body_pct,
        "upper_wick_pct": upper_wick_pct,
        "lower_wick_pct": lower_wick_pct,
        "pattern_bit": pattern_bit,
        "volume": v,
        "close": c,
    }


# ============================================================
# ROLLING
# ============================================================
def compute_rolling_features(features_list, idx,
                             window, field):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        f[field] for f in features_list[start:idx]
        if f.get(field) is not None
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def compute_volatility(features_list, idx, window):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        f["change_pct"] for f in features_list[start:idx]
        if f.get("change_pct") is not None
    ]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum(
        (x - mean) ** 2 for x in values
    ) / len(values)
    return round(var ** 0.5, 4)


def compute_change_sum(features_list, idx, window):
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [
        f["change_pct"] for f in features_list[start:idx]
        if f.get("change_pct") is not None
    ]
    if not values:
        return None
    return round(sum(values), 4)


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
    """Все funding_rate для символа."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, funding_rate "
                    "FROM funding_rates "
                    "WHERE symbol = %s "
                    "ORDER BY timestamp",
                    (symbol,),
                )
                result = []
                for r in cur.fetchall():
                    ts = r[0]
                    rate = r[1]
                    if rate is not None:
                        rate = float(rate)
                    result.append((ts, rate))
                return result
    except Exception as e:
        log.error(f"fetch_funding: {e}")
        return []


def funding_at(funding_list, ts):
    """Последний funding <= ts."""
    result = None
    for f_ts, f_rate in funding_list:
        if f_ts <= ts:
            result = f_rate
        else:
            break
    return result


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
                            f"INSERT features skip: {e}"
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
    for c in candles:
        f = compute_features_from_row(c)
        if f:
            f["timestamp"] = c["timestamp"]
            base_features.append(f)

    if not base_features:
        return 0

    avg_volume_24h = compute_rolling_features(
        base_features, len(base_features),
        24, "volume",
    )

    for idx, f in enumerate(base_features):
        if avg_volume_24h and avg_volume_24h > 0:
            f["volume_ratio_24h"] = round(
                f["volume"] / avg_volume_24h, 3
            )
        else:
            f["volume_ratio_24h"] = None

        f["volatility_24h"] = compute_volatility(
            base_features, idx, 24
        )
        f["volatility_7d"] = compute_volatility(
            base_features, idx, 168
        )
        f["change_4h"] = compute_change_sum(
            base_features, idx, 4
        )
        f["change_24h"] = compute_change_sum(
            base_features, idx, 24
        )
        f["change_7d"] = compute_change_sum(
            base_features, idx, 168
        )

    # --- Funding ---
    log.info(f"   Загружаю funding...")
    funding = fetch_funding(symbol)
    log.info(f"   funding точек: {len(funding)}")

    filled = 0
    for f in base_features:
        rate = funding_at(funding, f["timestamp"])
        if rate is not None:
            rate = safe_val(
                rate * 100, MAX_FUNDING_PCT
            )
            if rate is not None:
                filled += 1
        f["funding_rate"] = rate

    log.info(f"   funding заполнен: {filled}")

    added = save_features(symbol, base_features)
    log.info(f"   ✅ Добавлено features: {added}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧮 ARGUS-Trader FEATURES v2")
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        n = process_symbol(symbol, "1h")
        total += n

    log.info("=" * 60)
    log.info(f"✅ FEATURES DONE. Всего: {total}")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()