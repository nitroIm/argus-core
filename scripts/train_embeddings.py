# ============================================================
# ARGUS — TRAIN EMBEDDINGS (v3)
# v3: FROZEN model, incremental — считаем ТОЛЬКО новые чанки.
#     Никакого fine-tuning'а. Векторы всегда совместимы.
# ============================================================

import os
import json
import tempfile
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

from sentence_transformers import SentenceTransformer

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
METADATA_FILE = DATA_DIR / "chunks_metadata.json"

# Промежуточные файлы (не коммитятся) — передаются в build_index.py
TMP_DIR = Path(tempfile.gettempdir()) / "argus_train"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEW_EMBEDDINGS_FILE = TMP_DIR / "new_embeddings.npy"
NEW_IDS_FILE = TMP_DIR / "new_chunk_ids.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"

# --- Модель (замороженная) ---
MODEL_NAME = "intfloat/multilingual-e5-base"
MODEL_DIM = 768
MODEL_VERSION = "e5-base-v1"
PASSAGE_PREFIX = "passage: "  # Обязательно для E5!

# --- Параметры ---
BATCH_SIZE = 32


# ============================================================
# 1. LOAD KNOWLEDGE
# ============================================================
if not KNOWLEDGE_FILE.exists():
    raise SystemExit("❌ knowledge.json не найден — сначала ingest")

with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = knowledge.get("chunks", [])
print(f"📚 Всего чанков в knowledge.json: {len(chunks)}")

if len(chunks) == 0:
    raise SystemExit("❌ knowledge.json пуст")


# ============================================================
# 2. LOAD EXISTING METADATA (что уже проэмбеддено)
# ============================================================
existing_ids = set()
existing_model = None
existing_dim = None

if METADATA_FILE.exists():
    try:
        with open(METADATA_FILE, encoding="utf-8") as f:
            meta = json.load(f)
        existing_ids = set(meta.get("ids", []))
        existing_model = meta.get("model_name")
        existing_dim = meta.get("dim")
        print(f"📊 Существующий индекс: {len(existing_ids)} векторов, "
              f"модель={existing_model}, dim={existing_dim}")
    except Exception as e:
        print(f"⚠️ Не могу прочитать chunks_metadata.json: {e}")
        print("   → будут пересчитаны ВСЕ эмбеддинги")


# ============================================================
# 3. ПРОВЕРКА СОВМЕСТИМОСТИ МОДЕЛИ
# ============================================================
force_full_rebuild = False

if existing_model and existing_model != MODEL_NAME:
    print(f"🚨 Модель изменилась: {existing_model} → {MODEL_NAME}")
    print("   → требуется полный пересбор индекса")
    force_full_rebuild = True

if existing_dim and existing_dim != MODEL_DIM:
    print(f"🚨 Размерность изменилась: {existing_dim} → {MODEL_DIM}")
    force_full_rebuild = True

if force_full_rebuild:
    existing_ids = set()
    # Удаляем старый индекс — build_index.py создаст новый с нуля
    for f in [DATA_DIR / "faiss.index", METADATA_FILE]:
        if f.exists():
            f.unlink()
            print(f"🗑️ Удалён {f.name} (для полного пересбора)")


# ============================================================
# 4. НАХОДИМ НОВЫЕ ЧАНКИ
# ============================================================
new_chunks = [c for c in chunks if c.get("id") not in existing_ids]
print(f"✨ Новых чанков для эмбеддинга: {len(new_chunks)}")

if len(new_chunks) == 0:
    print("✅ Все чанки уже проэмбеддены — нечего делать")
    # Создаём пустые файлы, чтобы build_index.py понял, что ничего нового
    np.save(NEW_EMBEDDINGS_FILE, np.zeros((0, MODEL_DIM), dtype=np.float32))
    with open(NEW_IDS_FILE, "w") as f:
        json.dump([], f)
    raise SystemExit(0)


# ============================================================
# 5. ЗАГРУЖАЕМ МОДЕЛЬ И СЧИТАЕМ ЭМБЕДДИНГИ
# ============================================================
print(f"🤖 Загрузка модели: {MODEL_NAME}")
model = SentenceTransformer(MODEL_NAME)

texts = [PASSAGE_PREFIX + c["text"] for c in new_chunks]
print(f"🧮 Считаю эмбеддинги для {len(texts)} чанков...")

embeddings = model.encode(
    texts,
    batch_size=BATCH_SIZE,
    normalize_embeddings=True,   # critical for cosine
    show_progress_bar=True,
    convert_to_numpy=True,
).astype(np.float32)

print(f"✅ Получено: shape={embeddings.shape}, dtype={embeddings.dtype}")

# Проверка размерности
if embeddings.shape[1] != MODEL_DIM:
    raise RuntimeError(
        f"❌ Размерность модели {embeddings.shape[1]} != ожидаемой {MODEL_DIM}"
    )


# ============================================================
# 6. СОХРАНЕНИЕ ПРОМЕЖУТОЧНЫХ ФАЙЛОВ
# ============================================================
np.save(NEW_EMBEDDINGS_FILE, embeddings)
new_ids = [c["id"] for c in new_chunks]
with open(NEW_IDS_FILE, "w", encoding="utf-8") as f:
    json.dump(new_ids, f)

with open(MODEL_INFO_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "dim": MODEL_DIM,
        "passage_prefix": PASSAGE_PREFIX,
        "query_prefix": "query: ",  # ВАЖНО для retrieval!
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, f, ensure_ascii=False, indent=2)

print(f"💾 Сохранено: {NEW_EMBEDDINGS_FILE}")
print(f"💾 Сохранено: {NEW_IDS_FILE}")
print(f"💾 Сохранено: {MODEL_INFO_FILE}")
print()
print(f"🎉 TRAIN завершён. Новых векторов: {embeddings.shape[0]}")