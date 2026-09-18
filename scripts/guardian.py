# ============================================================
# ARGUS — ХРАНИТЕЛЬ КАЧЕСТВА
# Тестирует поиск, снимки версий, откат при деградации
# ============================================================

import os
import sys
import json
import time
import numpy as np
import faiss
from datetime import datetime
from sentence_transformers import SentenceTransformer

# ---------- Пути ----------
INDEX_FILE = "data/faiss.index"
CHUNKS_FILE = "data/chunks_for_index.json"
GOLDEN_FILE = "data/golden_questions.json"
QUALITY_FILE = "data/quality.json"
SNAPSHOT_DIR = "data/snapshots"

MODEL_NAME = "intfloat/multilingual-e5-small"


# ============================================================
# ЭТАЛОННЫЕ ВОПРОСЫ
# ============================================================
# Формат: {"query": "...", "expected_keyword": "..."}
# Если в топ-3 есть чанк с этим ключевым словом — тест пройден.

DEFAULT_GOLDEN = [
    {"query": "Что такое Новая Атлантида?", "expected_keyword": "Атлантида"},
    {"query": "Кто написал Новую Атлантиду?", "expected_keyword": "Бэкон"},
    {"query": "О чём книга Френсиса Бэкона?", "expected_keyword": "Бэкон"},
]


# ============================================================
# РЕЖИМЫ РАБОТЫ
# ============================================================
# guardian.py snapshot   — снимок текущего состояния
# guardian.py test       — тест качества
# guardian.py rollback   — откат к последнему снимку


def ensure_golden():
    """Создаёт файл эталонных вопросов, если его нет."""
    if not os.path.exists(GOLDEN_FILE):
        with open(GOLDEN_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_GOLDEN, f, ensure_ascii=False, indent=2)
        print(f"✅ Создан {GOLDEN_FILE}")
    with open(GOLDEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def snapshot():
    """Сохраняет текущие index + chunks в папку snapshots с меткой времени."""
    if not os.path.exists(INDEX_FILE) or not os.path.exists(CHUNKS_FILE):
        print("❌ Нечего снимать: нет index или chunks.")
        return

    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    with open(INDEX_FILE, "rb") as f:
        index_data = f.read()
    with open(CHUNKS_FILE, "rb") as f:
        chunks_data = f.read()

    with open(f"{SNAPSHOT_DIR}/faiss_{ts}.index", "wb") as f:
        f.write(index_data)
    with open(f"{SNAPSHOT_DIR}/chunks_{ts}.json", "wb") as f:
        f.write(chunks_data)

    # Указатель на последний снимок
    with open(f"{SNAPSHOT_DIR}/latest.json", "w") as f:
        json.dump({"timestamp": ts}, f)

    print(f"✅ Снимок создан: {ts}")


def rollback():
    """Возвращает последний снимок на место."""
    latest_file = f"{SNAPSHOT_DIR}/latest.json"
    if not os.path.exists(latest_file):
        print("❌ Нет снимков для отката.")
        return

    with open(latest_file) as f:
        ts = json.load(f)["timestamp"]

    index_snap = f"{SNAPSHOT_DIR}/faiss_{ts}.index"
    chunks_snap = f"{SNAPSHOT_DIR}/chunks_{ts}.json"

    if not os.path.exists(index_snap) or not os.path.exists(chunks_snap):
        print("❌ Файлы снимка повреждены.")
        return

    with open(index_snap, "rb") as f:
        index_data = f.read()
    with open(chunks_snap, "rb") as f:
        chunks_data = f.read()

    with open(INDEX_FILE, "wb") as f:
        f.write(index_data)
    with open(CHUNKS_FILE, "wb") as f:
        f.write(chunks_data)

    print(f"✅ Откат выполнен к снимку {ts}")


def test_quality():
    """Проверяет качество поиска на эталонных вопросах."""
    if not os.path.exists(INDEX_FILE) or not os.path.exists(CHUNKS_FILE):
        print("❌ Нет index или chunks.")
        return None

    golden = ensure_golden()

    print("📦 Загружаю модель и индекс...")
    model = SentenceTransformer(MODEL_NAME)
    index = faiss.read_index(INDEX_FILE)

    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        chunks = json.load(f)

    passed = 0
    results = []

    for item in golden:
        query = item["query"]
        keyword = item["expected_keyword"].lower()

        vec = model.encode([query], normalize_embeddings=True).astype("float32")
        distances, indices = index.search(vec, k=3)

        # Проверяем топ-3: есть ли там ключевое слово
        found = False
        for idx in indices[0]:
            if 0 <= idx < len(chunks):
                if keyword in chunks[idx].lower():
                    found = True
                    break

        if found:
            passed += 1

        results.append({
            "query": query,
            "keyword": keyword,
            "passed": found,
            "top_score": float(distances[0][0])
        })

    accuracy = passed / len(golden) * 100

    quality = {
        "tested_at": datetime.utcnow().isoformat(),
        "total": len(golden),
        "passed": passed,
        "accuracy": round(accuracy, 1),
        "results": results
    }

    with open(QUALITY_FILE, "w", encoding="utf-8") as f:
        json.dump(quality, f, ensure_ascii=False, indent=2)

    print("=" * 50)
    print(f"🎯 ТОЧНОСТЬ: {passed}/{len(golden)} = {accuracy:.1f}%")
    for r in results:
        mark = "✅" if r["passed"] else "❌"
        print(f"   {mark} {r['query']} (score {r['top_score']:.3f})")
    print("=" * 50)

    return accuracy


# ============================================================
# ЗАПУСК
# ============================================================
if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "test"

    if mode == "snapshot":
        snapshot()
    elif mode == "rollback":
        rollback()
    elif mode == "test":
        test_quality()
    else:
        print("Использование: guardian.py [snapshot|test|rollback]")