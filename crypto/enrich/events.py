# ============================================================
# ARGUS-Trader — EVENTS v3
# ------------------------------------------------------------
# v3: adaptive thresholds via percentile (90/85/15)
#     over last 30 days of features.
#     Thresholds logged in JSON for traceability.
# v2: + funding_spike, oi_spike, ls_extreme, rsi_extreme
# v1: rise_1h, fall_1h, rise_4h, fall_4h,
#     new_high_7d, new_low_7d, volume_spike
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
# АДАПТИВНЫЕ ПОРОГИ (percentile)
# ============================================================
THRESHOLD_WINDOW_DAYS = 30
THRESHOLD_LOOKBACK_HOURS = THRESHOLD_WINDOW_DAYS * 24

PCT_MOVE = 90
PCT_VOLUME = 90
PCT_FUNDING = 90
PCT_OI = 90
PCT_LS_HIGH = 85
PCT_LS_LOW = 15

# Fallback (если данных мало)
FALLBACK_RISE_1H = 1.5
FALLBACK_RISE_4H = 3.0
FALLBACK_VOLUME = 3.0
FALLBACK_FUNDING = 0.05
FALLBACK_OI = 5.0
FALLBACK_LS_HIGH = 1.5
FALLBACK_LS_LOW = 0.7
MIN_SAMPLES_FOR_QUANTILE = 100

RSI_OVERBOUGHT = 70
RSI_OVERSOLD = 30
RSI_PERIOD = 14


def percentile(values, p):
    """Simple percentile (linear interpolation)."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


def fetch_data(symbol, limit=2000):
    """Returns candles + features merged by timestamp."""
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
                    "SELECT timestamp, change_pct, volume_ratio_24h, "
                    "funding_rate, oi_change_pct, ls_ratio "
                    "FROM features_hourly WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                features_rows = list(reversed(cur.fetchall()))

        fmap = {}
        for r in features_rows:
            fmap[r[0]] = {
                "change_pct": float(r[1]) if r[1] is not None else 0,
                "volume_ratio": float(r[2]) if r[2] is not None else 0,
                "funding_rate": float(r[3]) if r[3] is not None else None,
                "oi_change_pct": float(r[4]) if r[4] is not None else None,
                "ls_ratio": float(r[5]) if r[5] is not None else None,
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
                "funding_rate": f.get("funding_rate"),
                "oi_change_pct": f.get("oi_change_pct"),
                "ls_ratio": f.get("ls_ratio"),
            })
        return result
    except Exception as e:
        log.error(f"fetch_data: {e}")
        return []


def compute_thresholds(data):
    """Adaptive thresholds from last 30 days."""
    if len(data) < MIN_SAMPLES_FOR_QUANTILE:
        log.warning(
            "   few data (%d < %d), fallback thresholds",
            len(data), MIN_SAMPLES_FOR_QUANTILE,
        )
        return {
            "source": "fallback",
            "rise_1h_pct": FALLBACK_RISE_1H,
            "rise_4h_pct": FALLBACK_RISE_4H,
            "volume_ratio": FALLBACK_VOLUME,
            "funding_pct": FALLBACK_FUNDING,
            "oi_pct": FALLBACK_OI,
            "ls_high": FALLBACK_LS_HIGH,
            "ls_low": FALLBACK_LS_LOW,
            "samples": len(data),
        }

    changes = [abs(d["change_pct"]) for d in data]
    changes = [c for c in changes if c > 0]

    sums_4h = []
    for i in range(3, len(data)):
        s = sum(data[j]["change_pct"] for j in range(i - 3, i + 1))
        sums_4h.append(abs(s))
    sums_4h = [s for s in sums_4h if s > 0]

    volumes = [d["volume_ratio"] for d in data if d["volume_ratio"]]
    fundings = [
        abs(d["funding_rate"]) for d in data
        if d["funding_rate"] is not None
    ]
    ois = [
        abs(d["oi_change_pct"]) for d in data
        if d["oi_change_pct"] is not None
    ]
    lsr = [
        d["ls_ratio"] for d in data
        if d["ls_ratio"] is not None
    ]

    thresholds = {
        "source": "quantile",
        "samples": len(data),
        "rise_1h_pct": round(
            percentile(changes, PCT_MOVE), 4
        ) if changes else FALLBACK_RISE_1H,
        "rise_4h_pct": round(
            percentile(sums_4h, PCT_MOVE), 4
        ) if sums_4h else FALLBACK_RISE_4H,
        "volume_ratio": round(
            percentile(volumes, PCT_VOLUME), 4
        ) if volumes else FALLBACK_VOLUME,
        "funding_pct": round(
            percentile(fundings, PCT_FUNDING), 6
        ) if fundings else FALLBACK_FUNDING,
        "oi_pct": round(
            percentile(ois, PCT_OI), 4
        ) if ois else FALLBACK_OI,
        "ls_high": round(
            percentile(lsr, PCT_LS_HIGH), 4
        ) if lsr else FALLBACK_LS_HIGH,
        "ls_low": round(
            percentile(lsr, PCT_LS_LOW), 4
        ) if lsr else FALLBACK_LS_LOW,
    }
    return thresholds


def fetch_existing_events(symbol):
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


def compute_rsi_series(closes, period=RSI_PERIOD):
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out

    gains = []
    losses = []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(0, diff))
        losses.append(max(0, -diff))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100 - 100 / (1 + rs)

    for i in range(period + 1, n):
        idx = i - 1
        avg_gain = (avg_gain * (period - 1) + gains[idx]) / period
        avg_loss = (avg_loss * (period - 1) + losses[idx]) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100 - 100 / (1 + rs)
    return out


def detect_events(symbol, data, th):
    """Detect events using adaptive thresholds."""
    events = []
    if len(data) < 2:
        return events

    rise_1h = th["rise_1h_pct"]
    rise_4h = th["rise_4h_pct"]
    vol_th = th["volume_ratio"]
    fund_th = th["funding_pct"]
    oi_th = th["oi_pct"]
    ls_high = th["ls_high"]
    ls_low = th["ls_low"]

    closes = [d["close"] for d in data]
    rsi_series = compute_rsi_series(closes, RSI_PERIOD)

    for i in range(len(data)):
        row = data[i]
        ts = row["timestamp"]

        # --- rise_1h / fall_1h ---
        if row["change_pct"] >= rise_1h:
            events.append({
                "timestamp": ts,
                "event_type": "rise_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["change_pct"], 4),
                "duration_hours": 1,
                "threshold": rise_1h,
            })
        elif row["change_pct"] <= -rise_1h:
            events.append({
                "timestamp": ts,
                "event_type": "fall_1h",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(abs(row["change_pct"]), 4),
                "duration_hours": 1,
                "threshold": rise_1h,
            })

        # --- rise_4h / fall_4h ---
        if i >= 3:
            sum_4h = sum(
                data[j]["change_pct"] for j in range(i - 3, i + 1)
            )
            if sum_4h >= rise_4h:
                events.append({
                    "timestamp": ts,
                    "event_type": "rise_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(sum_4h, 4),
                    "duration_hours": 4,
                    "threshold": rise_4h,
                })
            elif sum_4h <= -rise_4h:
                events.append({
                    "timestamp": ts,
                    "event_type": "fall_4h",
                    "change_pct": round(sum_4h, 4),
                    "magnitude": round(abs(sum_4h), 4),
                    "duration_hours": 4,
                    "threshold": rise_4h,
                })

        # --- new_high_7d / new_low_7d ---
        if i >= 167:
            prev_high = max(
                data[j]["high"] for j in range(i - 167, i)
            )
            if row["high"] > prev_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_high_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(row["high"] - prev_high, 4),
                    "duration_hours": 168,
                    "threshold": None,
                })

            prev_low = min(
                data[j]["low"] for j in range(i - 167, i)
            )
            if row["low"] < prev_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "new_low_7d",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(prev_low - row["low"], 4),
                    "duration_hours": 168,
                    "threshold": None,
                })

        # --- volume_spike ---
        if row["volume_ratio"] >= vol_th:
            events.append({
                "timestamp": ts,
                "event_type": "volume_spike",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(row["volume_ratio"], 4),
                "duration_hours": 1,
                "threshold": vol_th,
            })

        # --- funding_spike ---
        fr = row.get("funding_rate")
        if fr is not None:
            if fr >= fund_th:
                events.append({
                    "timestamp": ts,
                    "event_type": "funding_spike_pos",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(fr, 6),
                    "duration_hours": 1,
                    "threshold": fund_th,
                })
            elif fr <= -fund_th:
                events.append({
                    "timestamp": ts,
                    "event_type": "funding_spike_neg",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(abs(fr), 6),
                    "duration_hours": 1,
                    "threshold": fund_th,
                })

        # --- oi_spike ---
        oic = row.get("oi_change_pct")
        if oic is not None and abs(oic) >= oi_th:
            events.append({
                "timestamp": ts,
                "event_type": "oi_spike",
                "change_pct": round(row["change_pct"], 4),
                "magnitude": round(abs(oic), 4),
                "duration_hours": 1,
                "threshold": oi_th,
            })

        # --- ls_extreme ---
        lsr = row.get("ls_ratio")
        if lsr is not None:
            if lsr >= ls_high:
                events.append({
                    "timestamp": ts,
                    "event_type": "ls_long_extreme",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(lsr, 4),
                    "duration_hours": 1,
                    "threshold": ls_high,
                })
            elif lsr <= ls_low:
                events.append({
                    "timestamp": ts,
                    "event_type": "ls_short_extreme",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(lsr, 4),
                    "duration_hours": 1,
                    "threshold": ls_low,
                })

        # --- rsi_extreme (fixed thresholds) ---
        rsi_val = rsi_series[i] if i < len(rsi_series) else None
        if rsi_val is not None:
            if rsi_val >= RSI_OVERBOUGHT:
                events.append({
                    "timestamp": ts,
                    "event_type": "rsi_overbought",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(rsi_val, 2),
                    "duration_hours": 1,
                    "threshold": RSI_OVERBOUGHT,
                })
            elif rsi_val <= RSI_OVERSOLD:
                events.append({
                    "timestamp": ts,
                    "event_type": "rsi_oversold",
                    "change_pct": round(row["change_pct"], 4),
                    "magnitude": round(rsi_val, 2),
                    "duration_hours": 1,
                    "threshold": RSI_OVERSOLD,
                })

    return events


def save_events(symbol, events):
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
                            "(symbol, timestamp, event_type, "
                            "change_pct, magnitude, duration_hours) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (
                                symbol, e["timestamp"], e["event_type"],
                                e["change_pct"], e["magnitude"],
                                e["duration_hours"],
                            ),
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
    data = fetch_data(symbol, limit=THRESHOLD_LOOKBACK_HOURS)
    if not data:
        log.warning(f"{symbol}: данных нет")
        return None

    log.info(f"   Свечей: {len(data)}")

    th = compute_thresholds(data)
    log.info(f"   🎚 thresholds ({th['source']}):")
    log.info(f"      rise_1h  = {th['rise_1h_pct']}%")
    log.info(f"      rise_4h  = {th['rise_4h_pct']}%")
    log.info(f"      volume   = x{th['volume_ratio']}")
    log.info(f"      funding  = {th['funding_pct']}%")
    log.info(f"      oi       = {th['oi_pct']}%")
    log.info(f"      ls_high  = {th['ls_high']}")
    log.info(f"      ls_low   = {th['ls_low']}")

    existing = fetch_existing_events(symbol)
    log.info(f"   Уже в БД: {len(existing)}")

    all_events = detect_events(symbol, data, th)

    new_events = [
        e for e in all_events
        if (e["timestamp"], e["event_type"]) not in existing
    ]

    log.info(f"   Найдено всего: {len(all_events)}")
    log.info(f"   Новых для записи: {len(new_events)}")

    by_type = {}
    for e in all_events:
        by_type[e["event_type"]] = by_type.get(e["event_type"], 0) + 1
    log.info(f"   По типам:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        log.info(f"     {t}: {c}")

    saved = save_events(symbol, new_events)
    log.info(f"   ✅ Добавлено в БД: {saved}")

    return {
        "symbol": symbol,
        "total_events": len(all_events),
        "new_events": len(new_events),
        "saved": saved,
        "by_type": by_type,
        "thresholds": th,
        "recent_events": [
            {
                "timestamp": str(e["timestamp"]),
                "type": e["event_type"],
                "change_pct": e["change_pct"],
                "magnitude": e["magnitude"],
                "threshold": e.get("threshold"),
            }
            for e in sorted(
                all_events, key=lambda x: x["timestamp"], reverse=True
            )[:20]
        ],
    }


def main():
    log.info("=" * 60)
    log.info("⚡ ARGUS-Trader EVENTS v3 (adaptive)")
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