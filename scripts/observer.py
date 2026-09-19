# ============================================================
# ARGUS — НАБЛЮДАТЕЛЬ (v4)
# v4: pathlib, чтение argus.jsonl, avg_score вместо avg_distance, sys.exit
# ============================================================

import os
import sys
import json
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

# ИСПРАВЛЕНО: читаем новый формат лога .jsonl
LOG_FILE = REPO_ROOT / "logs" / "argus.jsonl"
OUTPUT_FILE = REPO_ROOT / "data" / "observation.json"

# --- Стоп-слова (RU + EN) ---
STOP_WORDS = {
    # RU
    "что", "такое", "как", "где", "когда", "почему", "зачем",
    "это", "есть", "быть", "может", "можно", "нужно", "хочу",
    "какой", "какая", "какие", "чем", "для", "про", "или", "если",
    "нет", "да", "не", "ни", "же", "бы", "ли", "вот", "всё", "весь",
    # EN
    "what", "is", "are", "the", "how", "why", "when", "where",
    "which", "about", "with", "from", "this", "that", "these",
    "those", "for", "and", "or", "but", "not", "can", "will",
    "would", "should", "could", "does", "did", "was", "were",
    "have", "has", "had", "into", "over", "under", "than", "it", "to",
}


def save_empty(note: str):
    obs = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_queries": 0,
        "failed_queries": 0,
        "failed_percent": 0.0,
        "errors": 0,
        "avg_response_ms": 0,
        "avg_score": 0.0,  # ИСПРАВЛЕНО: avg_score вместо avg_distance
        "translated_chunks_total": 0,
        "top_failed_words": [],
        "recent_failed_queries": [],
        "note": note,
    }
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(obs, f, ensure_ascii=False, indent=2)
    print(f"✅ Создан пустой {OUTPUT_FILE} ({note})")


# ---------- Загрузка логов ----------
if not LOG_FILE.exists():
    print(f"❌ Логов нет ({LOG_FILE}).")
    save_empty("log file not found")
    sys.exit(0)

all_logs = []
try:
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                all_logs.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # Пропускаем битые строки, не ломаем весь парсинг
except Exception as e:
    print(f"❌ Ошибка чтения логов: {e}")
    sys.exit(1)

# --- Фильтр: только записи от /ask ---
logs = [l for l in all_logs if l.get("action") == "ask"]

print(f"📂 Всего записей в логе: {len(all_logs)}")
print(f"🔍 Из них /ask: {len(logs)}")

if not logs:
    print("⚠️ Запросов /ask пока нет — создаю пустой observation.")
    save_empty("no ask records yet")
    sys.exit(0)

# ---------- Метрики ----------
total = len(logs)

# Неудачные: те, где found_chunks == 0 или отсутствует
failed = [l for l in logs if not l.get("found_chunks")]
errors = [l for l in logs if l.get("error")]

response_times = [l.get("response_time_ms", 0) for l in logs if l.get("response_time_ms")]
avg_time = sum(response_times) / len(response_times) if response_times else 0

# ИСПРАВЛЕНО: читаем avg_score (для FAISS IP чем выше, тем лучше)
scores = [l.get("avg_score") for l in logs if l.get("avg_score") is not None]
avg_score = sum(scores) / len(scores) if scores else 0.0

# ---------- Проблемные запросы ----------
failed_queries = [l.get("query", "") for l in failed if l.get("query")]

failed_words = Counter()
for query in failed_queries:
    for word in query.lower().split():
        # Очистка от пунктуации
        word = word.strip(".,!?;:()[]{}«»\"'`").strip()
        if len(word) > 3 and word not in STOP_WORDS:  # Уменьшил до 3, чтобы ловить короткие термины (например, "rsi")
            failed_words[word] += 1

translated_count = sum(
    l.get("extra", {}).get("translated", 0)
    for l in logs if l.get("extra")
)

# ---------- Отчёт ----------
observation = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "total_queries": total,
    "failed_queries": len(failed),
    "failed_percent": round(len(failed) / total * 100, 1) if total else 0.0,
    "errors": len(errors),
    "avg_response_ms": int(avg_time),
    "avg_score": round(avg_score, 3),  # ИСПРАВЛЕНО
    "translated_chunks_total": translated_count,
    "top_failed_words": failed_words.most_common(10),
    "recent_failed_queries": failed_queries[-10:],
}

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(observation, f, ensure_ascii=False, indent=2)

# ---------- Вывод ----------
print("📊 НАБЛЮДЕНИЕ ARGUS")
print("=" * 50)
print(f"Всего запросов: {total}")
print(f"Без ответа: {len(failed)} ({observation['failed_percent']}%)")
print(f"Ошибок: {len(errors)}")
print(f"Среднее время: {int(avg_time)} мс")
print(f"Средний скор совпадения: {observation['avg_score']:.3f} (чем выше, тем лучше)")
print(f"Переведено фрагментов: {translated_count}")

if failed_words:
    print("\n🔴 Топ проблемных слов (пробелы в знаниях):")
    for word, count in failed_words.most_common(5):
        print(f"   '{word}': {count} раз")

print("=" * 50)
print(f"✅ Сохранено в {OUTPUT_FILE}")
