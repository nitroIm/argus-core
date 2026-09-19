# ============================================================
# ARGUS — АНАЛИЗАТОР (v2)
# v2: pathlib, фикс действий под actor.py, timezone, безопасный парсинг
# ============================================================

import sys
import json
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

OBSERVATION_FILE = DATA_DIR / "observation.json"
OUTPUT_FILE = DATA_DIR / "analysis.json"

# ---------- Проверки ----------
if not OBSERVATION_FILE.exists():
    print("❌ Нет observation.json. Сначала запусти observer.")
    sys.exit(0)

try:
    with open(OBSERVATION_FILE, "r", encoding="utf-8") as f:
        obs = json.load(f)
except json.JSONDecodeError:
    print("❌ observation.json повреждён или пуст.")
    sys.exit(1)

# ---------- Анализ ----------
findings = []

# 1. Проблемы с поиском (качество базы знаний)
failed_percent = obs.get("failed_percent", 0)
if failed_percent > 30:
    findings.append({
        "type": "search_quality",
        "severity": "high",
        "message": f"Критически много запросов без ответа: {failed_percent}%",
        "action": "rebuild_index"  # Совпадает с actor.py
    })
elif failed_percent > 15:
    findings.append({
        "type": "search_quality",
        "severity": "medium",
        "message": f"Умеренный процент неудач: {failed_percent}%. Требуется расширение базы.",
        "action": "download_multiple"  # Совпадает с actor.py
    })

# 2. Проблемные слова → темы для новых книг
top_words = obs.get("top_failed_words", [])
# Поддержка и списка [[word, count]], и словаря {"word": count}
if isinstance(top_words, dict):
    top_words_list = list(top_words.items())
else:
    top_words_list = top_words

for item in top_words_list[:3]:
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        word, count = item[0], item[1]
    elif isinstance(item, dict):
        # Если вдруг формат изменился
        continue 
    else:
        continue
        
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
        "message": f"Медленный отклик системы: {avg_ms} мс",
        "action": "rebuild_index"  # Пересборка может оптимизировать FAISS
    })

# 4. Системные ошибки
errors = obs.get("errors", 0)
if errors > 5:
    findings.append({
        "type": "system_errors",
        "severity": "high",
        "message": f"Зафиксировано {errors} ошибок — требуется ручная проверка логов",
        "action": "run_observer"  # Запуск observer может помочь собрать детали
    })

# 5. Качество эмбеддингов (Cosine Similarity via FAISS IP)
# Для нормализованных векторов IP: 1.0 = идеально, < 0.5 = плохо
avg_score = obs.get("avg_score") or obs.get("avg_distance", 1.0)
if avg_score < 0.5:
    findings.append({
        "type": "embedding_quality",
        "severity": "high",
        "message": f"Низкое среднее сходство ({avg_score:.2f}) — модель плохо различает контекст",
        "action": "retrain_index"  # В actor.py это маппится на rebuild_index, или можно добавить полноценный retrain
    })

# ---------- Сохранение ----------
analysis = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "source": str(OBSERVATION_FILE),
    "total_queries": obs.get("total_queries", 0),
    "findings_count": len(findings),
    "findings": findings
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(analysis, f, ensure_ascii=False, indent=2)

# ---------- Вывод ----------
print("🔎 АНАЛИЗ ARGUS")
print("=" * 50)
print(f"Запросов в базе: {obs.get('total_queries', 0)}")
print(f"Найдено проблем: {len(findings)}")
print()

for f in findings:
    icon = "🔴" if f["severity"] == "high" else "🟡"
    print(f"{icon} [{f['severity'].upper()}] {f['message']}")
    print(f"   ↳ Действие: {f['action']}")

if not findings:
    print("✅ Проблем не найдено. Система работает стабильно.")

print("=" * 50)
print(f"✅ Сохранено в {OUTPUT_FILE}")
