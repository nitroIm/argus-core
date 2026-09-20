# ============================================================
# ARGUS — TRAIN EMBEDDINGS (v3.json.4)
# v3.4: fallback — если у чанка нет "id", генерируем из md5(text).
#       Работает со старым knowledge без пересборки.
# ============================================================

import json
import hashlib
import tempfile
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

from sentence_transformers import SentenceTransformer

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
METADATA_FILE = DATA_DIR / "chunks_for_index.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"

FINETUNED_MODEL_DIR = MODELS_DIR / "argus-embeddings"

TMP_DIR = Path(tempfile.gettempdir()) / "argus_train"
TMP_DIR.mkdir(parents=True, exist_ok=True)
NEW_EMBEDDINGS_FILE = TMP_DIR / "new_embeddings.npy"
NEW_IDS_FILE = TMP_DIR / "new_chunk_ids.json"

# --- Fallback ---
FALLBACK_MODEL_NAME = "intfloat/multilingual-e5-small"
FALLBACK_DIM = 384
BATCH_SIZE = 32


def ensure_chunk_id(c: dict) -> str:
    """Возвращает id чанка. Если нет — генерирует из md5 текста (детерминированно)."""
    cid = c.get("id")
    if cid:
        return cid
    text = c.get("text", "")
    h = hashlib.md5(text.encode("utf-8")).hexdigest()
    return f"auto#{h[:12]}"


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

# Проставляем id всем чанкам на лету (если нет)
missing_id = sum(1 for c in chunks if not c.get("id"))
if missing_id > 0:
    print(f"⚠️ У {missing_id} чанков нет 'id' — генерирую из текста (fallback)")

for c in chunks:
    if not c.get("id"):
        c["id"] = ensure_chunk_id(c)


# ============================================================
# 2. СУЩЕСТВУЮЩИЕ МЕТАДАННЫЕ
# ============================================================
existing_ids = set()

if METADATA_FILE.exists():
    try:
        with open(METADATA_FILE, encoding="utf-8") as f:
            meta = json.load(f)
        if isinstance(meta, list) and meta and isinstance(meta[0], dict):
            existing_ids = {item.get("id") for item in meta if item.get("id")}
            print(f"📊 Существующий индекс: {len(existing_ids)} id (формат: dict)")
        elif isinstance(meta, list) and meta and isinstance(meta[0], str):
            print("⚠️ chunks_for_index.json в СТАРОМ формате (список строк)")
            print("   → все чанки будут переэмбеддены заново")
        else:
            print("⚠️ chunks_for_index.json не список — пересбор")
    except Exception as e:
        print(f"⚠️ Не могу прочитать chunks_for_index.json: {e}")
        print("   → будут пересчитаны ВСЕ эмбеддинги")


# ============================================================
# 3. ВЫБОР МОДЕЛИ
# ============================================================
use_finetuned = FINETUNED_MODEL_DIR.exists() and any(FINETUNED_MODEL_DIR.iterdir())

if use_finetuned:
    MODEL_PATH = str(FINETUNED_MODEL_DIR)
    MODEL_LABEL = "argus-finetuned"
    print(f"🤖 Использую fine-tuned модель: {MODEL_PATH}")
    PREFIX_PASSAGE = ""
    PREFIX_QUERY = ""
else:
    MODEL_PATH = FALLBACK_MODEL_NAME
    MODEL_LABEL = FALLBACK_MODEL_NAME
    print(f"🤖 Fine-tuned не найдена, fallback: {MODEL_PATH}")
    PREFIX_PASSAGE = "passage: "
    PREFIX_QUERY = "query: "


# ============================================================
# 4. НОВЫЕ ЧАНКИ
# ============================================================
new_chunks = [c for c in chunks if c["id"] not in existing_ids]
print(f"✨ Новых чанков для эмбеддинга: {len(new_chunks)}")

if len(new_chunks) == 0:
    print("✅ Все чанки уже проэмбеддены — нечего делать")
    np.save(NEW_EMBEDDINGS_FILE, np.zeros((0, FALLBACK_DIM), dtype=np.float32))
    with open(NEW_IDS_FILE, "w") as f:
        json.dump([], f)
    raise SystemExit(0)


# ============================================================
# 5. СЧИТАЕМ ЭМБЕДДИНГИ
# ============================================================
model = SentenceTransformer(MODEL_PATH)
actual_dim = model.get_sentence_embedding_dimension()
print(f"📐 Размерность модели: {actual_dim}")

texts = [PREFIX_PASSAGE + c.get("text", "") for c in new_chunks]
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
    raise RuntimeError(f"❌ Размерность {embeddings.shape[1]} != {actual_dim}")


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