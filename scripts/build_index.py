# ============================================================
# ARGUS — ПОСТРОЕНИЕ ИНДЕКСА (v2)
# Многоязычная модель + нормализация + Inner Product
# ============================================================

import os
import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

if not os.path.exists("data/knowledge.json"):
    print("❌ knowledge.json не найден.")
    exit(1)

with open("data/knowledge.json", "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = [c["text"] for c in knowledge["chunks"]]
print(f"📚 Чанков: {len(chunks)}")

# Многоязычная модель — понимает русский и английский
MODEL_NAME = "intfloat/multilingual-e5-small"
print(f"📦 Загружаю модель: {MODEL_NAME}")
model = SentenceTransformer(MODEL_NAME)

# Нормализация эмбеддингов (для cosine similarity)
print("🧠 Создаю эмбеддинги...")
embeddings = model.encode(
    chunks,
    normalize_embeddings=True,   # ← ключевое
    show_progress_bar=True,
    batch_size=32
)
embeddings = np.array(embeddings).astype("float32")

# Inner Product вместо L2
dimension = embeddings.shape[1]
index = faiss.IndexFlatIP(dimension)   # ← IP вместо L2
index.add(embeddings)

faiss.write_index(index, "data/faiss.index")

with open("data/chunks_for_index.json", "w", encoding="utf-8") as f:
    json.dump(chunks, f, ensure_ascii=False)

print(f"✅ Индекс создан: {index.ntotal} векторов")