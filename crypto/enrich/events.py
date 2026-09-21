# ============================================================
# ARGUS-Trader — EVENTS
# ------------------------------------------------------------
# Находит значимые события в истории:
#   rise_1h, fall_1h, rise_4h, fall_4h,
#   new_high_7d, new_low_7d, volume_spike
# Записывает в events (Supabase) + JSON для causal.py.
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

from config import SYMBOLS
from db import get_connection, close_connection

DATA_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_FILE = DATA_DIR / "events_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.events")


# ============================================================
# ПОРОГИ
# ============================================================
RISE_1H_PCT = 1.5
FALL_1H_PCT = -1.5
RISE_4H_PCT = 3.0
FALL_4H_PCT = -3.0
VOLUME_SPIKE_RATIO = 3.0


def fetch_data(symbol, limit=2000):
    """Возвращает свечи + features, объединённые по timestamp."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                candles_rows = list(reversed(cur.fetchall()))

                cur.execute(
                    "SELECT timestamp, change_pct, volume_ratio_24h "
                    "FROM features_hourly WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                features_rows = list(reversed(cur.fetchall()))

        # Мапим features по timestamp
        fmap = {}
        for r in features_rows:
            fmap[r[0]] = {
                "change_pct": float(r[1]) if r[1] is not None else 0,
                "volume_ratio": float(r[2]) if r[2] is not None else 0,
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
            })
        return result
    except Exception as e:
        log.error(f"fetch_data: {e}")
        return []


def fetch_existing_events(symbol):
    """Возвращает set (timestamp, event_type) уже существующих событий."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, event_type FROM events WHERE symbol = %s",
                    (symbol,),
                )
                return {(r[0], r[1]) for r in cur.fetchall()}
    except Exception as e:
        log.error(f"fetch_existing_events: {e}")
        return set()


def detect_events(symbol, data):
    """Проходит по свечам, возвращает список событий."""
    events = []
    if len(data) < 2:
        return events

    # Для rolling-окон
    for i in range(len(data)):
        row = data[i]
        ts = row["timestamp"]

        # --- rise_1h / fall_1h ---
        if row["change_pct"] >= RISE_1H_PCT:
            events.append({
                "timestamp": ts,
                "event_type": "rise_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["change_pct"], 4),
                "duration_hours": 1,
            })
        elif row["change_pct"] <= FALL_1H_PCT:
            events.append({
                "timestamp": ts,
                "event_type": "fall_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(abs(row["change_pct"]), 4),
                "duration_hours": 1,
            })

        # --- rise_4h / fall_4h ---
        if i >= 3:
            sum_4h = sum(data[j]["change_pct"] for j in range(i - 3, i + 1))
            if sum_4h >= RISE_4H_PCT:
                events.append({
                    "timestamp": ts,
                    "event_type": "rise_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(sum_4h, 4),
                    "duration_hours": 4,
                })
            elif sum_4h <= FALL_4H_PCT:
                events.append({
                    "timestamp": ts,
                    "event_type": "fall_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(abs(sum_4h), 4),
                    "duration_hours": 4,
                })

        # --- new_high_7d ---
        if i >= 167:
            prev_high = max(data[j]["high"] for j in range(i - 167, i))
            if row["high"] > prev_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_high_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(row["high"] - prev_high, 4),
                    "duration_hours": 168,
                })

        # --- new_low_7d ---
        if i >= 167:
            prev_low = min(data[j]["low"] for j in range(i - 167, i))
            if row["low"] < prev_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_low_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(prev_low - row["low"], 4),
                    "duration_hours": 168,
                })

        # --- volume_spike ---
        if row["volume_ratio"] >= VOLUME_SPIKE_RATIO:
            events.append({
                "timestamp": ts,
                "event_type": "volume_spike",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["volume_ratio"], 4),
                "duration_hours": 1,
            })

    return events


def save_events(symbol, events):
    """Сохраняет события в БД. Возвращает число добавленных."""
    if not events:
        return 0

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for e in events:
                    try:
                        cur.execute(
                            "INSERT INTO events "
                            "(symbol, timestamp, event_type, change_pct, magnitude, duration_hours) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (symbol, e["timestamp"], e["event_type"],
                             e["change_pct"], e["magnitude"], e["duration_hours"]),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as ex:
                        log.warning(f"INSERT event skip: {ex}")
    except Exception as e:
        log.error(f"save_events: {e}")
    return added


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — детект событий")
    data = fetch_data(symbol, limit=2000)
    if not data:
        log.warning(f"{symbol}: данных нет")
        return None

    log.info(f"   Свечей: {len(data)}")

    # Уже существующие — не дублируем
    existing = fetch_existing_events(symbol)
    log.info(f"   Уже в БД: {len(existing)}")

    # Все возможные события
    all_events = detect_events(symbol, data)

    # Оставляем только новые
    new_events = [
        e for e in all_events
        if (e["timestamp"], e["event_type"]) not in existing
    ]

    log.info(f"   Найдено всего: {len(all_events)}")
    log.info(f"   Новых для записи: {len(new_events)}")

    # Группируем по типам для лога
    by_type = {}
    for e in all_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
    log.info(f"   По типам:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        log.info(f"     {t}: {c}")

    # Сохраняем
    saved = save_events(symbol, new_events)
    log.info(f"   ✅ Добавлено в БД: {saved}")

    return {
        "symbol": symbol,
        "total_events": len(all_events),
        "new_events": len(new_events),
        "saved": saved,
        "by_type": by_type,
        "recent_events": [
            {
                "timestamp": str(e["timestamp"]),
                "type": e["event_type"],
                "change_pct": e["change_pct"],
                "magnitude": e["magnitude"],
            }
            for e in sorted(all_events, key=lambda x: x["timestamp"], reverse=True)[:20]
        ],
    }


def main():
    log.info("=" * 60)
    log.info("⚡ ARGUS-Trader EVENTS")
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
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"💾 {ANALYSIS_FILE.name} сохранён")
    except Exception as e:
        log.error(f"save analysis: {e}")

    log.info("=" * 60)
    log.info("✅ EVENTS DONE")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()