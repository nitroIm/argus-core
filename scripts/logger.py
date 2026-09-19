# ============================================================
# ARGUS — ЛОГИРОВАНИЕ (v2)
# v2: pathlib, timezone-aware datetime, avg_score, защита от сбоев записи
# ============================================================

import json
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
LOG_DIR = REPO_ROOT / "logs"
LOG_FILE = LOG_DIR / "argus.jsonl"  # .jsonl явно указывает на формат JSON Lines

# Создаём папку логов, если её нет
LOG_DIR.mkdir(parents=True, exist_ok=True)


def log_action(action, query=None, found_chunks=0, avg_score=None, avg_distance=None,
               response_time_ms=0, error=None, extra=None):
    """
    Записывает действие в лог в формате JSONL.
    
    :param action: тип действия (например, "ask", "train", "download")
    :param query: поисковый запрос (если применимо)
    :param found_chunks: количество найденных чанков
    :param avg_score: средний скор совпадения (для FAISS IP: чем выше, тем лучше)
    :param avg_distance: устаревший параметр, поддерживается для совместимости
    :param response_time_ms: время выполнения в миллисекундах
    :param error: текст ошибки, если она произошла
    :param extra: словарь с дополнительными метаданными
    """
    # Поддержка обратной совместимости: если передан avg_distance, используем его
    score_to_log = avg_score if avg_score is not None else avg_distance

    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "query": query,
        "found_chunks": found_chunks,
        "avg_score": score_to_log,
        "response_time_ms": response_time_ms,
        "error": error
    }

    if extra:
        entry["extra"] = extra

    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        # Не ломаем основной поток, если лог не записался (например, нет места на диске)
        print(f"⚠️ Ошибка записи в лог {LOG_FILE}: {e}")
