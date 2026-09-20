# ============================================================
# ARGUS — TRAIN EMBEDDINGS (v3.1 — совместим с GUIDE.md)
# v3.1: использует СУЩЕСТВУЮЩУЮ модель (fine-tuned или базовую),
#       не переобучает, считает ТОЛЬКО новые чанки.
# ============================================================

import json
import tempfile
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

from sentence_transformers import SentenceTransformer

# --- Пути (pathlib) ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
METADATA_FILE = DATA_DIR / "chunks_metadata.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"

FINETUNED_MODEL_DIR = MODELS_DIR / "argus-embeddings"

TMP_DIR = Path(tempfile.gettempdir()) / "argus_train"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEW_EMBEDDINGS_FILE = TMP_DIR / "new_embeddings.npy"
NEW_IDS_FILE = TMP_DIR / "new_chunk_ids.json"

# --- Модель ---
FALLBACK_MODEL_NAME = "intfloat/multilingual-e5-small"
FALLBACK_DIM = 384
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
# 2. ЗАГРУЗКА СУЩЕСТВУЮЩИХ МЕТАДАННЫХ (формат GUIDE: список)
# ============================================================
existing_ids = set()

if METADATA_FILE.exists():
    try:
        with open(METADATA_FILE, encoding="utf-8") as f:
            meta = json.load(f)
        # Формат GUIDE — список словарей
        if isinstance(meta, list):
            existing_ids = {item.get("id") for item in meta if item.get("id")}
            print(f"📊 Существующий индекс: {len(existing_ids)} id")
        else:
            print("⚠️ chunks_metadata.json не в формате списка — все чанки будут новыми")
    except Exception as e:
        print(f"⚠️ Не могу прочитать chunks_metadata.json: {e}")
        print("   → будут пересчитаны ВСЕ эмбеддинги")


# ============================================================
# 3. ВЫБОР МОДЕЛИ: fine-tuned или fallback
# ============================================================
use_finetuned = FINETUNED_MODEL_DIR.exists() and any(FINETUNED_MODEL_DIR.iterdir())

if use_finetuned:
    MODEL_PATH = str(FINETUNED_MODEL_DIR)
    MODEL_LABEL = "argus-finetuned"
    print(f"🤖 Использую fine-tuned модель: {MODEL_PATH}")
    # Fine-tuned модель обучена БЕЗ префиксов — не добавляем
    PREFIX_PASSAGE = ""
    PREFIX_QUERY = ""
else:
    MODEL_PATH = FALLBACK_MODEL_NAME
    MODEL_LABEL = FALLBACK_MODEL_NAME
    print(f"🤖 Fine-tuned модель не найдена, использую базовую: {MODEL_PATH}")
    # Базовая E5 требует префиксы
    PREFIX_PASSAGE = "passage: "
    PREFIX_QUERY = "query: "


# ============================================================
# 4. НАХОДИМ НОВЫЕ ЧАНКИ
# ============================================================
new_chunks = [c for c in chunks if c.get("id") not in existing_ids]
print(f"✨ Новых чанков для эмбеддинга: {len(new_chunks)}")

if len(new_chunks) == 0:
    print("✅ Все чанки уже проэмбеддены — нечего делать")
    np.save(NEW_EMBEDDINGS_FILE, np.zeros((0, FALLBACK_DIM), dtype=np.float32))
    with open(NEW_IDS_FILE, "w") as f:
        json.dump([], f)
    raise SystemExit(0)


# ============================================================
# 5. ЗАГРУЖАЕМ МОДЕЛЬ И СЧИТАЕМ ЭМБЕДДИНГИ
# ============================================================
model = SentenceTransformer(MODEL_PATH)
actual_dim = model.get_sentence_embedding_dimension()
print(f"📐 Размерность модели: {actual_dim}")

texts = [PREFIX_PASSAGE + c["text"] for c in new_chunks]
print(f"🧮 Считаю эмбеддинги для {len(texts)} чанков...")

embeddings = model.encode(
    texts,
    batch_size=BATCH_SIZE,
    normalize_embeddings=True,
    show_progress_bar=True,
    convert_to_numpy=True,
).astype(np.float32)

print(f"✅ Получено: shape={embeddings.shape}")

if embeddings.shape[1] != actual_dim:
    raise RuntimeError(
        f"❌ Размерность {embeddings.shape[1]} != {actual_dim}"
    )


# ============================================================
# 6. СОХРАНЕНИЕ
# ============================================================
np.save(NEW_EMBEDDINGS_FILE, embeddings)
new_ids = [c["id"] for c in new_chunks]
with open(NEW_IDS_FILE, "w", encoding="utf-8") as f:
    json.dump(new_ids, f)

with open(MODEL_INFO_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "model_path": MODEL_PATH,
        "model_label": MODEL_LABEL,
        "dim": actual_dim,
        "passage_prefix": PREFIX_PASSAGE,
        "query_prefix": PREFIX_QUERY,
        "is_finetuned": use_finetuned,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, f, ensure_ascii=False, indent=2)

print(f"💾 Сохранено: {NEW_EMBEDDINGS_FILE}")
print(f"💾 Сохранено: {NEW_IDS_FILE}")
print(f"💾 Сохранено: {MODEL_INFO_FILE}")
print()
print(f"🎉 TRAIN завершён. Новых векторов: {embeddings.shape[0]}")