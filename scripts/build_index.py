# ============================================================
# ARGUS — ПОСТРОЕНИЕ ВЕКТОРНОГО ИНДЕКСА
# ============================================================

import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# 1. Загружаем чанки
with open("data/knowledge.json", "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = [c["text"] for c in knowledge["chunks"]]
print(f"Чанков: {len(chunks)}")

# 2. Своя модель
model = SentenceTransformer("models/argus-embeddings")

# 3. Создаём эмбеддинги
print("Создаю эмбеддинги...")
embeddings = model.encode(chunks, show_progress_bar=True)
embeddings = np.array(embeddings).astype("float32")

# 4. FAISS-индекс
dimension = embeddings.shape[1]
index = faiss.IndexFlatL2(dimension)
index.add(embeddings)

# 5. Сохраняем
faiss.write_index(index, "data/faiss.index")

with open("data/chunks_for_index.json", "w", encoding="utf-8") as f:
    json.dump(chunks, f, ensure_ascii=False)

print(f"✅ Индекс создан: {index.ntotal} векторов")