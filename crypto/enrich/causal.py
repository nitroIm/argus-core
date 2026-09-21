# ============================================================
# ARGUS-Trader — CAUSAL
# ------------------------------------------------------------
# Для каждого события (из events) собирает что было ДО него:
#   - features за 1ч/4ч/24ч до
#   - funding rate, OI, LS ratio
#   - ближайшие уровни (из levels_analysis.json)
#   - 0/1 паттерн (из patterns_analysis.json)
# Пишет в causal_links + JSON.
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
ANALYSIS_FILE = DATA_DIR / "causal_analysis.json"
LEVELS_FILE = DATA_DIR / "levels_analysis.json"
PATTERNS_FILE = DATA_DIR / "patterns_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.causal")


# Окна предшествия (часов назад от события)
WINDOWS = [1, 4, 24]


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"load {path.name}: {e}")
        return default if default is not None else {}


def fetch_events(symbol, limit=200):
    """Свежие события для символа."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, timestamp, event_type, change_pct, magnitude "
                    "FROM events WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                return [
                    {
                        "id": r[0],
                        "timestamp": r[1],
                        "event_type": r[2],
                        "change_pct": float(r[3]) if r[3] else 0,
                        "magnitude": float(r[4]) if r[4] else 0,
                    }
                    for r in cur.fetchall()
                ]
    except Exception as e:
        log.error(f"fetch_events: {e}")
        return []


def fetch_existing_causal(event_id):
    """Проверяет, есть ли уже записи для события."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM causal_links WHERE event_id = %s",
                    (event_id,),
                )
                return cur.fetchone()[0]
    except Exception:
        return 0


def fetch_ohlcv_before(symbol, event_ts, hours):
    """Возвращает свечи за N часов ДО события."""
    try:
        start = event_ts - timedelta(hours=hours)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s AND timeframe = '1h' "
                    "AND timestamp >= %s AND timestamp < %s "
                    "ORDER BY timestamp",
                    (symbol, start, event_ts),
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
    except Exception as e:
        log.warning(f"fetch_ohlcv_before: {e}")
        return []


def fetch_funding_before(symbol, event_ts, hours=24):
    """Последняя funding rate ДО события."""
    try:
        start = event_ts - timedelta(hours=hours)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT rate FROM funding_rates "
                    "WHERE symbol = %s AND timestamp >= %s AND timestamp < %s "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol, start, event_ts),
                )
                row = cur.fetchone()
                return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def fetch_oi_before(symbol, event_ts, hours=24):
    """OI в начале окна и в конце — считаем % изменения."""
    try:
        start = event_ts - timedelta(hours=hours)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT oi FROM open_interest "
                    "WHERE symbol = %s AND timestamp >= %s AND timestamp < %s "
                    "ORDER BY timestamp LIMIT 1",
                    (symbol, start, event_ts),
                )
                row_start = cur.fetchone()

                cur.execute(
                    "SELECT oi FROM open_interest "
                    "WHERE symbol = %s AND timestamp >= %s AND timestamp < %s "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol, start, event_ts),
                )
                row_end = cur.fetchone()

        if not row_start or not row_end:
            return None
        oi_start = float(row_start[0]) if row_start[0] else 0
        oi_end = float(row_end[0]) if row_end[0] else 0
        if oi_start == 0:
            return None
        return round((oi_end - oi_start) / oi_start * 100, 4)
    except Exception:
        return None


def fetch_ls_before(symbol, event_ts, hours=4):
    """Последний LS ratio ДО события."""
    try:
        start = event_ts - timedelta(hours=hours)
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ls_ratio FROM long_short_ratio "
                    "WHERE symbol = %s AND timestamp >= %s AND timestamp < %s "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (symbol, start, event_ts),
                )
                row = cur.fetchone()
                return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def compute_change_pct(candles):
    """Изменение close первого → close последнего."""
    if len(candles) < 2:
        return None
    first = candles[0]["open"]
    last = candles[-1]["close"]
    if first == 0:
        return None
    return round((last - first) / first * 100, 4)


def compute_volatility(candles):
    """Std change_pct по часовым свечам."""
    if len(candles) < 2:
        return None
    changes = []
    for c in candles:
        if c["open"] == 0:
            continue
        changes.append((c["close"] - c["open"]) / c["open"] * 100)
    if len(changes) < 2:
        return None
    mean = sum(changes) / len(changes)
    var = sum((x - mean) ** 2 for x in changes) / len(changes)
    return round(var ** 0.5, 4)


def find_nearest_level(symbol, price, levels_data):
    """Ближайший круглый уровень к цене события."""
    try:
        sym_data = levels_data.get("symbols", {}).get(symbol, {})
        round_levels = sym_data.get("round_levels", [])
        if not round_levels:
            return None
        return min(round_levels, key=lambda x: abs(x["price"] - price))
    except Exception:
        return None


def get_pattern_before(symbol, event_ts, patterns_data):
    """Возвращает паттерн 0/1 за окно перед событием."""
    try:
        sym_data = patterns_data.get("symbols", {}).get(symbol, {})
        binary = sym_data.get("binary_string", "")
        if not binary:
            return None
        # Берём последние 4 символа (для события)
        return binary[-4:] if len(binary) >= 4 else binary
    except Exception:
        return None


def build_causal_entry(symbol, event, levels_data, patterns_data):
    """Собирает causal_links для одного события."""
    event_ts = event["timestamp"]

    # Если уже есть — не дублируем
    if fetch_existing_causal(event["id"]) > 0:
        return None

    # Собираем по окнам
    features_1h = None
    features_4h = None
    features_24h = None

    ohlcv_1h = fetch_ohlcv_before(symbol, event_ts, 1)
    if ohlcv_1h:
        features_1h = {
            "change_pct": compute_change_pct(ohlcv_1h),
            "volatility": compute_volatility(ohlcv_1h),
            "candles": len(ohlcv_1h),
        }

    ohlcv_4h = fetch_ohlcv_before(symbol, event_ts, 4)
    if ohlcv_4h:
        features_4h = {
            "change_pct": compute_change_pct(ohlcv_4h),
            "volatility": compute_volatility(ohlcv_4h),
            "candles": len(ohlcv_4h),
        }

    ohlcv_24h = fetch_ohlcv_before(symbol, event_ts, 24)
    if ohlcv_24h:
        features_24h = {
            "change_pct": compute_change_pct(ohlcv_24h),
            "volatility": compute_volatility(ohlcv_24h),
            "candles": len(ohlcv_24h),
        }

    # Деривативы
    funding = fetch_funding_before(symbol, event_ts, 24)
    oi_change = fetch_oi_before(symbol, event_ts, 24)
    ls_ratio = fetch_ls_before(symbol, event_ts, 4)

    # Уровни
    entry_price = event.get("magnitude", 0)
    if ohlcv_1h:
        entry_price = ohlcv_1h[-1]["close"] if ohlcv_1h else 0

    nearest_level = find_nearest_level(symbol, entry_price, levels_data)

    # Паттерн
    pattern = get_pattern_before(symbol, event_ts, patterns_data)

    return {
        "event_id": event["id"],
        "symbol": symbol,
        "event_type": event["event_type"],
        "event_ts": event_ts,
        "features_1h": features_1h,
        "features_4h": features_4h,
        "features_24h": features_24h,
        "funding_rate": funding,
        "oi_change_pct": oi_change,
        "ls_ratio": ls_ratio,
        "nearest_level": nearest_level,
        "pattern_before": pattern,
    }


def save_causal_links(symbol, entries):
    """Записывает entries в causal_links."""
    if not entries:
        return 0
    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for e in entries:
                    try:
                        cur.execute(
                            "INSERT INTO causal_links "
                            "(event_id, hours_before, funding_rate, oi_change_pct, "
                            "ls_ratio, taker_ratio, volume_ratio, volatility, change_pct, "
                            "is_anomaly) "
                            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (event_id, hours_before) DO NOTHING",
                            (
                                e["event_id"], 24,
                                e.get("funding_rate"),
                                e.get("oi_change_pct"),
                                e.get("ls_ratio"),
                                None,
                                None,
                                (e.get("features_24h") or {}).get("volatility"),
                                (e.get("features_24h") or {}).get("change_pct"),
                                False,
                            ),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as ex:
                        log.warning(f"INSERT causal skip: {ex}")
    except Exception as e:
        log.error(f"save_causal_links: {e}")
    return added


def analyze_symbol(symbol, levels_data, patterns_data):
    log.info(f"📊 {symbol} — causal analysis")
    events = fetch_events(symbol, limit=200)
    if not events:
        log.warning(f"{symbol}: событий нет")
        return None

    log.info(f"   Событий в БД: {len(events)}")

    entries = []
    for ev in events:
        entry = build_causal_entry(symbol, ev, levels_data, patterns_data)
        if entry:
            entries.append(entry)

    log.info(f"   Новых для обработки: {len(entries)}")

    # Сохраняем в БД
    saved = save_causal_links(symbol, entries)
    log.info(f"   ✅ Добавлено в causal_links: {saved}")

    # Статистика по типам событий — какие lead-сигналы чаще
    summary = {}
    for e in entries:
        t = e["event_type"]
        if t not in summary:
            summary[t] = {
                "count": 0,
                "funding_avg": [],
                "oi_change_avg": [],
                "ls_avg": [],
                "change_24h_avg": [],
            }
        s = summary[t]
        s["count"] += 1
        if e.get("funding_rate") is not None:
            s["funding_avg"].append(e["funding_rate"])
        if e.get("oi_change_pct") is not None:
            s["oi_change_avg"].append(e["oi_change_pct"])
        if e.get("ls_ratio") is not None:
            s["ls_avg"].append(e["ls_ratio"])
        if e.get("features_24h") and e["features_24h"].get("change_pct") is not None:
            s["change_24h_avg"].append(e["features_24h"]["change_pct"])

    summary_out = {}
    for t, s in summary.items():
        def avg(arr):
            return round(sum(arr) / len(arr), 4) if arr else None
        summary_out[t] = {
            "count": s["count"],
            "avg_funding": avg(s["funding_avg"]),
            "avg_oi_change": avg(s["oi_change_avg"]),
            "avg_ls_ratio": avg(s["ls_avg"]),
            "avg_change_24h": avg(s["change_24h_avg"]),
        }

    log.info(f"   Сводка по событиям:")
    for t, s in summary_out.items():
        log.info(f"     {t} (N={s['count']}):")
        if s["avg_funding"] is not None:
            log.info(f"       funding={s['avg_funding']:+.6f}")
        if s["avg_oi_change"] is not None:
            log.info(f"       oi_change={s['avg_oi_change']:+.2f}%")
        if s["avg_ls_ratio"] is not None:
            log.info(f"       ls_ratio={s['avg_ls_ratio']:.3f}")
        if s["avg_change_24h"] is not None:
            log.info(f"       change_24h={s['avg_change_24h']:+.2f}%")

    return {
        "symbol": symbol,
        "total_events": len(events),
        "processed": len(entries),
        "saved": saved,
        "summary_by_type": summary_out,
    }


def main():
    log.info("=" * 60)
    log.info("🔗 ARGUS-Trader CAUSAL")
    log.info("=" * 60)

    levels_data = load_json(LEVELS_FILE, {})
    patterns_data = load_json(PATTERNS_FILE, {})
    log.info(f"   Уровни: {'✅' if levels_data else '⚠️ нет'}")
    log.info(f"   Паттерны: {'✅' if patterns_data else '⚠️ нет'}")
    log.info("")

    all_analysis = {}

    for symbol in SYMBOLS:
        result = analyze_symbol(symbol, levels_data, patterns_data)
        if result:
            all_analysis[symbol] = result
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
    log.info("✅ CAUSAL DONE")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()