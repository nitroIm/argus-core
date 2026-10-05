# ============================================================
# ARGUS-Trader — PATTERNS v3
# ------------------------------------------------------------
# v3: DB routing via symbol_conn (DB1: BTC/ETH, DB2: SOL/BNB).
#     SYMBOLS from env, fallback to config.SYMBOLS.
#     Автопоиск db2.py (как в features.py v8).
#     Логика расчёта не менялась с v2.2.
# v2.2: batch INSERT in save_patterns. No double fetch.
# v2.1: fix fetch_features — merge features_hourly + candles.
# v2: + regime detection (trend_up / trend_down / flat /
#     chop / volatile).
# ============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone
from collections import defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

# Auto-locate db2.py (same trick as features.py v8)
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
ANALYSIS_FILE = DATA_DIR / "patterns_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.patterns")

REGIME_WINDOW = 48
VOLATILE_RATIO = 1.6

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


def fetch_features(symbol, limit=500):
    """Merge features_hourly + candles by timestamp."""
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, change_pct, range_pct, "
                    "body_pct, upper_wick_pct, lower_wick_pct, "
                    "volume_ratio_24h "
                    "FROM features_hourly WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                features_rows = list(reversed(cur.fetchall()))

                cur.execute(
                    "SELECT timestamp, close, high, low "
                    "FROM candles WHERE symbol = %s "
                    "AND timeframe = '1h' "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                candles_rows = list(reversed(cur.fetchall()))

        cmap = {}
        for r in candles_rows:
            cmap[r[0]] = {
                "close": float(r[1]) if r[1] is not None else 0,
                "high": float(r[2]) if r[2] is not None else 0,
                "low": float(r[3]) if r[3] is not None else 0,
            }

        result = []
        for r in features_rows:
            ts = r[0]
            c = cmap.get(ts, {})
            result.append({
                "timestamp": ts,
                "change_pct": float(r[1]) if r[1] is not None else 0,
                "range_pct": float(r[2]) if r[2] is not None else 0,
                "body_pct": float(r[3]) if r[3] is not None else 0,
                "upper_wick_pct": float(r[4]) if r[4] is not None else 0,
                "lower_wick_pct": float(r[5]) if r[5] is not None else 0,
                "volume_ratio_24h": float(r[6]) if r[6] is not None else 0,
                "close": c.get("close", 0),
                "high": c.get("high", 0),
                "low": c.get("low", 0),
            })
        return result
    except Exception as e:
        log.error(f"fetch_features: {e}")
        return []


def build_binary_string(features, bit_field="change_pct"):
    result = []
    for f in features:
        value = f.get(bit_field, 0)
        bit = "1" if value > 0 else "0"
        result.append(bit)
    return "".join(result)


def count_ngrams(binary_str, n=4):
    ngram_stats = defaultdict(
        lambda: {"total": 0, "next_1": 0, "next_0": 0}
    )
    for i in range(len(binary_str) - n):
        ngram = binary_str[i:i + n]
        next_bit = binary_str[i + n]
        ngram_stats[ngram]["total"] += 1
        if next_bit == "1":
            ngram_stats[ngram]["next_1"] += 1
        else:
            ngram_stats[ngram]["next_0"] += 1

    result = {}
    for ngram, stats in ngram_stats.items():
        total = stats["total"]
        if total >= 5:
            result[ngram] = {
                "count": total,
                "next_1": stats["next_1"],
                "next_0": stats["next_0"],
                "p_up": round(stats["next_1"] / total, 4),
                "p_down": round(stats["next_0"] / total, 4),
            }
    return result


def build_markov_matrix(binary_str):
    transitions = {"00": 0, "01": 0, "10": 0, "11": 0}
    for i in range(len(binary_str) - 1):
        pair = binary_str[i:i + 2]
        if pair in transitions:
            transitions[pair] += 1

    total_from_0 = transitions["00"] + transitions["01"]
    total_from_1 = transitions["10"] + transitions["11"]

    return {
        "total_transitions": sum(transitions.values()),
        "p_1_given_1": round(
            transitions["11"] / total_from_1, 4
        ) if total_from_1 else 0,
        "p_0_given_1": round(
            transitions["10"] / total_from_1, 4
        ) if total_from_1 else 0,
        "p_1_given_0": round(
            transitions["01"] / total_from_0, 4
        ) if total_from_0 else 0,
        "p_0_given_0": round(
            transitions["00"] / total_from_0, 4
        ) if total_from_0 else 0,
    }


def longest_streak(binary_str, bit="1"):
    max_streak = 0
    current = 0
    for c in binary_str:
        if c == bit:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def compute_atr_simple(candles, period):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        if h <= 0 or l <= 0 or pc <= 0:
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def detect_regime(features):
    if len(features) < REGIME_WINDOW:
        return {
            "label": "unknown",
            "reason": "not enough data",
            "trade_allowed": True,
            "preferred_direction": "both",
        }

    window = features[-REGIME_WINDOW:]
    binary = "".join(
        "1" if f["change_pct"] > 0 else "0"
        for f in window
    )

    up_ratio = binary.count("1") / len(binary)
    max_up = longest_streak(binary, "1")
    max_down = longest_streak(binary, "0")

    switches = 0
    for i in range(1, len(binary)):
        if binary[i] != binary[i - 1]:
            switches += 1
    switch_rate = switches / max(1, len(binary) - 1)

    atr_short = compute_atr_simple(features, 24)
    atr_long = compute_atr_simple(features, 168)
    vol_ratio = None
    if atr_short and atr_long and atr_long > 0:
        vol_ratio = atr_short / atr_long

    base = {
        "up_ratio": round(up_ratio, 3),
        "max_streak_up": max_up,
        "max_streak_down": max_down,
        "switch_rate": round(switch_rate, 3),
        "vol_ratio": round(vol_ratio, 3) if vol_ratio else None,
    }

    if vol_ratio is not None and vol_ratio > VOLATILE_RATIO:
        return {
            "label": "volatile",
            "reason": f"ATR short/long={vol_ratio:.2f}",
            "trade_allowed": False,
            "preferred_direction": "none",
            **base,
        }

    if up_ratio >= 0.6 and max_up >= 4 and switch_rate < 0.4:
        return {
            "label": "trend_up",
            "reason": f"up_ratio={up_ratio:.2f}, streak={max_up}",
            "trade_allowed": True,
            "preferred_direction": "LONG",
            **base,
        }

    if up_ratio <= 0.4 and max_down >= 4 and switch_rate < 0.4:
        return {
            "label": "trend_down",
            "reason": f"up_ratio={up_ratio:.2f}, streak={max_down}",
            "trade_allowed": True,
            "preferred_direction": "SHORT",
            **base,
        }

    if switch_rate > 0.5:
        return {
            "label": "chop",
            "reason": f"switch_rate={switch_rate:.3f}",
            "trade_allowed": False,
            "preferred_direction": "none",
            **base,
        }

    return {
        "label": "flat",
        "reason": f"up_ratio={up_ratio:.2f}, streak={max(max_up, max_down)}",
        "trade_allowed": False,
        "preferred_direction": "none",
        **base,
    }


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — анализ паттернов")
    features = fetch_features(symbol, limit=500)
    if not features:
        log.warning(f"{symbol}: features нет")
        return None

    log.info(f"   Свечей: {len(features)}")

    full_str = build_binary_string(features, "change_pct")
    ngrams_4 = count_ngrams(full_str, 4)
    markov = build_markov_matrix(full_str)

    max_up = longest_streak(full_str, "1")
    max_down = longest_streak(full_str, "0")

    top_ngrams = sorted(
        [(k, v) for k, v in ngrams_4.items() if v["count"] >= 10],
        key=lambda x: abs(x[1]["p_up"] - 0.5),
        reverse=True,
    )[:10]

    regime = detect_regime(features)

    return {
        "symbol": symbol,
        "total_candles": len(features),
        "up_count": full_str.count("1"),
        "down_count": full_str.count("0"),
        "up_ratio": round(full_str.count("1") / len(full_str), 4),
        "max_streak_up": max_up,
        "max_streak_down": max_down,
        "markov": markov,
        "ngrams_top": [{"ngram": k, **v} for k, v in top_ngrams],
        "binary_string": full_str,
        "last_24h": full_str[-24:] if len(full_str) >= 24 else full_str,
        "last_168h": full_str[-168:] if len(full_str) >= 168 else full_str,
        "regime": regime,
        "_features": features,  # cached, stripped before JSON dump
    }


def save_patterns(symbol, analysis):
    """Single batch INSERT for all rows of one symbol."""
    if not analysis:
        return 0

    binary = analysis["binary_string"]
    features = analysis.get("_features") or []

    if not features:
        return 0

    rows = []
    for i, f in enumerate(features):
        ts = f["timestamp"]
        bit = 1 if binary[i] == "1" else 0

        p_4h = binary[max(0, i - 3):i + 1] \
            if i >= 3 else binary[:i + 1]
        p_24h = binary[max(0, i - 23):i + 1] \
            if i >= 23 else binary[:i + 1]
        p_7d = binary[max(0, i - 167):i + 1] \
            if i >= 167 else binary[:i + 1]

        rows.append((symbol, ts, bit, p_4h, p_24h, p_7d))

    if not rows:
        return 0

    sql_head = (
        "INSERT INTO price_patterns "
        "(symbol, timestamp, pattern_1h, pattern_4h, "
        "pattern_24h, pattern_7d) VALUES "
    )
    placeholders = ",".join(["(%s,%s,%s,%s,%s,%s)"] * len(rows))
    sql = sql_head + placeholders + " ON CONFLICT (symbol, timestamp) DO NOTHING"

    params = []
    for r in rows:
        params.extend(r)

    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.rowcount or 0
    except Exception as e:
        log.error(f"save_patterns batch failed: {e}, fallback single")
        return _save_patterns_single(symbol, rows)


def _save_patterns_single(symbol, rows):
    added = 0
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                for i, r in enumerate(rows):
                    sp = "sp_p_" + str(i)
                    try:
                        cur.execute("SAVEPOINT " + sp)
                        cur.execute(
                            "INSERT INTO price_patterns "
                            "(symbol, timestamp, pattern_1h, "
                            "pattern_4h, pattern_24h, pattern_7d) "
                            "VALUES (%s,%s,%s,%s,%s,%s) "
                            "ON CONFLICT (symbol, timestamp) DO NOTHING",
                            r,
                        )
                        n = cur.rowcount or 0
                        cur.execute("RELEASE SAVEPOINT " + sp)
                        added += n
                    except Exception as ex:
                        try:
                            cur.execute(
                                "ROLLBACK TO SAVEPOINT " + sp
                            )
                        except Exception:
                            pass
                        log.warning(f"row {i} skip: {ex}")
    except Exception as e:
        log.error(f"_save_patterns_single: {e}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧩 ARGUS-Trader PATTERNS v3")
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)

    all_analysis = {}
    total_saved = 0

    for symbol in SYMBOLS:
        analysis = analyze_symbol(symbol)
        if not analysis:
            continue

        saved = save_patterns(symbol, analysis)
        total_saved += saved

        log.info(f"   Up/Down: {analysis['up_count']}/"
                 f"{analysis['down_count']} "
                 f"({analysis['up_ratio'] * 100:.1f}% up)")
        log.info(f"   Серия вверх: {analysis['max_streak_up']} | "
                 f"Серия вниз: {analysis['max_streak_down']}")

        rg = analysis["regime"]
        log.info(f"   🎯 REGIME: {rg['label']} — {rg['reason']}")
        log.info(f"      trade_allowed={rg['trade_allowed']}, "
                 f"direction={rg['preferred_direction']}, "
                 f"up_ratio={rg.get('up_ratio')}, "
                 f"switch_rate={rg.get('switch_rate')}, "
                 f"vol_ratio={rg.get('vol_ratio')}")

        # Remove cached features before storing in JSON
        analysis.pop("_features", None)
        all_analysis[symbol] = analysis

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
    log.info(f"✅ PATTERNS DONE. Сохранено: {total_saved}")
    log.info("=" * 60)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()