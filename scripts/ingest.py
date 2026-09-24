# ============================================================
# ARGUS — INGEST v7.5 [PRODUCTION]
# ------------------------------------------------------------
# v7.5: + защита от мусора (HTML, entity, bad_chars, long_word)
#       + дедуп по filename (не только по file_hash)
#       + чанки < 200 символов после чистки — не добавляются
# ------------------------------------------------------------
# v7.4: файлы в processed_hashes помечаются для cleanup
# v7.3: pathlib, file_hash, stable chunk_id, last_ingest.json
# ============================================================

import re
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import fitz

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

BOOKS_DIR = REPO_ROOT / "books"
DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
LAST_INGEST_FILE = DATA_DIR / "last_ingest.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- Лимиты ---
MIN_CHUNK_LEN = 200
MAX_WORD_LEN = 30
MAX_BAD_RUN = 6

# --- HTML/entity ---
HTML_TAG_RE = re.compile(r"<[^>]{1,80}>")
ENTITY_RE = re.compile(r"&[a-z]{2,8};")
ENTITY_MAP = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&nbsp;": " ",
    "&#39;": "'",
    "&apos;": "'",
}
BAD_CHARS_RE = re.compile(
    r"([^\w\s])\1{%d,}" % (MAX_BAD_RUN - 1)
)
LONG_WORD_RE = re.compile(
    r"([A-Za-zА-Яа-яЁё]{%d,})" % MAX_WORD_LEN
)


# ============================================================
# УТИЛИТЫ
# ============================================================
def md5_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    """Чистит мусор ДО нарезки на чанки."""
    # HTML теги
    if HTML_TAG_RE.search(text):
        text = HTML_TAG_RE.sub(" ", text)
    # entity
    if ENTITY_RE.search(text):
        for k, v in ENTITY_MAP.items():
            text = text.replace(k, v)
    # long words (аааааааааа)
    if LONG_WORD_RE.search(text):
        text = LONG_WORD_RE.sub(
            lambda m: m.group(1)[:MAX_WORD_LEN],
            text,
        )
    # bad chars (=====, .....)
    if BAD_CHARS_RE.search(text):
        text = BAD_CHARS_RE.sub(r"\1", text)
    # старая логика
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_chunks(text: str, target: int = 900, min_size: int = 600):
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


def is_good_chunk(chunk: str) -> bool:
    if len(chunk) < MIN_CHUNK_LEN:
        return False
    letters = sum(1 for c in chunk if c.isalpha())
    return letters / len(chunk) > 0.5


# ============================================================
# ПАРСЕРЫ
# ============================================================
def parse_pdf(filepath: Path):
    doc = fitz.open(str(filepath))
    pages = len(doc)
    text = "".join(page.get_text() + "\n" for page in doc)
    doc.close()
    return text, pages


def parse_txt(filepath: Path):
    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
        return f.read(), 1


def parse_md(filepath: Path):
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
# ЗАГРУЗКА СУЩЕСТВУЮЩИХ ЗНАНИЙ
# ============================================================
knowledge = {"books": [], "chunks": []}
if KNOWLEDGE_FILE.exists():
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

# Дедуп по hash файла
processed_hashes = {b.get("file_hash") for b in knowledge.get("books", [])
                    if b.get("file_hash")}

# NEW v7.5: дедуп по filename (для старых записей без hash)
processed_names = {b.get("file") for b in knowledge.get("books", [])
                   if b.get("file")}

# Глобальный дедуп по хэшу текста чанка
existing_chunk_hashes = {md5_text(c.get("text", ""))
                         for c in knowledge.get("chunks", [])}


# ============================================================
# ОБРАБОТКА КНИГ
# ============================================================
new_files_ingested = []
total_new_chunks = 0
skipped_already_in_db = 0

if not BOOKS_DIR.is_dir():
    print("⚠️ Нет папки books/")
else:
    for filepath in sorted(BOOKS_DIR.iterdir()):
        if not filepath.is_file():
            continue
        ext = filepath.suffix.lower()
        if ext not in PARSERS:
            continue

        filename = filepath.name

        try:
            fhash = md5_file(filepath)
        except Exception as e:
            print(f"❌ Не могу прочитать {filename}: {e}")
            continue

        # NEW v7.5: дедуп по hash ИЛИ по filename
        if fhash in processed_hashes or filename in processed_names:
            print(f"⏭ Уже обработана: {filename} — cleanup")
            new_files_ingested.append(filename)
            skipped_already_in_db += 1
            continue

        print(f"📖 Обработка: {filename}")
        try:
            text, pages = PARSERS[ext](filepath)
            if len(text.strip()) < 100:
                print("   ⚠️ Слишком мало текста — пропуск")
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

                chunk_id = f"{fhash[:8]}#{idx:05d}"
                knowledge["chunks"].append({
                    "id": chunk_id,
                    "book": filename,
                    "source": filepath.stem,
                    "chunk_index": idx,
                    "text": chunk,
                })
                added += 1

            if added > 0:
                knowledge["books"].append({
                    "file": filename,
                    "file_hash": fhash,
                    "pages": pages,
                    "chunks_total": added,
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                })
                total_new_chunks += added
                processed_names.add(filename)  # NEW v7.5
                processed_hashes.add(fhash)

            new_files_ingested.append(filename)

            print(f"   ✅ Добавлено: {added}, дублей: {skipped_dup}")

        except Exception as e:
            print(f"   ❌ Ошибка парсинга {filename}: {e}")


# ============================================================
# СОХРАНЕНИЕ
# ============================================================
with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)

with open(LAST_INGEST_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "files": new_files_ingested,
        "new_chunks": total_new_chunks,
        "skipped_already_in_db": skipped_already_in_db,
    }, f, ensure_ascii=False, indent=2)

books_list = [{
    "file": b.get("file", "?"),
    "pages": b.get("pages", 0),
    "chunks": b.get("chunks_total", b.get("chunks", 0)),
} for b in knowledge.get("books", [])]

summary = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "total_books": len(books_list),
    "total_chunks": len(knowledge.get("chunks", [])),
    "new_chunks": total_new_chunks,
    "new_files": new_files_ingested,
    "skipped_already_in_db": skipped_already_in_db,
    "books": books_list,
}
with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print()
print("🎉 INGEST завершён.")
print(f"   Всего книг:   {len(knowledge['books'])}")
print(f"   Всего чанков: {len(knowledge['chunks'])}")
print(f"   Новых чанков: {total_new_chunks}")
print(f"   Файлов на cleanup: {len(new_files_ingested)}")
print(f"   Уже было в базе: {skipped_already_in_db}")