# ============================================================
# ARGUS — BUILD INDEX (v3.1 — совместим с GUIDE.md)
# v3.1: chunks_metadata.json = [{id, source, book, text}, ...]
#       как ожидают ask.py / search.py / reranker.py
# ============================================================

import json
import tempfile
import numpy as np
from datetime import datetime, timezone
from pathlib import Path

import faiss

# --- Пути (pathlib) ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
INDEX_FILE = DATA_DIR / "faiss.index"
METADATA_FILE = DATA_DIR / "chunks_metadata.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"

TMP_DIR = Path(tempfile.gettempdir()) / "argus_train"
NEW_EMBEDDINGS_FILE = TMP_DIR / "new_embeddings.npy"
NEW_IDS_FILE = TMP_DIR / "new_chunk_ids.json"


# ============================================================
# 1. ПРОВЕРКА ПРОМЕЖУТОЧНЫХ
# ============================================================
if not NEW_EMBEDDINGS_FILE.exists():
    raise SystemExit(f"❌ {NEW_EMBEDDINGS_FILE} не найден — сначала train_embeddings.py")

new_embeddings = np.load(NEW_EMBEDDINGS_FILE).astype(np.float32)
with open(NEW_IDS_FILE, encoding="utf-8") as f:
    new_ids = json.load(f)

assert new_embeddings.shape[0] == len(new_ids), \
    f"❌ Рассинхрон: {new_embeddings.shape[0]} векторов vs {len(new_ids)} ids"

print(f"📥 Загружено: {new_embeddings.shape[0]} новых векторов")


# ============================================================
# 2. KNOWLEDGE (для маппинга id → {source, book, text})
# ============================================================
if not KNOWLEDGE_FILE.exists():
    raise SystemExit("❌ knowledge.json не найден")

with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
    knowledge = json.load(f)

chunk_by_id = {c["id"]: c for c in knowledge.get("chunks", []) if "id" in c}


# ============================================================
# 3. СУЩЕСТВУЮЩИЙ ИНДЕКС
# ============================================================
existing_index = None
existing_metadata = []

if INDEX_FILE.exists() and METADATA_FILE.exists():
    try:
        existing_index = faiss.read_index(str(INDEX_FILE))
        with open(METADATA_FILE, encoding="utf-8") as f:
            existing_metadata = json.load(f)

        if not isinstance(existing_metadata, list):
            print("⚠️ chunks_metadata.json не список — полный пересбор")
            existing_index = None
            existing_metadata = []
        elif existing_index.ntotal != len(existing_metadata):
            print(f"🚨 Рассинхрон: {existing_index.ntotal} векторов vs "
                  f"{len(existing_metadata)} метаданных — полный пересбор")
            existing_index = None
            existing_metadata = []
        else:
            print(f"📊 Существующий индекс: {existing_index.ntotal} векторов, "
                  f"dim={existing_index.d}")
    except Exception as e:
        print(f"⚠️ Не могу прочитать индекс: {e} — полный пересбор")
        existing_index = None
        existing_metadata = []


# ============================================================
# 4. РАЗМЕРНОСТЬ
# ============================================================
if existing_index is not None:
    dim = existing_index.d
elif new_embeddings.shape[0] > 0:
    dim = new_embeddings.shape[1]
elif MODEL_INFO_FILE.exists():
    with open(MODEL_INFO_FILE, encoding="utf-8") as f:
        dim = json.load(f)["dim"]
else:
    dim = 384

print(f"📐 Размерность: {dim}")


# ============================================================
# 5. СОЗДАЁМ ИЛИ ДОПОЛНЯЕМ
# ============================================================
if existing_index is None:
    print("🆕 Создаю новый индекс с нуля")
    index = faiss.IndexFlatIP(dim)
    all_metadata = []
else:
    print(f"➕ Дополняю ({existing_index.ntotal} → +{new_embeddings.shape[0]})")
    index = existing_index
    all_metadata = list(existing_metadata)


# ============================================================
# 6. ДОБАВЛЯЕМ НОВЫЕ
# ============================================================
if new_embeddings.shape[0] > 0:
    if not np.isfinite(new_embeddings).all():
        raise RuntimeError("❌ В new_embeddings есть NaN или inf")

    if new_embeddings.shape[1] != dim:
        raise RuntimeError(
            f"❌ Dim новых {new_embeddings.shape[1]} != dim индекса {dim}"
        )

    index.add(new_embeddings)

    for cid in new_ids:
        chunk = chunk_by_id.get(cid)
        if chunk is None:
            print(f"⚠️ Чанк {cid} не найден в knowledge.json — пропуск")
            continue
        all_metadata.append({
            "id": cid,
            "source": chunk.get("source", ""),
            "book": chunk.get("book", ""),
            "text": chunk.get("text", ""),
        })

print(f"✅ Индекс: {index.ntotal} векторов, dim={index.d}")


# ============================================================
# 7. СОХРАНЕНИЕ
# ============================================================
faiss.write_index(index, str(INDEX_FILE))

with open(METADATA_FILE, "w", encoding="utf-8") as f:
    json.dump(all_metadata, f, ensure_ascii=False, indent=2)

print(f"💾 Сохранено: {INDEX_FILE} ({index.ntotal} векторов)")
print(f"💾 Сохранено: {METADATA_FILE} ({len(all_metadata)} записей)")

for f in [NEW_EMBEDDINGS_FILE, NEW_IDS_FILE]:
    if f.exists():
        f.unlink()

print()
print(f"🎉 BUILD завершён. Итого векторов: {index.ntotal}")