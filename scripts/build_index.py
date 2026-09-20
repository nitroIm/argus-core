# ============================================================
# ARGUS — BUILD INDEX (v3)
# v3: append новых векторов к существующему faiss.index
# ============================================================

import os
import json
import tempfile
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

import faiss

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
INDEX_FILE = DATA_DIR / "faiss.index"
METADATA_FILE = DATA_DIR / "chunks_metadata.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"

TMP_DIR = Path(tempfile.gettempdir()) / "argus_train"
NEW_EMBEDDINGS_FILE = TMP_DIR / "new_embeddings.npy"
NEW_IDS_FILE = TMP_DIR / "new_chunk_ids.json"


# ============================================================
# 1. ПРОВЕРКА ПРОМЕЖУТОЧНЫХ ФАЙЛОВ
# ============================================================
if not NEW_EMBEDDINGS_FILE.exists():
    raise SystemExit(f"❌ {NEW_EMBEDDINGS_FILE} не найден — сначала train_embeddings.py")

new_embeddings = np.load(NEW_EMBEDDINGS_FILE).astype(np.float32)
with open(NEW_IDS_FILE, encoding="utf-8") as f:
    new_ids = json.load(f)

assert new_embeddings.shape[0] == len(new_ids), \
    f"❌ Рассинхрон: {new_embeddings.shape[0]} векторов vs {len(new_ids)} ids"

print(f"📥 Загружено: {new_embeddings.shape[0]} новых векторов, dim={new_embeddings.shape[1] if new_embeddings.shape[0] else 0}")


# ============================================================
# 2. МОДЕЛЬ (для метаданных)
# ============================================================
if not MODEL_INFO_FILE.exists():
    raise SystemExit(f"❌ {MODEL_INFO_FILE} не найден")
with open(MODEL_INFO_FILE, encoding="utf-8") as f:
    model_info = json.load(f)

model_name = model_info["model_name"]
dim = model_info["dim"]


# ============================================================
# 3. ЗАГРУЗКА СУЩЕСТВУЮЩЕГО ИНДЕКСА (если есть)
# ============================================================
existing_index = None
existing_metadata = {"ids": [], "model_name": None, "dim": None}

if INDEX_FILE.exists() and METADATA_FILE.exists():
    try:
        existing_index = faiss.read_index(str(INDEX_FILE))
        with open(METADATA_FILE, encoding="utf-8") as f:
            existing_metadata = json.load(f)

        # Проверка совместимости
        old_dim = existing_index.d
        old_count = existing_index.ntotal
        old_ids = existing_metadata.get("ids", [])
        old_model = existing_metadata.get("model_name")

        print(f"📊 Существующий индекс: {old_count} векторов, dim={old_dim}, model={old_model}")

        # Рассинхрон 1: dim не совпадает с моделью
        if old_dim != dim:
            print(f"🚨 dim индекса ({old_dim}) != dim модели ({dim}) — полный пересбор")
            existing_index = None
            existing_metadata = {"ids": [], "model_name": None, "dim": None}
        # Рассинхрон 2: модель не совпадает
        elif old_model and old_model != model_name:
            print(f"🚨 модель индекса ({old_model}) != модель ({model_name}) — полный пересбор")
            existing_index = None
            existing_metadata = {"ids": [], "model_name": None, "dim": None}
        # Рассинхрон 3: количество векторов != количество ids
        elif old_count != len(old_ids):
            print(f"🚨 Рассинхрон: {old_count} векторов vs {len(old_ids)} ids — полный пересбор")
            existing_index = None
            existing_metadata = {"ids": [], "model_name": None, "dim": None}

    except Exception as e:
        print(f"⚠️ Не могу прочитать существующий индекс: {e} — полный пересбор")
        existing_index = None
        existing_metadata = {"ids": [], "model_name": None, "dim": None}


# ============================================================
# 4. СОЗДАЁМ ИЛИ ДОПОЛНЯЕМ ИНДЕКС
# ============================================================
if existing_index is None:
    print("🆕 Создаю новый индекс с нуля")
    # Inner Product на нормализованных векторах = cosine similarity
    index = faiss.IndexFlatIP(dim)
    all_ids = []
else:
    print(f"➕ Дополняю существующий индекс ({existing_index.ntotal} → +{new_embeddings.shape[0]})")
    index = existing_index
    all_ids = list(existing_metadata["ids"])


# Защита: не добавляем пустоту
if new_embeddings.shape[0] > 0:
    # Финальная проверка на NaN/inf
    if not np.isfinite(new_embeddings).all():
        raise RuntimeError("❌ В new_embeddings есть NaN или inf")
    index.add(new_embeddings)
    all_ids.extend(new_ids)

print(f"✅ Индекс: {index.ntotal} векторов, dim={index.d}")


# ============================================================
# 5. СОХРАНЕНИЕ
# ============================================================
faiss.write_index(index, str(INDEX_FILE))

with open(METADATA_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "model_name": model_name,
        "model_version": model_info.get("model_version"),
        "dim": dim,
        "passage_prefix": model_info.get("passage_prefix"),
        "query_prefix": model_info.get("query_prefix"),
        "count": index.ntotal,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ids": all_ids,
    }, f, ensure_ascii=False, indent=2)

print(f"💾 Сохранено: {INDEX_FILE} ({index.ntotal} векторов)")
print(f"💾 Сохранено: {METADATA_FILE}")

# Чистим промежуточные файлы
for f in [NEW_EMBEDDINGS_FILE, NEW_IDS_FILE]:
    if f.exists():
        f.unlink()

print()
print(f"🎉 BUILD завершён. Итого векторов: {index.ntotal}")