# ============================================================
# ARGUS-Trader — ANOMALY DETECTORS
# ------------------------------------------------------------
# 4 детектора манипуляций:
#   1. pump_dump       — резкий рост + объём + падение
#   2. cross_exchange  — расхождение цен между биржами
#   3. wash_trading    — огромный объём без движения
#   4. stop_hunting    — свеча с длинным хвостом
# Пишет в anomaly_log + Telegram.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from config import SYMBOLS, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from db import get_connection, close_connection, log_anomaly

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.anomaly")


# ============================================================
# ПОРОГИ ДЕТЕКТОРОВ
# ============================================================
PUMP_PCT = 5.0                # +5% за час = pump
PUMP_VOLUME_RATIO = 3.0       # объём ×3 от среднего
DUMP_PCT = -3.0               # следующий час -3% = подтверждение
WASH_VOLUME_RATIO = 10.0      # объём ×10
WASH_RANGE_PCT = 0.5          # при этом range < 0.5%
WICK_PCT = 50.0               # хвост > 50% от range
CROSS_DIFF_PCT = 0.5          # расхождение цен > 0.5%


def notify(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        log.warning(f"Telegram: {e}")


# ============================================================
# ЗАГРУЗeКА СВЕЧ}")
ЕЙ
# ============================================================
def fetch_recent_candles(symbol, hours=48):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc) - timedelta(hours=hours)
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s AND timeframe = '1h' "
                    "AND timestamp >= %s ORDER BY timestamp",
                    (symbol, start),
                )
                return [
                    {
                        "timestamp": r[0],
                        "open": float(r[1]),
                        "high": float(r[2]),
                        "low": float(r[3]),
                        "close": float(r[4]),
                        "volume": float(r[5]),
                    }
                    for r in cur.fetchall()
                ]
    except Exception as e       :
        log.error(f"fetch_re returncent_candles: { []


def fetch_avg_volume(symbol, hours=24):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc) - timedelta(hours=hours)
                cur.execute(
                    "SELECT AVG(volume) FROM candles "
                    "WHERE symbol = %s AND timeframe = '1h' AND timestamp >= %s",
                    (symbol, start),
                )
                row = cur.fetchone()
                return float(row[0]) if row and row[0] else 0
    except Exception:
        return 0


def fetch_cross_check(symbol, hours=1):
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc) - timedelta(hours=hours)
                cur.execute(
                    "SELECT timestamp, diff_pct, price_primary, price_secondary "
                    "FROM cross_check WHERE symbol = %s AND timestamp >= %s "
                    "ORDER BY timestamp DESC LIMIT 5",
                    (symbol, start),
                )
                return [
                    {
                        "timestamp": r[0],
                        "diff_pct": float(r[1]) if r[1] else 0,
                        "primary": float(r[2]) if r[2] else 0,
                        "secondary": float(r[3]) if r[3] else 0,
                    }
                    for r in cur.fetchall()
                ]
    except Exception:
        return []


def anomaly_exists(symbol, anomaly_type, hours=24):
    """Проверка, была ли уже такая аномалия за последние N часов."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                start = datetime.now(timezone.utc) - timedelta(hours=hours)
                cur.execute(
                    "SELECT COUNT(*) FROM anomaly_log "
                    "WHERE symbol = %s AND anomaly_type = %s AND created_at >= %s",
                    (symbol, anomaly_type, start),
                )
                return cur.fetchone()[0] > 0
    except Exception:
        return False


# ============================================================
# ДЕТЕКТОР 1: PUMP & DUMP
# ============================================================
def detect_pump_dump(symbol, candles, avg_volume):
    if len(candles) < 2:
        return None

    # Смотрим последние 3 часа — ищем pump
    for i in range(max(0, len(candles) - 3), len(candles) - 1):
        c = candles[i]
        if c["open"] == 0:
            continue

        change = (c["close"] - c["open"]) / c["open"] * 100

        if change >= PUMP_PCT and avg_volume > 0:
            vol_ratio = c["volume"] / avg_volume

            if vol_ratio >= PUMP_VOLUME_RATIO:
                # Проверяем следующий час — падение?
                next_c = candles[i + 1] if i + 1 < len(candles) else None
                next_change = None
                if next_c and next_c["open"] != 0:
                    next_change = (next_c["close"] - next_c["open"]) / next_c["open"] * 100

                if next_change is not None and next_change <= DUMP_PCT:
                    return {
                        "timestamp": c["timestamp"],
                        "change_pct": round(change, 2),
                        "next_change": round(next_change, 2),
                        "volume_ratio": round(vol_ratio, 2),
                    }
    return None


# ============================================================
# ДЕТЕКТОР 2: CROSS-EXCHANGE
# ============================================================
def detect_cross_exchange(symbol, cross_checks):
    for cc in cross_checks:
        if abs(cc["diff_pct"]) > CROSS_DIFF_PCT:
            return {
                "timestamp": cc["timestamp"],
                "diff_pct": cc["diff_pct"],
                "primary": cc["primary"],
                "secondary": cc["secondary"],
            }
    return None


# ============================================================
# ДЕТЕКТОР 3: WASH TRADING
# ============================================================
def detect_wash_trading(symbol, candles, avg_volume):
    if not candles or avg_volume <= 0:
        return None

    # Последние 6 часов
    for c in candles[-6:]:
        if c["open"] == 0:
            continue
        rng_pct = (c["high"] - c["low"]) / c["open"] * 100
        vol_ratio = c["volume"] / avg_volume

        if vol_ratio >= WASH_VOLUME_RATIO and rng_pct < WASH_RANGE_PCT:
            return {
                "timestamp": c["timestamp"],
                "range_pct": round(rng_pct, 3),
                "volume_ratio": round(vol_ratio, 2),
            }
    return None


# ============================================================
# ДЕТЕКТОР 4: STOP HUNTING
# ============================================================
def detect_stop_hunting(symbol, candles):
    if not candles:
        return None

    for c in candles[-3:]:
        rng = c["high"] - c["low"]
        if rng <= 0:
            continue

        upper_wick = c["high"] - max(c["open"], c["close"])
        lower_wick = min(c["open"], c["close"]) - c["low"]

        upper_pct = upper_wick / rng * 100
        lower_pct = lower_wick / rng * 100

        if upper_pct >= WICK_PCT:
            return {
                "timestamp": c["timestamp"],
                "wick_type": "upper",
                "wick_pct": round(upper_pct, 1),
                "price": c["close"],
            }
        if lower_pct >= WICK_PCT:
            return {
                "timestamp": c["timestamp"],
                "wick_type": "lower",
                "wick_pct": round(lower_pct, 1),
                "price": c["close"],
            }
    return None


# ============================================================
# ОБРАБОТКА СИМВОЛА
# ============================================================
def process_symbol(symbol):
    log.info(f"🔍 {symbol}")
    candles = fetch_recent_candles(symbol, hours=48)
    if not candles:
        log.warning(f"   нет свечей")
        return 0

    avg_volume = fetch_avg_volume(symbol, hours=24)
    log.info(f"   свечей: {len(candles)} | avg_volume={avg_volume:.2f}")

    found = 0

    # --- Pump & Dump ---
    pd = detect_pump_dump(symbol, candles, avg_volume)
    if pd and not anomaly_exists(symbol, "pump_dump"):
        log_anomaly(
            symbol=symbol, timestamp=pd["timestamp"],
            anomaly_type="pump_dump", severity="high",
            details=pd,
        )
        notify(
            f"🚨 <b>PUMP & DUMP</b> {symbol}\n"
            f"Рост: <b>{pd['change_pct']:+.2f}%</b>\n"
            f"Падение: <b>{pd['next_change']:+.2f}%</b>\n"
            f"Объём: ×{pd['volume_ratio']}"
        )
        found += 1
        log.info(f"   🚨 pump_dump найден")

    # --- Cross-exchange ---
    cross_checks = fetch_cross_check(symbol, hours=1)
    ce = detect_cross_exchange(symbol, cross_checks)
    if ce and not anomaly_exists(symbol, "cross_exchange"):
        log_anomaly(
            symbol=symbol, timestamp=ce["timestamp"],
            anomaly_type="cross_exchange", severity="medium",
            details=ce,
        )
        notify(
            f"⚠️ <b>Cross-exchange</b> {symbol}\n"
            f"Расхождение: <b>{ce['diff_pct']:.2f}%</b>\n"
            f"OKX: ${ce['primary']:,.2f}\n"
            f"CG: ${ce['secondary']:,.2f}"
        )
        found += 1
        log.info(f"   ⚠️ cross_exchange найден")

    # --- Wash trading ---
    wt = detect_wash_trading(symbol, candles, avg_volume)
    if wt and not anomaly_exists(symbol, "wash_trading"):
        log_anomaly(
            symbol=symbol, timestamp=wt["timestamp"],
            anomaly_type="wash_trading", severity="high",
            details=wt,
        )
        notify(
            f"🚨 <b>WASH TRADING</b> {symbol}\n"
            f"Объём ×{wt['volume_ratio']} при range {wt['range_pct']:.2f}%"
        )
        found += 1
        log.info(f"   🚨 wash_trading найден")

    # --- Stop hunting ---
    sh = detect_stop_hunting(symbol, candles)
    if sh and not anomaly_exists(symbol, "stop_hunting"):
        log_anomaly(
            symbol=symbol, timestamp=sh["timestamp"],
            anomaly_type="stop_hunting", severity="medium",
            details=sh,
        )
        found += 1
        log.info(f"   ⚠️ stop_hunting найден ({sh['wick_type']} wick {sh['wick_pct']}%)")

    log.info(f"   ✅ Найдено аномалий: {found}")
    return found


def main():
    log.info("=" * 60)
    log.info("🚨 ARGUS-Trader ANOMALY DETECTORS")
    log.info("=" * 60)

    total = 0
    for symbol in SYMBOLS:
        total += process_symbol(symbol)
        log.info("")

    log.info("=" * 60)
    log.info(f"✅ ANOMALY DONE. Найдено: {total}")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()