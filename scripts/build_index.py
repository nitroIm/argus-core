# ============================================================
# ARGUS — ПОСТРОЕНИЕ ИНДЕКСА (v3)
# v3: загрузка обученной модели, сохранение метаданных чанков (для отображения книги в ответе)
# ============================================================

import os
import json
import numpy as np
import faiss
from pathlib import Path
from sentence_transformers import SentenceTransformer

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
INDEX_FILE = DATA_DIR / "faiss.index"
METADATA_FILE = DATA_DIR / "chunks_metadata.json"  # <-- НОВОЕ: хранит привязку к книгам
TRAINED_MODEL_PATH = MODELS_DIR / "argus-embeddings"

DATA_DIR.mkdir(parents=True, exist_ok=True)

if not KNOWLEDGE_FILE.exists():
    print("❌ knowledge.json не найден. Сначала запусти ingest.py")
    exit(1)

print(f"📚 Загрузка знаний из: {KNOWLEDGE_FILE}")
with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
    knowledge = json.load(f)

# --- Подготовка данных ---
texts = []
metadata = []

for c in knowledge.get("chunks", []):
    text = " ".join(c.get("text", "").split()) # нормализация пробелов
    if len(text) < 100:  # пропускаем мусорные короткие куски
        continue
    
    texts.append(text)
    metadata.append({
        "id": c.get("id", f"chunk_{len(metadata)}"),
        "source": c.get("source", c.get("book", "unknown")),
        "book": c.get("book", "unknown"),
        "chunk_index": c.get("chunk_index", 0)
    })

print(f"✅ Подготовлено {len(texts)} чанков для индексации")

# --- Загрузка модели ---
# Сначала пытаемся загрузить ту, которую мы только что обучили
if TRAINED_MODEL_PATH.exists() and (TRAINED_MODEL_PATH / "config.json").exists():
    model_path = str(TRAINED_MODEL_PATH)
    print(f"🧠 Загружаю ОБУЧЕННУЮ модель: {model_path}")
else:
    # Фоллбэк на базовую, если обученной ещё нет (первый запуск)
    model_path = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    print(f"⚠️ Обученная модель не найдена, использую базовую: {model_path}")

model = SentenceTransformer(model_path)

# --- Создание эмбеддингов ---
print("🧠 Векторизация чанков...")
embeddings = model.encode(
    texts,
    normalize_embeddings=True,   # Обязательно для IndexFlatIP (Cosine Similarity)
    show_progress_bar=True,
    batch_size=32
)
embeddings = np.array(embeddings).astype("float32")

# --- Построение FAISS индекса ---
dimension = embeddings.shape[1]
print(f"📐 Размерность вектора: {dimension}")

# IndexFlatIP (Inner Product) на нормализованных векторах = Cosine Similarity
index = faiss.IndexFlatIP(dimension)
index.add(embeddings)

# --- Сохранение ---
faiss.write_index(index, str(INDEX_FILE))
print(f"✅ FAISS индекс сохранён: {INDEX_FILE} ({index.ntotal} векторов)")

with open(METADATA_FILE, "w", encoding="utf-8") as f:
    json.dump(metadata, f, ensure_ascii=False, indent=2)
print(f"✅ Метаданные чанков сохранены: {METADATA_FILE}")

print("🎉 Индекс успешно построен!")
