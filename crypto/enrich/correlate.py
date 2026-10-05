# ============================================================
# ARGUS-Trader — CORRELATE v4
# ------------------------------------------------------------
# v4: DB routing via symbol_conn (DB1: BTC/ETH, DB2: SOL/BNB).
#     SYMBOLS from env, fallback to config.SYMBOLS.
#     Автопоиск db2.py (как в features.py v8).
#     Логика расчёта не менялась с v3.
# v3: base_rate + edge. MIN_SAMPLES=10, MIN_EDGE=0.15.
#     Rules without edge over base are dropped.
#     Sorted by edge, not by confidence.
# v2: fix compute_rule_stats — считает directional только.
# ============================================================

import os
import sys
import json
import logging
from datetime import datetime, timezone
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
OUTPUT_FILE = DATA_DIR / "correlations.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.correlate")

MIN_SAMPLES = 10
MIN_EDGE = 0.15

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


def fetch_causal_data(symbol):
    """Собирает пары (событие + lead-сигналы)."""
    try:
        with symbol_conn(symbol) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        e.event_type,
                        e.change_pct AS event_change,
                        e.magnitude,
                        c.funding_rate,
                        c.oi_change_pct,
                        c.ls_ratio,
                        c.volatility,
                        c.change_pct AS pre_change
                    FROM events e
                    JOIN causal_links c ON c.event_id = e.id
                    WHERE e.symbol = %s
                    ORDER BY e.timestamp DESC
                """, (symbol,))
                rows = cur.fetchall()
                return [
                    {
                        "event_type": r[0],
                        "event_change": float(r[1]) if r[1] is not None else 0,
                        "magnitude": float(r[2]) if r[2] is not None else 0,
                        "funding": float(r[3]) if r[3] is not None else None,
                        "oi_change": float(r[4]) if r[4] is not None else None,
                        "ls_ratio": float(r[5]) if r[5] is not None else None,
                        "volatility": float(r[6]) if r[6] is not None else None,
                        "pre_change": float(r[7]) if r[7] is not None else None,
                    }
                    for r in rows
                ]
    except Exception as e:
        log.error(f"fetch_causal_data: {e}")
        return []


def is_directional(event_type):
    return (
        event_type.startswith("rise")
        or event_type.startswith("fall")
    )


def compute_base_rate(rows):
    """Base rate of 'rise' among all directional events."""
    directional = [r for r in rows if is_directional(r["event_type"])]
    if not directional:
        return None
    rises = sum(
        1 for r in directional
        if r["event_type"].startswith("rise")
    )
    return rises / len(directional)


def compute_rule_stats(rows, condition_fn, label, base_rate):
    """Apply condition, compute stats over base_rate."""
    matched = [r for r in rows if condition_fn(r)]
    directional = [r for r in matched if is_directional(r["event_type"])]

    if len(directional) < MIN_SAMPLES:
        return None

    rises = sum(
        1 for r in directional
        if r["event_type"].startswith("rise")
    )
    falls = len(directional) - rises
    total = len(directional)

    p_rule = rises / total
    edge = abs(p_rule - base_rate)

    if edge < MIN_EDGE:
        return None

    direction = "up" if p_rule > base_rate else "down"

    if direction == "down":
        confidence = falls / total
    else:
        confidence = p_rule

    return {
        "rule": label,
        "samples": total,
        "rises": rises,
        "falls": falls,
        "direction": direction,
        "confidence": round(confidence, 4),
        "base_rate": round(base_rate, 4),
        "edge": round(edge, 4),
    }


def find_rules(rows, base_rate):
    """Ищет простые, двойные и тройные комбинации."""
    rules = []
    if not rows or base_rate is None:
        return rules

    # --- funding ---
    r = compute_rule_stats(
        rows,
        lambda x: x["funding"] is not None and x["funding"] < -0.00005,
        "funding < -0.005%",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["funding"] is not None and x["funding"] > 0.00005,
        "funding > +0.005%",
        base_rate,
    )
    if r:
        rules.append(r)

    # --- oi_change ---
    r = compute_rule_stats(
        rows,
        lambda x: x["oi_change"] is not None and x["oi_change"] < -2,
        "OI change < -2%",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["oi_change"] is not None and x["oi_change"] > 2,
        "OI change > +2%",
        base_rate,
    )
    if r:
        rules.append(r)

    # --- ls_ratio ---
    r = compute_rule_stats(
        rows,
        lambda x: x["ls_ratio"] is not None and x["ls_ratio"] < 1.0,
        "LS ratio < 1.0",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["ls_ratio"] is not None and x["ls_ratio"] > 1.5,
        "LS ratio > 1.5",
        base_rate,
    )
    if r:
        rules.append(r)

    # --- volatility ---
    r = compute_rule_stats(
        rows,
        lambda x: x["volatility"] is not None and x["volatility"] > 1.0,
        "Volatility > 1.0",
        base_rate,
    )
    if r:
        rules.append(r)

    # --- двойные ---
    r = compute_rule_stats(
        rows,
        lambda x: (
            x["funding"] is not None and x["funding"] < -0.00005
            and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0
        ),
        "funding < -0.005% AND LS < 1.0",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (
            x["oi_change"] is not None and x["oi_change"] > 2
            and x["ls_ratio"] is not None and x["ls_ratio"] > 1.5
        ),
        "OI > +2% AND LS > 1.5",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (
            x["oi_change"] is not None and x["oi_change"] < -2
            and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0
        ),
        "OI < -2% AND LS < 1.0",
        base_rate,
    )
    if r:
        rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (
            x["volatility"] is not None and x["volatility"] > 1.0
            and x["oi_change"] is not None
            and abs(x["oi_change"]) > 3
        ),
        "Volatility > 1.0 AND |OI change| > 3%",
        base_rate,
    )
    if r:
        rules.append(r)

    # --- тройные ---
    r = compute_rule_stats(
        rows,
        lambda x: (
            x["funding"] is not None and x["funding"] < -0.00005
            and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0
            and x["oi_change"] is not None and x["oi_change"] < 0
        ),
        "funding < -0.005% AND LS < 1.0 AND OI снижается",
        base_rate,
    )
    if r:
        rules.append(r)

    rules.sort(
        key=lambda x: (x["edge"], x["samples"]),
        reverse=True,
    )
    return rules


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — корреляции")
    rows = fetch_causal_data(symbol)
    log.info(f"   Событий с lead-сигналами: {len(rows)}")

    if not rows:
        return None

    base_rate = compute_base_rate(rows)
    if base_rate is None:
        log.warning("   нет directional событий")
        return None

    log.info(f"   base_rate (rise) = {base_rate:.3f}")

    rules = find_rules(rows, base_rate)

    log.info(f"   Найдено правил: {len(rules)}")
    for r in rules[:5]:
        log.info(
            f"     • {r['rule']} → {r['direction'].upper()} "
            f"({r['confidence']*100:.0f}%, N={r['samples']}, "
            f"edge={r['edge']:.2f})"
        )

    return {
        "symbol": symbol,
        "total_events": len(rows),
        "base_rate": round(base_rate, 4),
        "rules_found": len(rules),
        "rules": rules,
    }


def main():
    log.info("=" * 60)
    log.info("🧠 ARGUS-Trader CORRELATE v4")
    log.info(
        "SYMBOLS=%s DB2_SYMBOLS=%s (DB2_OK=%s)",
        SYMBOLS, sorted(DB2_SYMBOLS), DB2_OK,
    )
    log.info("=" * 60)
    log.info(f"   MIN_SAMPLES={MIN_SAMPLES}, MIN_EDGE={MIN_EDGE}")
    log.info("")

    all_analysis = {}

    for symbol in SYMBOLS:
        result = analyze_symbol(symbol)
        if result:
            all_analysis[symbol] = result
        log.info("")

    try:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "min_samples": MIN_SAMPLES,
                "min_edge": MIN_EDGE,
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"💾 {OUTPUT_FILE.name} сохранён")
    except Exception as e:
        log.error(f"save: {e}")

    log.info("=" * 60)
    log.info("✅ CORRELATE DONE")
    log.info("=" * 60)

    close_connection()
    if DB2_OK and close_conn_db2:
        try:
            close_conn_db2()
        except Exception:
            pass


if __name__ == "__main__":
    main()