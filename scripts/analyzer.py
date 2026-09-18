# ============================================================
# ARGUS — АНАЛИЗАТОР
# Читает наблюдения, находит закономерности
# ============================================================

import os
import json
from datetime import datetime

OBSERVATION_FILE = "data/observation.json"
OUTPUT_FILE = "data/analysis.json"

# ---------- Проверки ----------
if not os.path.exists(OBSERVATION_FILE):
    print("❌ Нет observation.json. Сначала запусти observer.")
    exit(0)

with open(OBSERVATION_FILE, "r", encoding="utf-8") as f:
    obs = json.load(f)

# ---------- Анализ ----------
findings = []

# 1. Проблемы с поиском
failed_percent = obs.get("failed_percent", 0)
if failed_percent > 30:
    findings.append({
        "type": "search_quality",
        "severity": "high",
        "message": f"Слишком много запросов без ответа: {failed_percent}%",
        "action": "download_books"
    })
elif failed_percent > 15:
    findings.append({
        "type": "search_quality",
        "severity": "medium",
        "message": f"Умеренный процент неудач: {failed_percent}%",
        "action": "expand_knowledge"
    })

# 2. Проблемные слова → темы для книг
top_words = obs.get("top_failed_words", [])
for word, count in top_words[:3]:
    if count >= 3:
        findings.append({
            "type": "missing_topic",
            "severity": "high",
            "message": f"Нет материала по теме '{word}' — {count} запросов без ответа",
            "action": "download_book",
            "topic": word
        })

# 3. Производительность
avg_ms = obs.get("avg_response_ms", 0)
if avg_ms > 5000:
    findings.append({
        "type": "performance",
        "severity": "medium",
        "message": f"Медленный отклик: {avg_ms} мс",
        "action": "optimize_search"
    })

# 4. Ошибки
errors = obs.get("errors", 0)
if errors > 5:
    findings.append({
        "type": "errors",
        "severity": "high",
        "message": f"{errors} ошибок — проверь логи",
        "action": "check_logs"
    })

# 5. Качество эмбеддингов
avg_distance = obs.get("avg_distance", 0)
if avg_distance and avg_distance < 0.5:
    findings.append({
        "type": "embedding_quality",
        "severity": "medium",
        "message": f"Низкий средний score ({avg_distance}) — модель плохо различает",
        "action": "retrain_index"
    })

# ---------- Сохранение ----------
analysis = {
    "generated_at": datetime.utcnow().isoformat(),
    "source": OBSERVATION_FILE,
    "total_queries": obs.get("total_queries", 0),
    "findings_count": len(findings),
    "findings": findings
}

os.makedirs("data", exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(analysis, f, ensure_ascii=False, indent=2)

# ---------- Вывод ----------
print("🔎 АНАЛИЗ ARGUS")
print("=" * 50)
print(f"Запросов: {obs.get('total_queries', 0)}")
print(f"Найдено проблем: {len(findings)}")
print()

for f in findings:
    icon = "🔴" if f["severity"] == "high" else "🟡"
    print(f"{icon} {f['message']}")

if not findings:
    print("✅ Проблем не найдено.")

print("=" * 50)
print(f"✅ Сохранено в {OUTPUT_FILE}")