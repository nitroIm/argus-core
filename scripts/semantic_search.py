# ============================================================
# ARGUS — ПОИСК ПО СМЫСЛУ
# ============================================================

import sys
import json
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

query = " ".join(sys.argv[1:])

# 1. Загружаем
model = SentenceTransformer("models/argus-embeddings")
index = faiss.read_index("data/faiss.index")

with open("data/chunks_for_index.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)

# 2. Кодируем вопрос
query_vec = model.encode([query]).astype("float32")

# 3. Ищем топ-5
distances, indices = index.search(query_vec, k=5)

# 4. Выводим
print(f"🔍 Вопрос: {query}\n")
for i, idx in enumerate(indices[0]):
    print(f"--- #{i+1} (расстояние: {distances[0][i]:.3f}) ---")
    print(chunks[idx][:400])
    print()