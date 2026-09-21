# ============================================================
# ARGUS-Trader — CORRELATE
# ------------------------------------------------------------
# Ищет закономерности: какие условия ПРЕДШЕСТВУЮТ движению.
# Читает causal_links + events из БД.
# Находит простые и двойные комбинации признаков.
# Сохраняет в crypto/data/correlations.json.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

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

from config import SYMBOLS
from db import get_connection, close_connection

DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = DATA_DIR / "correlations.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.correlate")


# Минимум сэмплов для уверенности в правиле
MIN_SAMPLES = 3


def fetch_causal_data(symbol):
    """
    Собирает все пары (событие + lead-сигналы).
    JOIN causal_links с events.
    """
    try:
        with get_connection() as conn:
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


def compute_rule_stats(rows, condition_fn, label):
    """
    Применяет условие к строкам, считает статистику.
    condition_fn(row) → bool
    """
    matched = [r for r in rows if condition_fn(r)]
    if len(matched) < MIN_SAMPLES:
        return None

    # Смотрим на событие: rise или fall
    rises = sum(1 for r in matched if r["event_type"].startswith("rise"))
    falls = sum(1 for r in matched if r["event_type"].startswith("fall"))
    total = len(matched)

    if total == 0:
        return None

    # Итоговое направление: рост или падение чаще?
    if rises > falls:
        direction = "up"
        p_correct = round(rises / total, 4)
    elif falls > rises:
        direction = "down"
        p_correct = round(falls / total, 4)
    else:
        direction = "neutral"
        p_correct = 0.5

    return {
        "rule": label,
        "samples": total,
        "rises": rises,
        "falls": falls,
        "direction": direction,
        "confidence": p_correct,
    }


def find_rules(rows):
    """Ищет простые и двойные комбинации."""
    rules = []

    if not rows:
        return rules

    # --- Простые правила: funding ---
    r = compute_rule_stats(
        rows,
        lambda x: x["funding"] is not None and x["funding"] < -0.00005,
        "funding < -0.005%",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["funding"] is not None and x["funding"] > 0.00005,
        "funding > +0.005%",
    )
    if r: rules.append(r)

    # --- Простые правила: oi_change ---
    r = compute_rule_stats(
        rows,
        lambda x: x["oi_change"] is not None and x["oi_change"] < -2,
        "OI change < -2%",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["oi_change"] is not None and x["oi_change"] > 2,
        "OI change > +2%",
    )
    if r: rules.append(r)

    # --- Простые правила: ls_ratio ---
    r = compute_rule_stats(
        rows,
        lambda x: x["ls_ratio"] is not None and x["ls_ratio"] < 1.0,
        "LS ratio < 1.0 (толпа в шортах)",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: x["ls_ratio"] is not None and x["ls_ratio"] > 1.5,
        "LS ratio > 1.5 (толпа в лонгах)",
    )
    if r: rules.append(r)

    # --- Простые правила: volatility ---
    r = compute_rule_stats(
        rows,
        lambda x: x["volatility"] is not None and x["volatility"] > 1.0,
        "Volatility > 1.0",
    )
    if r: rules.append(r)

    # --- Двойные правила ---
    r = compute_rule_stats(
        rows,
        lambda x: (x["funding"] is not None and x["funding"] < -0.00005
                   and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0),
        "funding < -0.005% AND LS < 1.0",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (x["oi_change"] is not None and x["oi_change"] > 2
                   and x["ls_ratio"] is not None and x["ls_ratio"] > 1.5),
        "OI > +2% AND LS > 1.5",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (x["oi_change"] is not None and x["oi_change"] < -2
                   and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0),
        "OI < -2% AND LS < 1.0",
    )
    if r: rules.append(r)

    r = compute_rule_stats(
        rows,
        lambda x: (x["volatility"] is not None and x["volatility"] > 1.0
                   and x["oi_change"] is not None and abs(x["oi_change"]) > 3),
        "Volatility > 1.0 AND |OI change| > 3%",
    )
    if r: rules.append(r)

    # --- Тройные правила ---
    r = compute_rule_stats(
        rows,
        lambda x: (x["funding"] is not None and x["funding"] < -0.00005
                   and x["ls_ratio"] is not None and x["ls_ratio"] < 1.0
                   and x["oi_change"] is not None and x["oi_change"] < 0),
        "funding < -0.005% AND LS < 1.0 AND OI снижается",
    )
    if r: rules.append(r)

    # Сортируем по confidence, отсеиваем слабые
    rules = [r for r in rules if r["confidence"] >= 0.55]
    rules.sort(key=lambda x: (x["confidence"], x["samples"]), reverse=True)
    return rules


def analyze_symbol(symbol):
    log.info(f"📊 {symbol} — корреляции")
    rows = fetch_causal_data(symbol)
    log.info(f"   Событий с lead-сигналами: {len(rows)}")

    if not rows:
        return None

    rules = find_rules(rows)

    log.info(f"   Найдено правил: {len(rules)}")
    for r in rules[:5]:
        log.info(f"     • {r['rule']} → {r['direction'].upper()} "
                 f"({r['confidence'] * 100:.0f}%, N={r['samples']})")

    return {
        "symbol": symbol,
        "total_events": len(rows),
        "rules_found": len(rules),
        "rules": rules,
    }


def main():
    log.info("=" * 60)
    log.info("🧠 ARGUS-Trader CORRELATE")
    log.info("=" * 60)
    log.info(f"   Мин. сэмплов для правила: {MIN_SAMPLES}")
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
                "symbols": all_analysis,
            }, f, ensure_ascii=False, indent=2, default=str)
        log.info(f"💾 {OUTPUT_FILE.name} сохранён")
    except Exception as e:
        log.error(f"save: {e}")

    log.info("=" * 60)
    log.info("✅ CORRELATE DONE")
    log.info("=" * 60)

    close_connection()


if __name__ == "__main__":
    main()