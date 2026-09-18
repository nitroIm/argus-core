# ============================================================
# ARGUS — НАБЛЮДАТЕЛЬ
# Собирает статистику из логов, находит слабые места
# ============================================================

import os
import json
from datetime import datetime, timedelta
from collections import Counter

LOG_FILE = "logs/argus.log"
OUTPUT_FILE = "data/observation.json"


# ---------- Загрузка логов ----------
if not os.path.exists(LOG_FILE):
    print("❌ Логов нет. Пока нечего наблюдать.")
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

# Средние значения
response_times = [l.get("response_time_ms", 0) for l in logs if l.get("response_time_ms")]
avg_time = sum(response_times) / len(response_times) if response_times else 0

distances = [l.get("avg_distance") for l in logs if l.get("avg_distance") is not None]
avg_distance = sum(distances) / len(distances) if distances else 0

# ---------- Проблемные запросы ----------
# Запросы без результатов
failed_queries = [l["query"] for l in failed if l.get("query")]

# Ключевые слова из неудачных запросов (длиннее 4 символов)
STOP_WORDS = {"что", "такое", "как", "где", "когда", "почему", "зачем",
              "это", "есть", "быть", "может", "можно", "нужно", "хочу"}

failed_words = Counter()
for query in failed_queries:
    for word in query.lower().split():
        word = word.strip(".,!?;:")
        if len(word) > 4 and word not in STOP_WORDS:
            failed_words[word] += 1

# ---------- Топ переведённых ----------
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

# ---------- Сохранение ----------
os.makedirs("data", exist_ok=True)
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