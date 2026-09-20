# ============================================================
# ARGUS — INGEST (v7.2)
# v7.2: pathlib, все файлы помечаются обработанными (fix zombie),
#       формат chunks: {id, source, book, chunk_index, text}
# ============================================================

import re
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import fitz

# --- Пути (pathlib, как в GUIDE) ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

BOOKS_DIR = REPO_ROOT / "books"
DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
LAST_INGEST_FILE = DATA_DIR / "last_ingest.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


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
    if len(chunk) < 200:
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
# ЗАГРУЗКА СУЩЕСТВУЮЩИХ ЗНАНИЙ (merge)
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

# Множество обработанных file_hash
processed_hashes = {b.get("file_hash") for b in knowledge.get("books", [])
                    if b.get("file_hash")}

# Глобальный дедуп по хэшу текста чанка
existing_chunk_hashes = {md5_text(c.get("text", ""))
                         for c in knowledge.get("chunks", [])}


# ============================================================
# ОБРАБОТКА КНИГ
# ============================================================
new_files_ingested = []
total_new_chunks = 0

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

            # Помечаем обработанным ВСЕГДА, если парсинг успешен
            new_files_ingested.append(filename)

            print(f"   ✅ Добавлено: {added}, дублей пропущено: {skipped_dup}")

        except Exception as e:
            print(f"   ❌ Ошибка парсинга {filename}: {e}")
            # Файл НЕ в new_files_ingested — не удалим


# ============================================================
# СОХРАНЕНИЕ KNOWLEDGE
# ============================================================
with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
    json.dump(knowledge, f, ensure_ascii=False, indent=2)


# ============================================================
# LAST INGEST
# ============================================================
with open(LAST_INGEST_FILE, "w", encoding="utf-8") as f:
    json.dump({
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "files": new_files_ingested,
        "new_chunks": total_new_chunks,
    }, f, ensure_ascii=False, indent=2)


# ============================================================
# SUMMARY
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