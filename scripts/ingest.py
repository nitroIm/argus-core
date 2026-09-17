# ============================================================
# ARGUS — ЧИТАТЕЛЬ КНИГ (v4)
# ============================================================

import os
import re
import json
import fitz

BOOKS_DIR = "books"
DATA_DIR = "data"
OUTPUT_FILE = os.path.join(DATA_DIR, "knowledge.json")

if os.path.exists(OUTPUT_FILE):
    with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
        knowledge = json.load(f)
else:
    knowledge = {"books": [], "chunks": []}

processed = []
for book in knowledge["books"]:
    processed.append(book["file"])


# ============================================================
# ОЧИСТКА
# ============================================================
def clean_text(text):
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ============================================================
# РАЗБИВКА — НАКОПИТЕЛЬНЫЙ БУФЕР
# ============================================================
def split_into_chunks(text, target=900, min_size=600):
    # Единый поток: убираем переносы, клеим по предложениям
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    # Режем на предложения
    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        # Если добавление не превысит target — копим
        if len(current) + len(sentence) + 1 <= target:
            if current:
                current = current + " " + sentence
            else:
                current = sentence
        else:
            # Буфер полон — сохраняем, если достаточно большой
            if len(current) >= min_size:
                chunks.append(current)
            elif chunks:
                # Маленький хвост — клеим к предыдущему
                chunks[-1] = chunks[-1] + " " + current
            elif current:
                chunks.append(current)

            current = sentence

    # Последний буфер
    if len(current) >= min_size:
        chunks.append(current)
    elif chunks and current:
        chunks[-1] = chunks[-1] + " " + current
    elif current:
        chunks.append(current)

    return chunks


# ============================================================
# ПРОВЕРКА
# ============================================================
def is_good_chunk(chunk):
    if len(chunk) < 200:
        return False

    letters = 0
    for c in chunk:
        if c.isalpha():
            letters = letters + 1

    ratio = letters / len(chunk)
    return ratio > 0.5


# ============================================================
# ОБРАБОТКА
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
        doc = fitz.open(filepath)
        pages_count = len(doc)

        full_text = ""
        for page in doc:
            full_text = full_text + page.get_text() + "\n"

        doc.close()

        if len(full_text.strip()) < 100:
            print(f"   ⚠️ Мало текста")
            continue

        cleaned = clean_text(full_text)
        print(f"   Очищено: {len(full_text)} → {len(cleaned)} символов")

        chunks = split_into_chunks(cleaned)
        good_chunks = []
        for c in chunks:
            if is_good_chunk(c):
                good_chunks.append(c)

        print(f"   Чанков: {len(chunks)} → {len(good_chunks)} после фильтра")

        for idx, chunk in enumerate(good_chunks):
            knowledge["chunks"].append({
                "book": filename,
                "chunk_id": idx,
                "text": chunk
            })

        knowledge["books"].append({
            "file": filename,
            "pages": pages_count,
            "chunks": len(good_chunks)
        })

        print(f"   ✅ Готово")

    except Exception as e:
        print(f"   ❌ Ошибка: {e}")


os.makedirs(DATA_DIR, exist_ok=True)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)

print(f"\n🎉 ARGUS обновил знания.")
print(f"   Книг: {len(knowledge['books'])}")
print(f"   Чанков: {len(knowledge['chunks'])}")