# ============================================================
# ARGUS-Trader — LEVELS v3
# ------------------------------------------------------------
# v3: адаптивные уровни — шаг выбирается автоматически
#     от текущей цены:
#       $85,000 → major 10k, mid 5k, minor 1k
#       $8,500  → major 1k, mid 500, minor 100
#       $850    → major 100, mid 50, minor 10
#     Диапазон: ±70% от цены (покрывает 10k-200k для BTC).
# ------------------------------------------------------------
# v2: три уровня приоритета (major/mid/minor)
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


def pick_step_size(price):
    """
    Подбирает базовый шаг по порядку цены.
    Возвращает (major_step, mid_step, minor_step).
    Логика: 1 значащая цифра цены × (1, 0.5, 0.1).
    """
    import math

    if price <= 0:
        return 1, 0.5, 0.1

    # Порядок цены: 85000 → 10000, 8500 → 1000, 850 → 100
    order = 10 ** (math.floor(math.log10(price)) - 1)  # 10% от порядка

    # Основной шаг — 10% от порядка
    major = order

    # Если major слишком маленький или большой — корректируем
    ratio = price / major
    if ratio > 50:
        major = major * 10
    elif ratio < 5:
        major = major / 10

    mid = major / 2
    minor = major / 10

    return major, mid, minor


def build_round_levels(current_price, range_pct=70):
    """
    Строит ВСЕ круглые уровни в диапазоне ±range_pct% от цены.
    Шаг определяется автоматически от цены.
    Tier: 1 = major, 2 = mid, 3 = minor (только ближние ±10%).
    """
    major, mid, minor = pick_step_size(current_price)

    lower = current_price * (1 - range_pct / 100)
    upper = current_price * (1 + range_pct / 100)

    # Защита: нижняя граница не меньше нуля
    if lower <= 0:
        lower = current_price * 0.01

    levels = []

    # --- Major (1 шаг) ---
    start = int(lower / major) * major
    end = int(upper / major + 1) * major
    v = start
    while v <= end:
        if v > 0:
            levels.append({
                "price": float(v),
                "tier": 1,
                "step": major,
            })
        v += major

    # --- Mid (0.5 шага) — исключаем Major ---
    start = int(lower / mid) * mid
    end = int(upper / mid + 1) * mid
    v = start
    while v <= end:
        if v > 0 and abs(v % major) > 1e-9:
            levels.append({
                "price": float(v),
                "tier": 2,
                "step": mid,
            })
        v += mid

    # --- Minor (0.1 шага) — только ближние ±10% ---
    near_lower = current_price * 0.90
    near_upper = current_price * 1.10
    start = int(near_lower / minor) * minor
    end = int(near_upper / minor + 1) * minor
    v = start
    while v <= end:
        if v > 0 and abs(v % mid) > 1e-9 and abs(v % major) > 1e-9:
            levels.append({
                "price": float(v),
                "tier": 3,
                "step": minor,
            })
        v += minor

    # --- Distance ---
    for lvl in levels:
        lvl["distance_pct"] = round(
            (lvl["price"] - current_price) / current_price * 100, 2
        )
        lvl["distance_abs"] = round(lvl["price"] - current_price, 2)
        lvl["position"] = "above" if lvl["price"] > current_price else "below"

    # Сортируем по близости к цене
    levels.sort(key=lambda x: abs(x["distance_pct"]))
    return levels


def recent_extremes(candles, window):
    if len(candles) < window:
        window = len(candles)
    recent = candles[-window:]
    if not recent:
        return None, None
    max_high = max(c["high"] for c in recent)
    min_low = min(c["low"] for c in recent)
    return round(max_high, 2), round(min_low, 2)


def find_touches(candles, level, tolerance_pct=0.15):
    tol = level * tolerance_pct / 100
    touches = 0
    for c in candles:
        if c["low"] - tol <= level <= c["high"] + tol:
            touches += 1
    return touches


def find_supports_resistances(candles, current_price, top_n=5):
    if len(candles) < 20:
        return [], []

    local_lows = []
    local_highs = []

    for i in range(2, len(candles) - 2):
        c = candles[i]
        if (c["low"] < candles[i - 1]["low"] and
                c["low"] < candles[i - 2]["low"] and
                c["low"] < candles[i + 1]["low"] and
                c["low"] < candles[i + 2]["low"]):
            local_lows.append(c["low"])

        if (c["high"] > candles[i - 1]["high"] and
                c["high"] > candles[i - 2]["high"] and
                c["high"] > candles[i + 1]["high"] and
                c["high"] > candles[i + 2]["high"]):
            local_highs.append(c["high"])

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

    supports.sort(key=lambda x: x["distance_pct"])
    supports = supports[:top_n]

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


def format_price(price):
    """Красивое форматирование цены."""
    if price >= 1000:
        return f"${int(price):,}"
    elif price >= 1:
        return f"${price:,.2f}"
    else:
        return f"${price:.4f}"


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — анализ уровней")
    candles = fetch_candles(symbol, limit=2000)
    if not candles:
        log.warning(f"{symbol}: свечей нет")
        return None

    log.info(f"   Свечей: {len(candles)}")

    current = candles[-1]
    current_price = current["close"]
    major, mid, minor = pick_step_size(current_price)

    log.info(f"   Текущая цена: {format_price(current_price)}")
    log.info(f"   Подобранные шаги: major={major:g} | mid={mid:g} | minor={minor:g}")

    round_levels = build_round_levels(current_price, range_pct=70)

    h7, l7 = recent_extremes(candles, 168)
    h30, l30 = recent_extremes(candles, 720)
    h90, l90 = recent_extremes(candles, 2160)

    supports, resistances = find_supports_resistances(candles, current_price, top_n=5)
    vol_profile = volume_profile(candles, bins=20, top_n=5)

    return {
        "symbol": symbol,
        "current_price": round(current_price, 2),
        "steps": {"major": major, "mid": mid, "minor": minor},
        "round_levels": round_levels,
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
    log.info("📍 ARGUS-Trader LEVELS v3 (адаптивный)")
    log.info("=" * 60)

    all_analysis = {}

    for symbol in SYMBOLS:
        analysis = analyze_symbol(symbol)
        if not analysis:
            continue

        all_analysis[symbol] = analysis

        # --- Лог ---
        log.info("")
        log.info(f"   🔵 Major:")
        major_lvls = [l for l in analysis["round_levels"] if l["tier"] == 1]
        for l in major_lvls[:10]:
            arrow = "⬆" if l["position"] == "above" else "⬇"
            log.info(f"     {arrow} {format_price(l['price'])} "
                     f"({l['distance_pct']:+.2f}%)")

        log.info(f"   🟢 Mid:")
        mid_lvls = [l for l in analysis["round_levels"] if l["tier"] == 2]
        for l in mid_lvls[:6]:
            arrow = "⬆" if l["position"] == "above" else "⬇"
            log.info(f"     {arrow} {format_price(l['price'])} "
                     f"({l['distance_pct']:+.2f}%)")

        log.info(f"   ⚪ Minor (ближние):")
        minor_lvls = [l for l in analysis["round_levels"] if l["tier"] == 3]
        for l in minor_lvls[:5]:
            arrow = "⬆" if l["position"] == "above" else "⬇"
            log.info(f"     {arrow} {format_price(l['price'])} "
                     f"({l['distance_pct']:+.2f}%)")

        if analysis["supports"]:
            log.info(f"   🛡 Поддержки:")
            for s in analysis["supports"][:3]:
                log.info(f"     {format_price(s['price'])} — "
                         f"{s['touches']} касаний "
                         f"(-{s['distance_pct']:.2f}%)")

        if analysis["resistances"]:
            log.info(f"   ⚔️ Сопротивления:")
            for r in analysis["resistances"][:3]:
                log.info(f"     {format_price(r['price'])} — "
                         f"{r['touches']} касаний "
                         f"(+{r['distance_pct']:.2f}%)")

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