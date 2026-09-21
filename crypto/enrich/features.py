# ============================================================
# ARGUS-Trader — FEATURES
# ------------------------------------------------------------
# Считает признаки из свечей OHLCV.
# Записывает в features_hourly (Supabase).
# Запускается после каждого collect.
# ------------------------------------------------------------
# v1: базовые признаки — change, range, body, wick, волатильность
# ============================================================

import sys
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(CRYP(
TO_ROOT))

from config import SYMBOLS
from db import get_   connection, close_connection

logging.basicConfig level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.features")


# ============================================================
# РАСЧЁТ ПРИЗНАКОВ
# ============================================================
def compute_features_from_row(row: dict) -> dict:
    """
    Считает признаки из одной свечи.
    row: {timestamp, open, high, low, close, volume}
    """
    o, h, l, c, v = row["open"], row["high"], row["low"], row["close"], row["volume"]

    if not o or not h or not l or not c:
        return None

    # --- Движение цены ---
    change_pct = round((c - o) / o * 100, 4)

    rng = h - l
    range_pct = round(rng / o * 100, 4) if o else 0

    # Тело свечи: |close - open| / range
    body_pct = round(abs(c - o) / rng * 100, 4) if rng > 0 else 0

    # Верхний хвост: (high - max(open, close)) / range
    upper_wick_pct = round((h - max(o, c)) / rng * 100, 4) if rng > 0 else 0

    # Нижний хвост: (min(open, close) - low) / range
    lower_wick_pct = round((min(o, c) - l) / rng * 100, 4) if rng > 0 else 0

    # Бинарный сигнал: 1 = рост, 0 = падение
    pattern_bit = 1 if c > o else 0

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


def compute_rolling_features(features_list: list, idx: int, window: int, field: str) -> float:
    """Среднее значение поля за window свечей до idx (не включая idx)."""
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [f[field] for f in features_list[start:idx] if f.get(field) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 4)


def compute_volatility(features_list: list, idx: int, window: int) -> float:
    """Std отклонение change_pct за window."""
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [f["change_pct"] for f in features_list[start:idx]]
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return round(var ** 0.5, 4)


def compute_change_sum(features_list: list, idx: int, window: int) -> float:
    """Сумма change_pct за window (накопленное движение)."""
    start = max(0, idx - window)
    if start >= idx:
        return None
    values = [f["change_pct"] for f in features_list[start:idx]]
    if not values:
        return None
    return round(sum(values), 4)


# ============================================================
# ЗАГРУЗКА СВЕЧЕЙ ИЗ БД
# ============================================================
def fetch_candles(symbol: str, timeframe: str = "1h", limit: int = 500) -> list:
    """Возвращает последние N свечей (свежие первыми, но потом развернём)."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT timestamp, open, high, low, close, volume
                    FROM candles
                    WHERE symbol = %s AND timeframe = %s
                    ORDER BY timestamp DESC
                    LIMIT %s
                    """,
                    (symbol, timeframe, limit),
                )
                rows = cur.fetchall()
                # Разворачиваем — от старых к новым
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


# ============================================================
# СОХРАНЕНИЕ В БД
# ============================================================
def save_features(symbol: str, features: list) -> int:
    """Сохраняет список features в features_hourly."""
    if not features:
        return 0

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for f in features:
                    try:
                        cur.execute(
                            """
                            INSERT INTO features_hourly
                                (symbol, timestamp,
                                 change_pct, range_pct, body_pct,
                                 upper_wick_pct, lower_wick_pct,
                                 volume_ratio_24h,
                                 volatility_24h, volatility_7d,
                                 change_4h, change_24h, change_7d)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (symbol, timestamp) DO NOTHING
                            """,
                            (
                                symbol, f["timestamp"],
                                f.get("change_pct"), f.get("range_pct"),
                                f.get("body_pct"), f.get("upper_wick_pct"),
                                f.get("lower_wick_pct"), f.get("volume_ratio_24h"),
                                f.get("volatility_24h"), f.get("volatility_7d"),
                                f.get("change_4h"), f.get("change_24h"),
                                f.get("change_7d"),
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(f"INSERT features skip: {e}")
    except Exception as e:
        log.error(f"save_features: {e}")
    return added


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
def process_symbol(symbol: str, timeframe: str = "1h") -> int:
    """Считает features для всех свечей символа. Возвращает число добавленных."""
    log.info(f"📊 {symbol} — загружаю свечи")
    candles = fetch_candles(symbol, timeframe, limit=500)
    if not candles:
        log.warning(f"{symbol}: свечей нет")
        return 0

    log.info(f"   Получено свечей: {len(candles)}")

    # Базовые признаки
    base_features = []
    for c in candles:
        f = compute_features_from_row(c)
        if f:
            f["timestamp"] = c["timestamp"]
            base_features.append(f)

    if not base_features:
        return 0

    # Средний объём за 24 часа (rolling)
    avg_volume_24h = compute_rolling_features(base_features, len(base_features), 24, "volume")
    if not avg_volume_24h:
        avg_volume_24h = None

    # Добавляем rolling-признаки
    for idx, f in enumerate(base_features):
        # Объёмный коэффициент
        if avg_volume_24h and avg_volume_24h > 0:
            f["volume_ratio_24h"] = round(f["volume"] / avg_volume_24h, 3)
        else:
            f["volume_ratio_24h"] = None

        # Волатильность
        f["volatility_24h"] = compute_volatility(base_features, idx, 24)
        f["volatility_7d"] = compute_volatility(base_features, idx, 168)

        # Накопленное движение
        f["change_4h"] = compute_change_sum(base_features, idx, 4)
        f["change_24h"] = compute_change_sum(base_features, idx, 24)
        f["change_7d"] = compute_change_sum(base_features, idx, 168)

    # Сохранение
    added = save_features(symbol, base_features)
    log.info(f"   ✅ Добавлено features: {added}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧮 ARGUS-Trader FEATURES")
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        n = process_symbol(symbol, "1h")
        total += n

    log.info("=" * 60)
    log.info(f"✅ FEATURES DONE. Всего добавлено: {total}")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()