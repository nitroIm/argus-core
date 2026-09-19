# ============================================================
# ARGUS — ЧИТАТЕЛЬ КНИГ (v5)
# v5: + создание summary.json для /stats
# ============================================================

import os
import re
import json
from datetime import datetime
import fitz

# --- Пути от корня репо ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

BOOKS_DIR = os.path.join(REPO_ROOT, "books")
DATA_DIR = os.path.join(REPO_ROOT, "data")
KNOWLEDGE_FILE = os.path.join(DATA_DIR, "knowledge.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.json")

os.makedirs(DATA_DIR, exist_ok=True)

if os.path.exists(KNOWLEDGE_FILE):
    with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
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
    text = re.sub(r"\b(\w)\s(?=\w\b)", r"\1", text)
    text = re.sub(r"\b(\w)\s(?=\w\b)", r"\1", text)
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ============================================================
# РАЗБИВКА
# ============================================================
def split_into_chunks(text, target=900, min_size=600):
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if len(current) + len(sentence) + 1 <= target:
            if current:
                current = current + " " + sentence
            else:
                current = sentence
        else:
            if len(current) >= min_size:
                chunks.append(current)
            elif chunks:
                chunks[-1] = chunks[-1] + " " + current
            elif current:
                chunks.append(current)

            current = sentence

    if len(current) >= min_size:
        chunks.append(current)
    elif chunks and current:
        chunks[-1] = chunks[-1] + " " + current
    elif current:
        chunks.append(current)

    return chunks


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
# ОБРАБОТКА КНИГ
# ============================================================
if not os.path.isdir(BOOKS_DIR):
    print("⚠️ Нет папки books/")
else:
    for filename in os.listdir(BOOKS_DIR):
        if not filename.lower().endswith(".pdf"):
            continue

        if filename in processed:
            print("⏭ Уже обработана: " + filename)
            continue

        filepath = os.path.join(BOOKS_DIR, filename)
        print("📖 Обработка: " + filename)

        try:
            doc = fitz.open(filepath)
            pages_count = len(doc)

            full_text = ""
            for page in doc:
                full_text = full_text + page.get_text() + "\n"

            doc.close()

            if len(full_text.strip()) < 100:
                print("   ⚠️ Мало текста")
                continue

            cleaned = clean_text(full_text)
            print("   Очищено: " + str(len(full_text)) + " -> " + str(len(cleaned)) + " символов")

            chunks = split_into_chunks(cleaned)
            good_chunks = []
            for c in chunks:
                if is_good_chunk(c):
                    good_chunks.append(c)

            print("   Чанков: " + str(len(chunks)) + " -> " + str(len(good_chunks)) + " после фильтра")

            for idx, chunk in enumerate(good_chunks):
                knowledge["chunks"].append({
                    "book": filename,
                    "chunk_id": idx,
                    "text": chunk,
                })

            knowledge["books"].append({
                "file": filename,
                "pages": pages_count,
                "chunks": len(good_chunks),
            })

            print("   ✅ Готово")

        except Exception as e:
            print("   ❌ Ошибка: " + str(e))


# ============================================================
# СОХРАНЕНИЕ KNOWLEDGE
# ============================================================
with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)


# ============================================================
# СОЗДАНИЕ SUMMARY (для /stats)
# ============================================================
books_list = []
for b in knowledge.get("books", []):
    books_list.append({
        "file": b.get("file", "?"),
        "pages": b.get("pages", 0),
        "chunks": b.get("chunks", 0),
    })

summary = {
    "generated_at": datetime.utcnow().isoformat(),
    "total_books": len(books_list),
    "total_chunks": len(knowledge.get("chunks", [])),
    "books": books_list,
}

with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)


# ============================================================
# ИТОГ
# ============================================================
print("")
print("🎉 ARGUS обновил знания.")
print("   Книг: " + str(len(knowledge["books"])))
print("   Чанков: " + str(len(knowledge["chunks"])))
print("   summary.json: создан")