# ============================================================
# ARGUS — ЧИТАТЕЛЬ КНИГ (v6)
# v6: + TXT/MD, дедуп чанков, глобальный chunk_id, защита от битого JSON
# ============================================================

import os
import re
import json
import hashlib
from datetime import datetime, timezone
import fitz

# --- Пути от корня репо ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

BOOKS_DIR = os.path.join(REPO_ROOT, "books")
DATA_DIR = os.path.join(REPO_ROOT, "data")
KNOWLEDGE_FILE = os.path.join(DATA_DIR, "knowledge.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.json")

os.makedirs(DATA_DIR, exist_ok=True)

# --- Загрузка существующих знаний (с защитой от битого файла) ---
knowledge = {"books": [], "chunks": []}
if os.path.exists(KNOWLEDGE_FILE):
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "chunks" in data:
            knowledge = data
        else:
            print("️ knowledge.json битый — начинаю с нуля")
    except Exception as e:
        print(f"⚠️ Не удалось прочитать knowledge.json: {e} — начинаю с нуля")

processed = {b["file"] for b in knowledge.get("books", []) if "file" in b}

# --- Дедупликация уже имеющихся чанков по хэшу ---
existing_hashes = set()
for c in knowledge.get("chunks", []):
    h = hashlib.md5(c.get("text", "").encode("utf-8")).hexdigest()
    existing_hashes.add(h)


# ============================================================
# ОЧИСТКА
# ============================================================
def clean_text(text):
    # Убираем разрывы слов (OCR-артефакты)
    text = re.sub(r"\b(\w)\s(?=\w\b)", r"\1", text)
    # Схлопываем повторы символов (-----, =====)
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    # Нормализуем пробелы и переносы
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
            current = (current + " " + sentence) if current else sentence
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
    letters = sum(1 for c in chunk if c.isalpha())
    return letters / len(chunk) > 0.5


# ============================================================
# ПАРСЕРЫ
# ============================================================
def parse_pdf(filepath):
    doc = fitz.open(filepath)
    pages = len(doc)
    text = ""
    for page in doc:
        text += page.get_text() + "\n"
    doc.close()
    return text, pages


def parse_txt(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(), 1


def parse_md(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    # Убираем markdown-разметку: заголовки, ссылки, жирный/курсив
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)
    return text, 1


PARSERS = {
    ".pdf": parse_pdf,
    ".txt": parse_txt,
    ".md": parse_md,
    ".markdown": parse_md,
}


# ============================================================
# ОБРАБОТКА КНИГ
# ============================================================
if not os.path.isdir(BOOKS_DIR):
    print("⚠️ Нет папки books/")
else:
    for filename in sorted(os.listdir(BOOKS_DIR)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in PARSERS:
            continue

        if filename in processed:
            print(f"⏭ Уже обработана: {filename}")
            continue

        filepath = os.path.join(BOOKS_DIR, filename)
        print(f"📖 Обработка: {filename}")

        try:
            text, pages = PARSERS[ext](filepath)

            if len(text.strip()) < 100:
                print("   ⚠️ Мало текста")
                continue

            cleaned = clean_text(text)
            print(f"   Очищено: {len(text)} -> {len(cleaned)} символов")

            chunks = split_into_chunks(cleaned)
            good_chunks = [c for c in chunks if is_good_chunk(c)]
            print(f"   Чанков: {len(chunks)} -> {len(good_chunks)} после фильтра")

            added = 0
            skipped_dup = 0
            for idx, chunk in enumerate(good_chunks):
                h = hashlib.md5(chunk.encode("utf-8")).hexdigest()
                if h in existing_hashes:
                    skipped_dup += 1
                    continue
                existing_hashes.add(h)

                knowledge["chunks"].append({
                    "id": f"{os.path.splitext(filename)[0]}#{idx:05d}",
                    "source": os.path.splitext(filename)[0],
                    "book": filename,
                    "chunk_index": idx,
                    "text": chunk,
                })
                added += 1

            if added > 0 or skipped_dup > 0:
                knowledge["books"].append({
                    "file": filename,
                    "pages": pages,
                    "chunks": added,
                    "skipped_duplicates": skipped_dup,
                })

            print(f"   ✅ Добавлено: {added}, пропущено дублей: {skipped_dup}")

        except Exception as e:
            print(f"   ❌ Ошибка: {e}")


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
    "generated_at": datetime.now(timezone.utc).isoformat(),
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
print(f"   Книг: {len(knowledge['books'])}")
print(f"   Чанков: {len(knowledge['chunks'])}")
print("   summary.json: создан")
