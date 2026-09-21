# ============================================================
# ARGUS-Trader — PRIORITY
# ------------------------------------------------------------
# Таблица приоритетов: какая метрика с каких бирж берётся.
# Каждая метрика — независимая fallback-цепочка.
# ------------------------------------------------------------
# v1: начальная версия
# v2: fix импортов (sys.path для запуска из любой папки)
# ============================================================

import sys
import logging
from pathlib import Path

# --- Путь к crypto/ (для импорта config) ---
SCRIPT_DIR = Path(__file__).resolve().parent          # crypto/collect/
CRYPTO_ROOT = SCRIPT_DIR.parent                        # crypto/
sys.path.insert(0, str(CRYPTO_ROOT))

log = logging.getLogger("crypto.priority")


# ============================================================
# ПРИОРИТЕТЫ ПО МЕТРИКАМ
# ============================================================
# Формат: metric_name → [(exchange, method_name), ...]
# Порядок важен: первый — основной источник.

PRIORITY = {
    "ohlcv": [
        ("okx",    "fetch_ohlcv"),
        ("bitget", "fetch_ohlcv"),
        ("gate",   "fetch_ohlcv"),
        ("kucoin", "fetch_ohlcv"),
    ],
    "funding": [
        ("okx",    "fetch_funding"),
        ("bitget", "fetch_funding"),
        ("gate",   "fetch_funding"),
        ("kucoin", "fetch_funding"),
    ],
    "oi": [
        ("okx",    "fetch_oi"),
        ("bitget", "fetch_oi"),
        ("gate",   "fetch_oi"),
    ],
    "ls_ratio": [
        ("okx",    "fetch_ls"),
        ("bitget", "fetch_ls"),
        ("gate",   "fetch_ls"),
    ],
    "taker": [
        ("okx",    "fetch_taker"),
        ("gate",   "fetch_taker"),
    ],
}


# ============================================================
# ХЕЛПЕРЫ
# ============================================================
def get_priority(metric: str) -> list:
    """Возвращает приоритетный список бирж для метрики."""
    return PRIORITY.get(metric, [])


def supported_metrics() -> list:
    """Возвращает список поддерживаемых метрик."""
    return list(PRIORITY.keys())


def get_metrics_table() -> str:
    """Возвращает текстовую таблицу приоритетов для логов."""
    lines = ["📋 PRIORITY TABLE", "=" * 60]
    for metric, sources in PRIORITY.items():
        chain = " → ".join(name for name, _ in sources)
        lines.append(f"  {metric:12} : {chain}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================
# ТЕСТ
# ============================================================
if __name__ == "__main__":
    print(get_metrics_table())