# ============================================================
# ARGUS — ХРАНИТЕЛЬ КАЧЕСТВА (v2)
# v2: pathlib, синхронизация с chunks_metadata.json, проверка обученной модели, timezone
# ============================================================

import os
import sys
import json
import time
import numpy as np
import faiss
import requests
from datetime import datetime, timezone
from pathlib import Path
from sentence_transformers import SentenceTransformer

# ---------- Пути от корня репо ----------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
# ИСПРАВЛЕНО: читаем метаданные, а не старый chunks_for_index.json
CHUNKS_FILE = DATA_DIR / "chunks_metadata.json"
GOLDEN_FILE = DATA_DIR / "golden_questions.json"
QUALITY_FILE = DATA_DIR / "quality.json"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# Telegram для уведомлений
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass

# ============================================================
# ЭТАЛОННЫЕ ВОПРОСЫ
# ============================================================
DEFAULT_GOLDEN = [
    {"query": "Что такое Новая Атлантида?", "expected_keyword": "атлантид"},
    {"query": "Кто написал Новую Атлантиду?", "expected_keyword": "бэкон"},
    {"query": "О чём книга Френсиса Бэкона?", "expected_keyword": "бэкон"},
    {"query": "Что такое риск-менеджмент?", "expected_keyword": "риск"},
]


def ensure_golden():
    """Создаёт файл эталонных вопросов, если его нет."""
    if not GOLDEN_FILE.exists():
        with open(GOLDEN_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_GOLDEN, f, ensure_ascii=False, indent=2)
        print(f"✅ Создан {GOLDEN_FILE}")
    with open(GOLDEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def snapshot():
    """Сохраняет текущие index + chunks в папку snapshots с меткой времени."""
    if not INDEX_FILE.exists() or not CHUNKS_FILE.exists():
        print("❌ Нечего снимать: нет index или chunks_metadata.json.")
        return

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    with open(INDEX_FILE, "rb") as f:
        index_data = f.read()
    with open(CHUNKS_FILE, "rb") as f:
        chunks_data = f.read()

    with open(SNAPSHOT_DIR / f"faiss_{ts}.index", "wb") as f:
        f.write(index_data)
    with open(SNAPSHOT_DIR / f"chunks_{ts}.json", "wb") as f:
        f.write(chunks_data)

    # Указатель на последний снимок
    with open(SNAPSHOT_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump({"timestamp": ts}, f)

    print(f"✅ Снимок создан: {ts}")


def rollback():
    """Возвращает последний снимок на место."""
    latest_file = SNAPSHOT_DIR / "latest.json"
    if not latest_file.exists():
        print("❌ Нет снимков для отката.")
        return

    with open(latest_file, "r", encoding="utf-8") as f:
        ts = json.load(f)["timestamp"]

    index_snap = SNAPSHOT_DIR / f"faiss_{ts}.index"
    chunks_snap = SNAPSHOT_DIR / f"chunks_{ts}.json"

    if not index_snap.exists() or not chunks_snap.exists():
        print("❌ Файлы снимка повреждены или отсутствуют.")
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
    notify(f"🔄 <b>ARGUS Guardian:</b> выполнен откат к снимку {ts}")


def test_quality():
    """Проверяет качество поиска на эталонных вопросах."""
    if not INDEX_FILE.exists() or not CHUNKS_FILE.exists():
        print("❌ Нет index или chunks_metadata.json.")
        return None

    golden = ensure_golden()

    # ИСПРАВЛЕНО: проверяем наличие обученной модели, как в build_index и ask
    TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
    if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
        model_path = str(TRAINED_MODEL)
        print(f"🧠 Тестирую ОБУЧЕННУЮ модель: {model_path}")
    else:
        model_path = "intfloat/multilingual-e5-small"
        print(f"📦 Тестирую базовую модель: {model_path}")

    print("📦 Загружаю модель и индекс...")
    model = SentenceTransformer(model_path)
    index = faiss.read_index(str(INDEX_FILE))

    with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
        # Теперь это список словарей, а не строк!
        chunks_meta = json.load(f)

    passed = 0
    results = []

    for item in golden:
        query = item["query"]
        keyword = item["expected_keyword"].lower()

        vec = model.encode([query], normalize_embeddings=True).astype("float32")
        distances, indices = index.search(vec, k=3)

        # Проверяем топ-3: есть ли там ключевое слово в тексте чанка
        found = False
        for idx in indices[0]:
            if 0 <= idx < len(chunks_meta):
                chunk_text = chunks_meta[idx].get("text", "").lower()
                if keyword in chunk_text:
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

    accuracy = (passed / len(golden) * 100) if golden else 0.0

    quality = {
        "tested_at": datetime.now(timezone.utc).isoformat(),
        "model_used": model_path,
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

    # Уведомление в Telegram
    icon = "✅" if accuracy >= 75 else "⚠️" if accuracy >= 50 else "❌"
    msg = f"{icon} <b>ARGUS Guardian Test</b>\n\n"
    msg += f"Модель: <code>{model_path.split('/')[-1]}</code>\n"
    msg += f"Точность: <b>{accuracy:.1f}%</b> ({passed}/{len(golden)})"
    notify(msg)

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
