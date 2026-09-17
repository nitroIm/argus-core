# ============================================================
# ARGUS — ЧИТАТЕЛЬ КНИГ (v2)
# Читает PDF через pymupdf, чистит текст, режет по предложениям
# ============================================================

import os
import re
import json
import fitz   # pymupdf

# --- Пути ---
BOOKS_DIR = "books"
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "knowledge.json")

# --- Загружаем существующие знания ---
if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        knowledge = json.load(f)
else:
    knowledge = {"books": [], "chunks": []}

# --- Список уже обработанных файлов ---
processed = []
for book in knowledge["books"]:
    processed.append(book["file"])


# ============================================================
# ОЧИСТКА ТЕКСТА
# ============================================================
def clean_text(text):
    # Убираем повторяющиеся символы (C+C+C+, =====, -----, ++++)
    text = re.sub(r"(\S)\1{3,}", r"\1", text)

    # Убираем длинные цепочки символов без пробелов
    text = re.sub(r"[^\w\s]{4,}", " ", text)

    # Убираем одиночные символы через пробел (а б в г ...)
    text = re.sub(r"\b\w\b\s\b\w\b\s\b\w\b\s\b\w\b", " ", text)

    # Схлопываем пробелы
    text = re.sub(r"[ \t]+", " ", text)

    # Убираем пустые строки (больше 2 подряд)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# ============================================================
# РАЗБИВКА НА ПРЕДЛОЖЕНИЯ → ЧАНКИ
# ============================================================
def split_into_chunks(text, max_chars=800, min_chars=200):
    # Режем по предложениям: точка, !, ?, перенос строки
    sentences = re.split(r"(?<=[.!?])\s+|\n\n", text)

    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Если предложение короткое — добавляем к текущему
        if len(current) + len(sentence) < max_chars:
            current = current + " " + sentence
        else:
            # Сохраняем текущий чанк
            if len(current) >= min_chars:
                chunks.append(current.strip())
            # Начинаем новый
            current = sentence

    # Не забываем последний
    if len(current) >= min_chars:
        chunks.append(current.strip())

    return chunks


# ============================================================
# ПРОВЕРКА КАЧЕСТВА ЧАНКА
# ============================================================
def is_good_chunk(chunk):
    # Отбрасываем чанки, где мало букв (только мусор)
    letters = 0
    for char in chunk:
        if char.isalpha():
            letters = letters + 1

    if len(chunk) == 0:
        return False

    ratio = letters / len(chunk)
    return ratio > 0.6   # минимум 60% букв


# ============================================================
# ОБРАБОТКА ВСЕХ PDF
# ============================================================
for filename in os.listdir(BOOKS_DIR):
    if not filename.lower().endswith(".pdf"):
        continue

    if filename in processed:
        print(f"⏭ Уже обработана: {filename}")
        continue

    filepath = os.path.join(BOOKS_DIR, filename)
    print(f"📖 Обработка: {filename}")

    try:
        # --- Открываем PDF ---
        doc = fitz.open(filepath)
        full_text = ""

        for page in doc:
            full_text = full_text + page.get_text() + "\n"

        doc.close()

        if len(full_text.strip()) < 100:
            print(f"   ⚠️ Мало текста — возможно, скан")
            continue

        # --- Чистим текст ---
        cleaned = clean_text(full_text)
        print(f"   Очищено: {len(full_text)} → {len(cleaned)} символов")

        # --- Режем на чанки ---
        chunks = split_into_chunks(cleaned)

        # --- Фильтруем мусор ---
        good_chunks = []
        for chunk in chunks:
            if is_good_chunk(chunk):
                good_chunks.append(chunk)

        print(f"   Чанков: {len(chunks)} → {len(good_chunks)} после фильтра")

        # --- Сохраняем ---
        for idx, chunk in enumerate(good_chunks):
            knowledge["chunks"].append({
                "book": filename,
                "chunk_id": idx,
                "text": chunk
            })

        knowledge["books"].append({
            "file": filename,
            "pages": len(doc),
            "chunks": len(good_chunks)
        })

        print(f"   ✅ Готово")

    except Exception as e:
        print(f"   ❌ Ошибка: {e}")


# ============================================================
# СОХРАНЕНИЕ
# ============================================================
os.makedirs(DATA_DIR, exist_ok=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)

print(f"\n🎉 ARGUS обновил знания.")
print(f"   Книг: {len(knowledge['books'])}")
print(f"   Чанков: {len(knowledge['chunks'])}")