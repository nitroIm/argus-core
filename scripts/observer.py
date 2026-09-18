# ============================================================
# ARGUS — НАБЛЮДАТЕЛЬ
# v2: правильные пути + расширенные стоп-слова
# ============================================================

import os
import json
from datetime import datetime
from collections import Counter

# --- Пути от корня репо ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

LOG_FILE = os.path.join(REPO_ROOT, "logs", "argus.log")
OUTPUT_FILE = os.path.join(REPO_ROOT, "data", "observation.json")

# --- Стоп-слова (RU + EN) ---
STOP_WORDS = {
    # RU
    "что", "такое", "как", "где", "когда", "почему", "зачем",
    "это", "есть", "быть", "может", "можно", "нужно", "хочу",
    "какой", "какая", "какие", "чем", "для", "про", "или", "если",
    # EN
    "what", "is", "are", "the", "how", "why", "when", "where",
    "which", "about", "with", "from", "this", "that", "these",
    "those", "for", "and", "or", "but", "not", "can", "will",
    "would", "should", "could", "does", "did", "was", "were",
    "have", "has", "had", "into", "over", "under", "than",
}


# ---------- Загрузка логов ----------
if not os.path.exists(LOG_FILE):
    print(f"❌ Логов нет ({LOG_FILE}). Пока нечего наблюдать.")
    # Не выходим — создаём пустой observation, чтобы цепочка не рвалась
    obs = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_queries": 0,
        "failed_queries": 0,
        "failed_percent": 0,
        "errors": 0,
        "avg_response_ms": 0,
        "avg_distance": 0,
        "translated_chunks_total": 0,
        "top_failed_words": [],
        "recent_failed_queries": [],
        "note": "log file not found",
    }
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(obs, f, ensure_ascii=False, indent=2)
    print(f"✅ Создан пустой {OUTPUT_FILE}")
    exit(0)

with open(LOG_FILE, "r", encoding="utf-8") as f:
    lines = f.readlines()

logs = []
for line in lines:
    line = line.strip()
    if not line:
        continue
    try:
        logs.append(json.loads(line))
    except Exception:
        continue

if not logs:
    print("❌ Логи пусты.")
    exit(0)

# ---------- Метрики ----------
total = len(logs)
failed = [l for l in logs if not l.get("found_chunks")]
errors = [l for l in logs if l.get("error")]

response_times = [l.get("response_time_ms", 0) for l in logs if l.get("response_time_ms")]
avg_time = sum(response_times) / len(response_times) if response_times else 0

distances = [l.get("avg_distance") for l in logs if l.get("avg_distance") is not None]
avg_distance = sum(distances) / len(distances) if distances else 0

# ---------- Проблемные запросы ----------
failed_queries = [l.get("query", "") for l in failed if l.get("query")]

failed_words = Counter()
for query in failed_queries:
    for word in query.lower().split():
        word = word.strip(".,!?;:()[]{}«»\"'").strip()
        if len(word) > 4 and word not in STOP_WORDS:
            failed_words[word] += 1

translated_count = sum(
    l.get("extra", {}).get("translated", 0)
    for l in logs if l.get("extra")
)

# ---------- Отчёт ----------
observation = {
    "generated_at": datetime.utcnow().isoformat(),
    "total_queries": total,
    "failed_queries": len(failed),
    "failed_percent": round(len(failed) / total * 100, 1) if total else 0,
    "errors": len(errors),
    "avg_response_ms": int(avg_time),
    "avg_distance": round(avg_distance, 3),
    "translated_chunks_total": translated_count,
    "top_failed_words": failed_words.most_common(10),
    "recent_failed_queries": failed_queries[-10:],
}

os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(observation, f, ensure_ascii=False, indent=2)

# ---------- Вывод ----------
print("📊 НАБЛЮДЕНИЕ ARGUS")
print("=" * 50)
print(f"Всего запросов: {total}")
print(f"Без ответа: {len(failed)} ({observation['failed_percent']}%)")
print(f"Ошибок: {len(errors)}")
print(f"Среднее время: {int(avg_time)} мс")
print(f"Средний score: {observation['avg_distance']}")
print(f"Переведено фрагментов: {translated_count}")

if failed_words:
    print("\n🔴 Топ проблемных слов:")
    for word, count in failed_words.most_common(5):
        print(f"   {word}: {count}")

print("=" * 50)
print(f"✅ Сохранено в {OUTPUT_FILE}")