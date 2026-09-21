# ============================================================
# ARGUS-Trader — PATTERNS
# ------------------------------------------------------------
# Из features_hourly собирает:
#   - бинарные строки (0/1) за окна 24ч, 7д
#   - n-граммы (4-символьные)
#   - матрицу переходов Маркова
# Сохраняет в price_patterns + analysis файл.
# ------------------------------------------------------------
# v1: начальная версия
# ============================================================

import sys
import json
import logging
from datetime import datetime, timezone
from collections import Counter, defaultdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
sys.path.insert(0, str(CRYPTO_ROOT))

from config import SYMBOLS
from db import get_connection, close_connection

DATA_DIR.mkdir(parents=True, exist_ok=True)
ANALYSIS_FILE = DATA_DIR / "patterns_analysis.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.patterns")


def fetch_features(symbol, limit=500):
    """Возвращает features в порядке от старых к новым."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT timestamp, change_pct, range_pct, body_pct, "
                    "upper_wick_pct, lower_wick_pct, volume_ratio_24h "
                    "FROM features_hourly WHERE symbol = %s "
                    "ORDER BY timestamp DESC LIMIT %s",
                    (symbol, limit),
                )
                rows = cur.fetchall()
                rows = list(reversed(rows))
                return [
                    {
                        "timestamp": r[0],
                        "change_pct": float(r[1]) if r[1] is not None else 0,
                        "range_pct": float(r[2]) if r[2] is not None else 0,
                        "body_pct": float(r[3]) if r[3] is not None else 0,
                        "upper_wick_pct": float(r[4]) if r[4] is not None else 0,
                        "lower_wick_pct": float(r[5]) if r[5] is not None else 0,
                        "volume_ratio_24h": float(r[6]) if r[6] is not None else 0,
                    }
                    for r in rows
                ]
    except Exception as e:
        log.error(f"fetch_features: {e}")
        return []


def build_binary_string(features, bit_field="change_pct"):
    """Собирает строку '0' и '1' по свечам."""
    result = []
    for f in features:
        value = f.get(bit_field, 0)
        bit = "1" if value > 0 else "0"
        result.append(bit)
    return "".join(result)


def count_ngrams(binary_str, n=4):
    """Считает частоту всех n-грамм и что идёт СЛЕДУЮЩИМ."""
    ngram_stats = defaultdict(lambda: {"total": 0, "next_1": 0, "next_0": 0})

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
    """P(1|1), P(0|1), P(1|0), P(0|0)."""
    transitions = {"00": 0, "01": 0, "10": 0, "11": 0}
    for i in range(len(binary_str) - 1):
        pair = binary_str[i:i + 2]
        if pair in transitions:
            transitions[pair] += 1

    total_from_0 = transitions["00"] + transitions["01"]
    total_from_1 = transitions["10"] + transitions["11"]

    result = {
        "total_transitions": sum(transitions.values()),
        "p_1_given_1": round(transitions["11"] / total_from_1, 4) if total_from_1 else 0,
        "p_0_given_1": round(transitions["10"] / total_from_1, 4) if total_from_1 else 0,
        "p_1_given_0": round(transitions["01"] / total_from_0, 4) if total_from_0 else 0,
        "p_0_given_0": round(transitions["00"] / total_from_0, 4) if total_from_0 else 0,
    }
    return result


def longest_streak(binary_str, bit="1"):
    """Самая длинная цепочка подряд."""
    max_streak = 0
    current = 0
    for c in binary_str:
        if c == bit:
            current += 1
            max_streak = max(max_streak, current)
        else:
            current = 0
    return max_streak


def analyze_symbol(symbol):
    """Считает все паттерны для одного символа."""
    log.info(f"📊 {symbol} — анализ паттернов")
    features = fetch_features(symbol, limit=500)
    if not features:
        log.warning(f"{symbol}: features нет")
        return None

    log.info(f"   Свечей: {len(features)}")

    full_str = build_binary_string(features, "change_pct")

    # N-граммы разной длины
    ngrams_4 = count_ngrams(full_str, 4)

    # Матрица переходов
    markov = build_markov_matrix(full_str)

    # Серии
    max_up = longest_streak(full_str, "1")
    max_down = longest_streak(full_str, "0")

    # Топ паттернов по силе сигнала
    top_ngrams = sorted(
        [(k, v) for k, v in ngrams_4.items() if v["count"] >= 10],
        key=lambda x: abs(x[1]["p_up"] - 0.5),
        reverse=True,
    )[:10]

    result = {
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
    }
    return result


def save_patterns(symbol, analysis):
    """Сохраняет в price_patterns для каждого часа."""
    if not analysis:
        return 0

    binary = analysis["binary_string"]
    features = fetch_features(symbol, limit=500)

    added = 0
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                for i, f in enumerate(features):
                    ts = f["timestamp"]
                    bit = 1 if binary[i] == "1" else 0

                    # Строки контекста
                    p_4h = binary[max(0, i - 3):i + 1] if i >= 3 else binary[:i + 1]
                    p_24h = binary[max(0, i - 23):i + 1] if i >= 23 else binary[:i + 1]
                    p_7d = binary[max(0, i - 167):i + 1] if i >= 167 else binary[:i + 1]

                    try:
                        cur.execute(
                            "INSERT INTO price_patterns "
                            "(symbol, timestamp, pattern_1h, pattern_4h, pattern_24h, pattern_7d) "
                            "VALUES (%s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (symbol, timestamp) DO NOTHING",
                            (symbol, ts, bit, p_4h, p_24h, p_7d),
                        )
                        if cur.rowcount and cur.rowcount > 0:
                            added += cur.rowcount
                    except Exception as e:
                        log.warning(f"INSERT pattern skip: {e}")
    except Exception as e:
        log.error(f"save_patterns: {e}")
    return added


def main():
    log.info("=" * 60)
    log.info("🧩 ARGUS-Trader PATTERNS")
    log.info("=" * 60)

    all_analysis = {}
    total_saved = 0

    for symbol in SYMBOLS:
        analysis = analyze_symbol(symbol)
        if not analysis:
            continue

        all_analysis[symbol] = analysis
        saved = save_patterns(symbol, analysis)
        total_saved += saved

        # Лог
        log.info(f"   Up/Down: {analysis['up_count']}/{analysis['down_count']} "
                 f"({analysis['up_ratio'] * 100:.1f}% up)")
        log.info(f"   Серия вверх: {analysis['max_streak_up']} | "
                 f"Серия вниз: {analysis['max_streak_down']}")
        mk = analysis["markov"]
        log.info(f"   P(1|1)={mk['p_1_given_1']} | P(0|1)={mk['p_0_given_1']}")
        log.info(f"   P(1|0)={mk['p_1_given_0']} | P(0|0)={mk['p_0_given_0']}")

        if analysis["ngrams_top"]:
            log.info("   Топ паттернов:")
            for item in analysis["ngrams_top"][:5]:
                log.info(f"     {item['ngram']} → P(up)={item['p_up']} "
                         f"(N={item['count']})")

    # Сохраняем анализ в JSON
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


if __name__ == "__main__":
    main()