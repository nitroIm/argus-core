# ============================================================
# ARGUS — ПОИСК ПО СМЫСЛУ (v2)
# v2: pathlib, нормализация эмбеддингов, синхронизация с chunks_metadata.json, fallback модели
# ============================================================

import sys
import json
from pathlib import Path
import faiss
from sentence_transformers import SentenceTransformer

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
# ИСПРАВЛЕНО: читаем актуальные метаданные с названиями книг
META_FILE = DATA_DIR / "chunks_metadata.json"  

TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"

# --- 1. Получаем запрос ---
query = " ".join(sys.argv[1:]).strip() or "Что такое трейдинг?"

# --- 2. Загружаем модель (с проверкой) ---
if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
    model_path = str(TRAINED_MODEL)
    use_prefix = False
    print(f"🧠 Модель: Обученная ({model_path})")
else:
    model_path = BASE_MODEL
    use_prefix = True
    print(f"📦 Модель: Базовая ({model_path}) + префикс 'query: '")

model = SentenceTransformer(model_path)

# --- 3. Загружаем индекс и метаданные ---
try:
    index = faiss.read_index(str(INDEX_FILE))
    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)
    print(f"✅ Индекс загружен: {index.ntotal} векторов")
except Exception as e:
    print(f"❌ Ошибка загрузки данных: {e}")
    print("💡 Убедись, что ты сначала запустил build_index.py")
    sys.exit(1)

# --- 4. Кодируем вопрос ---
# КРИТИЧЕСКИ ВАЖНО: normalize_embeddings=True должен совпадать с build_index.py
text_to_encode = f"query: {query}" if use_prefix else query
query_vec = model.encode(
    [text_to_encode], 
    normalize_embeddings=True,  # <-- ИСПРАВЛЕНО
    show_progress_bar=False
).astype("float32")

# --- 5. Ищем топ-5 ---
distances, indices = index.search(query_vec, k=5)

# --- 6. Выводим красивый результат ---
print(f"\n🔍 Вопрос: '{query}'\n" + "=" * 60)

found_any = False
for i, idx in enumerate(indices[0]):
    if idx < 0 or idx >= len(meta):
        continue
    
    found_any = True
    m = meta[idx]
    score = distances[0][i]
    book_name = m.get("book", m.get("source", "Неизвестный источник"))
    text = m.get("text", "")
    
    print(f"📚 #{i+1} | Скор: {score:.3f} | Книга: {book_name}")
    print("-" * 60)
    # Выводим первые 400 символов текста
    print(text[:400] + ("..." if len(text) > 400 else ""))
    print("=" * 60)

if not found_any:
    print("⚠️ Ничего не найдено.")
