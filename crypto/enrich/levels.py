# ============================================================
# ARGUS-Trader — LEVELS
# ------------------------------------------------------------
# Считает уровни цены:
#   - Round numbers (84k, 85k, 86k)
#   - Recent highs/lows (7d, 30d, 90d)
#   - Support/Resistance (где цена отскакивала 3+ раз)
#   - Volume profile (уровни с макс объёмом)
# Результат → crypto/data/levels_analysis.json
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import sys
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from config import SYMBOLS
from db import get_connection, close_connection

DATA_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_FILE = DATA_DIR / "levels_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.levels")


def fetch_candles(symbol, limit=2000):
    """Возвращает свечи в порядке от старых к новым."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                rows = cur.fetchall()
                rows = list(reversed(rows))
                return [
                    {
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                    for r in rows
                ]
    except Exception as e:
        log.error(f"fetch_candles: {e}")
        return []


def round_levels(price, step=1000):
    """Ближайшие круглые уровни вокруг цены."""
    base = int(price / step) * step
    return [
        base - step,
        base,
        base + step,
        base + 2 * step,
    ]


def recent_extremes(candles, window):
    """Max high и min low за window свечей."""
    if len(candles) < window:
        window = len(candles)
    recent = candles[-window:]
    if not recent:
        return None, None
    max_high = max(c["high"] for c in recent)
    min_low = min(c["low"] for c in recent)
    return round(max_high, 2), round(min_low, 2)


def find_touches(candles, level, tolerance_pct=0.3):
    """Сколько раз цена касалась уровня (в пределах tolerance)."""
    tol = level * tolerance_pct / 100
    touches = 0
    for c in candles:
        if c["low"] - tol <= level <= c["high"] + tol:
            touches += 1
    return touches


def find_supports_resistances(candles, current_price, top_n=5):
    """
    Ищем уровни где цена отскакивала.
    Уровень = локальный min (для поддержки) или max (для сопротивления).
    """
    if len(candles) < 20:
        return [], []

    # --- Локальные экстремумы ---
    local_lows = []
    local_highs = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        # Локальный минимум
        if (c["low"] < candles[i - 1]["low"] and
            c["low"] < candles[i - 2]["low"] and
            c["low"] < candles[i + 1]["low"] and
            c["low"] < candles[i + 2]["low"]):
            local_lows.append(c["low"])

        # Локальный максимум
        if (c["high"] > candles[i - 1]["high"] and
            c["high"] > candles[i - 2]["high"] and
            c["high"] > candles[i + 1]["high"] and
            c["high"] > candles[i + 2]["high"]):
            local_highs.append(c["high"])

    # --- Поддержки (ниже текущей цены) ---
    supports = []
    for low in sorted(set(local_lows)):
        if low < current_price:
            touches = find_touches(candles, low)
            if touches >= 2:
                distance_pct = round((current_price - low) / current_price * 100, 2)
                supports.append({
                    "price": round(low, 2),
                    "touches": touches,
                    "distance_pct": distance_pct,
                    "strength": min(5, touches),
                })

    # Сортируем: ближайшая поддержка первая
    supports.sort(key=lambda x: x["distance_pct"])
    supports = supports[:top_n]

    # --- Сопротивления (выше текущей цены) ---
    resistances = []
    for high in sorted(set(local_highs)):
        if high > current_price:
            touches = find_touches(candles, high)
            if touches >= 2:
                distance_pct = round((high - current_price) / current_price * 100, 2)
                resistances.append({
                    "price": round(high, 2),
                    "touches": touches,
                    "distance_pct": distance_pct,
                    "strength": min(5, touches),
                })

    resistances.sort(key=lambda x: x["distance_pct"])
    resistances = resistances[:top_n]

    return supports, resistances


def volume_profile(candles, bins=20, top_n=5):
    """Уровни с максимальным объёмом."""
    if not candles:
        return []

    lows = [c["low"] for c in candles]
    highs = [c["high"] for c in candles]
    min_p = min(lows)
    max_p = max(highs)
    step = (max_p - min_p) / bins

    if step <= 0:
        return []

    profile = {}
    for c in candles:
        mid = (c["high"] + c["low"]) / 2
        bucket = int((mid - min_p) / step)
        bucket = min(bucket, bins - 1)
        profile[bucket] = profile.get(bucket, 0) + c["volume"]

    sorted_buckets = sorted(profile.items(), key=lambda x: x[1], reverse=True)[:top_n]

    result = []
    for bucket, vol in sorted_buckets:
        price = min_p + bucket * step + step / 2
        result.append({
            "price": round(price, 2),
            "volume": round(vol, 2),
        })
    return result


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — анализ уровней")
    candles = fetch_candles(symbol, limit=2000)
    if not candles:
        log.warning(f"{symbol}: свечей нет")
        return None

    log.info(f"   Свечей: {len(candles)}")

    current = candles[-1]
    current_price = current["close"]
    log.info(f"   Текущая цена: ${current_price:,.2f}")

    # --- Round levels ---
    rounds = round_levels(current_price, step=1000)
    round_levels_list = []
    for r in rounds:
        distance_pct = round((r - current_price) / current_price * 100, 2)
        round_levels_list.append({
            "price": r,
            "distance_pct": distance_pct,
            "type": "round",
        })

    # --- Recent extremes ---
    h7, l7 = recent_extremes(candles, 168)
    h30, l30 = recent_extremes(candles, 720)
    h90, l90 = recent_extremes(candles, 2160)

    # --- Supports / Resistances ---
    supports, resistances = find_supports_resistances(candles, current_price, top_n=5)

    # --- Volume profile ---
    vol_profile = volume_profile(candles, bins=20, top_n=5)

    return {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "round_levels": round_levels_list,
        "extremes": {
            "7d": {"high": h7, "low": l7},
            "30d": {"high": h30, "low": l30},
            "90d": {"high": h90, "low": l90},
        },
        "supports": supports,
        "resistances": resistances,
        "volume_profile": vol_profile,
    }


def main():
    log.info("=" * 60)
    log.info("📍 ARGUS-Trader LEVELS")
    log.info("=" * 60)

    all_analysis = {}

    for symbol in SYMBOLS:
        analysis = analyze_symbol(symbol)
        if not analysis:
            continue

        all_analysis[symbol] = analysis

        # Лог
        log.info("")
        log.info(f"   Круглые уровни:")
        for r in analysis["round_levels"]:
            marker = "←" if r["distance_pct"] > 0 else ""
            log.info(f"     ${r['price']:,} ({r['distance_pct']:+.2f}%) {marker}")

        if analysis["supports"]:
            log.info(f"   Поддержки:")
            for s in analysis["supports"][:3]:
                log.info(f"     ${s['price']:,} — "
                         f"{s['touches']} касаний "
                         f"({s['distance_pct']:.2f}%)")

        if analysis["resistances"]:
            log.info(f"   Сопротивления:")
            for r in analysis["resistances"][:3]:
                log.info(f"     ${r['price']:,} — "
                         f"{r['touches']} касаний "
                         f"({r['distance_pct']:+.2f}%)")

        log.info("")

    try:
        with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"💾 {ANALYSIS_FILE.name} сохранён")
    except Exception as e:
        log.error(f"save analysis: {e}")

    log.info("=" * 60)
    log.info("✅ LEVELS DONE")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()