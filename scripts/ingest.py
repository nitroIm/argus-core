# ============================================================
# ARGUS — INGEST (v7.1)
# v7.1: fix — все успешно распарсенные файлы помечаются обработанными
#       (даже если дали 0 новых чанков), чтобы cleanup их удалял.
# ============================================================

import os
import re
import json
import hashlib
from datetime import datetime, timezone
import fitz

# --- Пути ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)

BOOKS_DIR = os.path.join(REPO_ROOT, "books")
DATA_DIR = os.path.join(REPO_ROOT, "data")
KNOWLEDGE_FILE = os.path.join(DATA_DIR, "knowledge.json")
SUMMARY_FILE = os.path.join(DATA_DIR, "summary.json")
LAST_INGEST_FILE = os.path.join(DATA_DIR, "last_ingest.json")

os.makedirs(DATA_DIR, exist_ok=True)


# ============================================================
# УТИЛИТЫ
# ============================================================
def md5_file(path, chunk_size=1 << 20):
    """MD5 содержимого файла. Для больших PDF быстрее, чем читать целиком."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_text(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def clean_text(text):
    # Убираем OCR-артефакты типа "-----" и "====="
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    # Нормализуем пробелы и переносы
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
    text = "".join(page.get_text() + "\n" for page in doc)
    doc.close()
    return text, pages


def parse_txt(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(), 1


def parse_md(filepath):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
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
# ЗАГРУЗКА СУЩЕСТВУЮЩИХ ЗНАНИЙ (merge, не перезапись)
# ============================================================
knowledge = {"books": [], "chunks": []}
if os.path.exists(KNOWLEDGE_FILE):
    try:
        with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "chunks" in data:
            knowledge = data
            print(f"📚 Загружено: {len(knowledge['books'])} книг, "
                  f"{len(knowledge['chunks'])} чанков")
        else:
            print("⚠️ knowledge.json битый — начинаю с нуля")
    except Exception as e:
        print(f"⚠️ Не удалось прочитать knowledge.json: {e} — начинаю с нуля")

# Множество уже обработанных file_hash (не имён!)
processed_hashes = {b.get("file_hash") for b in knowledge.get("books", [])
                    if b.get("file_hash")}

# Множество хэшей текстов — глобальный дедуп
existing_chunk_hashes = {md5_text(c.get("text", ""))
                         for c in knowledge.get("chunks", [])}


# ============================================================
# ОБРАБОТКА КНИГ
# ============================================================
new_files_ingested = []  # для workflow — что удалять
total_new_chunks = 0

if not os.path.isdir(BOOKS_DIR):
    print("⚠️ Нет папки books/")
else:
    for filename in sorted(os.listdir(BOOKS_DIR)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in PARSERS:
            continue

        filepath = os.path.join(BOOKS_DIR, filename)
        if not os.path.isfile(filepath):
            continue

        try:
            fhash = md5_file(filepath)
        except Exception as e:
            print(f"❌ Не могу прочитать {filename}: {e}")
            continue

        if fhash in processed_hashes:
            print(f"⏭ Уже обработана (hash): {filename}")
            continue

        print(f"📖 Обработка: {filename}")
        try:
            text, pages = PARSERS[ext](filepath)
            if len(text.strip()) < 100:
                print("   ⚠️ Слишком мало текста — пропуск (файл НЕ удалим)")
                continue

            cleaned = clean_text(text)
            chunks = split_into_chunks(cleaned)
            good_chunks = [c for c in chunks if is_good_chunk(c)]

            added = 0
            skipped_dup = 0
            for idx, chunk in enumerate(good_chunks):
                ch = md5_text(chunk)
                if ch in existing_chunk_hashes:
                    skipped_dup += 1
                    continue
                existing_chunk_hashes.add(ch)

                # Стабильный уникальный chunk_id
                chunk_id = f"{fhash[:8]}#{idx:05d}"
                knowledge["chunks"].append({
                    "id": chunk_id,
                    "book": filename,
                    "source": os.path.splitext(filename)[0],
                    "chunk_index": idx,
                    "text": chunk,
                })
                added += 1

            # Запись книги — только если реально что-то попало в базу
            if added > 0:
                knowledge["books"].append({
                    "file": filename,
                    "file_hash": fhash,
                    "pages": pages,
                    "chunks_total": added,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                })
                total_new_chunks += added

            # FIX v7.1: файл помечаем обработанным ВСЕГДА,
            # если он успешно распарсился (даже если 0 новых чанков).
            # Это чтобы cleanup удалял и книги-дубли (иначе висят зомби).
            new_files_ingested.append(filename)

            print(f"   ✅ Добавлено: {added}, дублей пропущено: {skipped_dup}")

        except Exception as e:
            print(f"   ❌ Ошибка парсинга {filename}: {e}")
            # ВАЖНО: файл НЕ добавляется в new_files_ingested — не удалим,
            # чтобы пользователь мог починить/перезалить


# ============================================================
# СОХРАНЕНИЕ KNOWLEDGE
# ============================================================
with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)


# ============================================================
# LAST INGEST — для безопасного удаления PDF в workflow
# ============================================================
with open(LAST_INGEST_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "files": new_files_ingested,
        "new_chunks": total_new_chunks,
    }, f, ensure_ascii=False, indent=2)


# ============================================================
# SUMMARY (для Telegram /stats)
# ============================================================
books_list = [{
    "file": b.get("file", "?"),
    "pages": b.get("pages", 0),
    "chunks": b.get("chunks_total", 0),
} for b in knowledge.get("books", [])]

summary = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "total_books": len(books_list),
    "total_chunks": len(knowledge.get("chunks", [])),
    "new_chunks": total_new_chunks,
    "new_files": new_files_ingested,
    "books": books_list,
}
with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)


# ============================================================
# ИТОГ
# ============================================================
print()
print("🎉 INGEST завершён.")
print(f"   Всего книг:   {len(knowledge['books'])}")
print(f"   Всего чанков: {len(knowledge['chunks'])}")
print(f"   Новых чанков: {total_new_chunks}")
print(f"   Новых файлов: {len(new_files_ingested)}")