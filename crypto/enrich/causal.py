# ============================================================
# ARGUS-Trader — CAUSAL v4
# ------------------------------------------------------------
# v4: market_type='futures' in load_window_data.
#     English logs (no emoji).
# v3: DB routing via symbol_conn.
# ============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

for _p in CRYPTO_ROOT.rglob("db2.py"):
    _d = str(_p.parent)
    if "__pycache__" in _d:
        continue
    if _d not in sys.path:
        sys.path.insert(0, _d)
    break

from config import SYMBOLS as CONFIG_SYMBOLS
from db import get_connection, close_connection

DB2_OK = False
get_conn_db2 = None
close_conn_db2 = None
if (os.getenv("ARGUS_DB_URL_2") or "").strip():
    try:
        from db2 import get_connection as get_conn_db2
        from db2 import close_connection as close_conn_db2
        _t = get_conn_db2()
        with _t as _c:
            with _c.cursor() as _cur:
                _cur.execute("SELECT 1")
                _cur.fetchone()
        DB2_OK = True
    except Exception as e:
        print("DB2 fail: " + str(e))

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

WINDOW_HOURS = 24
EVENT_LIMIT = 200

DEFAULT_SYMBOLS = (
    list(CONFIG_SYMBOLS) if CONFIG_SYMBOLS
    else ["BTCUSDT", "ETHUSDT"]
)
SYMBOLS = [
    s.strip().upper()
    for s in (
        os.getenv("SYMBOLS")
        or ",".join(DEFAULT_SYMBOLS)
    ).split(",")
    if s.strip()
]
DB2_SYMBOLS = {
    s.strip().upper()
    for s in (
        os.getenv("DB2_SYMBOLS") or "SOLUSDT,BNBUSDT"
    ).split(",")
    if s.strip()
}


def symbol_conn(symbol):
    if symbol in DB2_SYMBOLS and DB2_OK:
        try:
            return get_conn_db2()
        except Exception as e:
            log.warning("db2 conn %s: %s", symbol, e)
    return get_connection()


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("load %s: %s", path.name, e)
        return default if default is not None else {}


def fetch_events(symbol, limit=EVENT_LIMIT):
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, timestamp, event_type, "
                    "change_pct, magnitude "
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
        log.error("fetch_events: %s", e)
        return []


def fetch_existing_event_ids(symbol, events):
    if not events:
        return set()
    ids = [e["id"] for e in events]
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT event_id FROM causal_links "
                    "WHERE event_id = ANY(%s)",
                    (ids,),
                )
                return {r[0] for r in cur.fetchall()}
    except Exception as e:
        log.error("fetch_existing_event_ids: %s", e)
        return set()


def load_window_data(symbol, events):
    if not events:
        return {}
    earliest = min(e["timestamp"] for e in events)
    latest = max(e["timestamp"] for e in events)
    start = earliest - timedelta(hours=WINDOW_HOURS)

    out = {
        "start": start,
        "latest": latest,
        "candles": [],
        "funding": [],
        "oi": [],
        "ls": [],
    }

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, open, high, low, close, volume "
                    "FROM candles WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "AND market_type = 'futures' "
                    "AND timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp",
                    (symbol, start, latest),
                )
                for r in cur.fetchall():
                    out["candles"].append({
                        "timestamp": r[0],
                        "open": float(r[1]) if r[1] else 0,
                        "high": float(r[2]) if r[2] else 0,
                        "low": float(r[3]) if r[3] else 0,
                        "close": float(r[4]) if r[4] else 0,
                        "volume": float(r[5]) if r[5] else 0,
                    })

                cur.execute(
                    "SELECT timestamp, rate FROM funding_rates "
                    "WHERE symbol = %s "
                    "AND timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp",
                    (symbol, start, latest),
                )
                out["funding"] = [
                    (r[0], float(r[1]) if r[1] is not None else None)
                    for r in cur.fetchall()
                ]

                cur.execute(
                    "SELECT timestamp, oi FROM open_interest "
                    "WHERE symbol = %s "
                    "AND timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp",
                    (symbol, start, latest),
                )
                out["oi"] = [
                    (r[0], float(r[1]) if r[1] is not None else None)
                    for r in cur.fetchall()
                ]
    except Exception as e:
        log.error("load_window_data (core): %s", e)

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, ls_ratio FROM long_short_ratio "
                    "WHERE symbol = %s "
                    "AND timestamp >= %s AND timestamp <= %s "
                    "ORDER BY timestamp",
                    (symbol, start, latest),
                )
                out["ls"] = [
                    (r[0], float(r[1]) if r[1] is not None else None)
                    for r in cur.fetchall()
                ]
    except Exception:
        out["ls"] = []

    return out


def candles_before(candles, event_ts, hours):
    end = event_ts
    start = event_ts - timedelta(hours=hours)
    return [
        c for c in candles
        if start <= c["timestamp"] < end
    ]


def last_before(series, event_ts):
    result = None
    for ts, val in series:
        if ts < event_ts:
            result = (ts, val)
        else:
            break
    return result


def first_after(series, ts):
    for row in series:
        if row[0] >= ts:
            return row
    return None


def compute_change_pct(candles):
    if len(candles) < 2:
        return None
    first = candles[0]["open"]
    last = candles[-1]["close"]
    if first == 0:
        return None
    return round((last - first) / first * 100, 4)


def compute_volatility(candles):
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


def find_nearest_level(price, levels_data, symbol):
    try:
        sym_data = levels_data.get("symbols", {}).get(symbol, {})
        round_levels = sym_data.get("round_levels", [])
        if not round_levels or price <= 0:
            return None
        return min(
            round_levels, key=lambda x: abs(x["price"] - price)
        )
    except Exception:
        return None


def get_pattern_before(patterns_data, symbol):
    try:
        sym_data = patterns_data.get("symbols", {}).get(symbol, {})
        binary = sym_data.get("binary_string", "")
        if not binary:
            return None
        return binary[-4:] if len(binary) >= 4 else binary
    except Exception:
        return None


def build_entry(symbol, event, data, levels_data, patterns_data):
    event_ts = event["timestamp"]
    candles_all = data.get("candles", [])

    ohlcv_1h = candles_before(candles_all, event_ts, 1)
    ohlcv_4h = candles_before(candles_all, event_ts, 4)
    ohlcv_24h = candles_before(candles_all, event_ts, WINDOW_HOURS)

    f_1h = None
    if ohlcv_1h:
        f_1h = {
            "change_pct": compute_change_pct(ohlcv_1h),
            "volatility": compute_volatility(ohlcv_1h),
        }

    funding = None
    row = last_before(data.get("funding", []), event_ts)
    if row is not None:
        funding = row[1]

    oi_change = None
    oi_series = data.get("oi", [])
    oi_start = first_after(
        oi_series, event_ts - timedelta(hours=WINDOW_HOURS)
    )
    oi_end = last_before(oi_series, event_ts)
    if oi_start and oi_end and oi_start[1] and oi_start[1] > 0:
        oi_change = round(
            (oi_end[1] - oi_start[1]) / oi_start[1] * 100, 4
        )

    ls_ratio = None
    row = last_before(data.get("ls", []), event_ts)
    if row is not None:
        ls_ratio = row[1]

    entry_price = ohlcv_1h[-1]["close"] if ohlcv_1h else 0
    nearest = find_nearest_level(
        entry_price, levels_data, symbol
    )
    pattern = get_pattern_before(patterns_data, symbol)

    return {
        "event_id": event["id"],
        "symbol": symbol,
        "event_type": event["event_type"],
        "event_ts": event_ts,
        "features_1h": f_1h,
        "funding_rate": funding,
        "oi_change_pct": oi_change,
        "ls_ratio": ls_ratio,
        "nearest_level": nearest,
        "pattern_before": pattern,
        "volatility_24h": (
            compute_volatility(ohlcv_24h) if ohlcv_24h else None
        ),
        "change_24h": (
            compute_change_pct(ohlcv_24h) if ohlcv_24h else None
        ),
    }


def save_batch(symbol, entries):
    if not entries:
        return 0

    sql_head = (
        "INSERT INTO causal_links "
        "(event_id, hours_before, funding_rate, "
        "oi_change_pct, ls_ratio, taker_ratio, "
        "volume_ratio, volatility, change_pct, "
        "is_anomaly) VALUES "
    )
    sql_tail = " ON CONFLICT (event_id, hours_before) DO NOTHING"

    placeholders = ",".join(
        ["(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"] * len(entries)
    )
    sql = sql_head + placeholders + sql_tail

    params = []
    for e in entries:
        params.extend([
            e["event_id"], WINDOW_HOURS,
            e.get("funding_rate"),
            e.get("oi_change_pct"),
            e.get("ls_ratio"),
            None,
            None,
            e.get("volatility_24h"),
            e.get("change_24h"),
            False,
        ])

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.rowcount or 0
    except Exception as e:
        log.error("save_batch: %s", e)
        return 0


def analyze_symbol(symbol, levels_data, patterns_data):
    log.info("%s -- causal", symbol)
    events = fetch_events(symbol, limit=EVENT_LIMIT)
    if not events:
        log.warning("%s: no events", symbol)
        return None

    log.info("   events in DB: %d", len(events))

    existing = fetch_existing_event_ids(symbol, events)
    log.info("   already in causal_links: %d", len(existing))

    new_events = [e for e in events if e["id"] not in existing]
    log.info("   new to process: %d", len(new_events))

    if not new_events:
        return {
            "symbol": symbol,
            "total_events": len(events),
            "processed": 0,
            "saved": 0,
            "summary_by_type": {},
        }

    data = load_window_data(symbol, new_events)
    log.info(
        "   window: candles=%d funding=%d oi=%d ls=%d",
        len(data.get("candles", [])),
        len(data.get("funding", [])),
        len(data.get("oi", [])),
        len(data.get("ls", [])),
    )

    entries = []
    for ev in new_events:
        try:
            entry = build_entry(
                symbol, ev, data, levels_data, patterns_data
            )
            entries.append(entry)
        except Exception as e:
            log.warning("build_entry %s: %s", ev["id"], e)

    log.info("   entries built: %d", len(entries))

    saved = save_batch(symbol, entries)
    log.info("   added to causal_links: %d", saved)

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
        if e.get("change_24h") is not None:
            s["change_24h_avg"].append(e["change_24h"])

    def avg(arr):
        return round(sum(arr) / len(arr), 4) if arr else None

    summary_out = {}
    for t, s in summary.items():
        summary_out[t] = {
            "count": s["count"],
            "avg_funding": avg(s["funding_avg"]),
            "avg_oi_change": avg(s["oi_change_avg"]),
            "avg_ls_ratio": avg(s["ls_avg"]),
            "avg_change_24h": avg(s["change_24h_avg"]),
        }

    return {
        "symbol": symbol,
        "total_events": len(events),
        "processed": len(entries),
        "saved": saved,
        "summary_by_type": summary_out,
    }


def main():
    log.info("=" * 60)
    log.info("ARGUS-Trader CAUSAL v4")
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    levels_data = load_json(LEVELS_FILE, {})
    patterns_data = load_json(PATTERNS_FILE, {})
    log.info("   levels: %s",
             "ok" if levels_data else "missing")
    log.info("   patterns: %s",
             "ok" if patterns_data else "missing")
    log.info("")

    all_analysis = {}
    for symbol in SYMBOLS:
        result = analyze_symbol(
            symbol, levels_data, patterns_data
        )
        if result:
            all_analysis[symbol] = result
        log.info("")

    try:
        with open(ANALYSIS_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info("%s saved", ANALYSIS_FILE.name)
    except Exception as e:
        log.error("save analysis: %s", e)

    log.info("=" * 60)
    log.info("CAUSAL DONE")
    log.info("=" * 60)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()