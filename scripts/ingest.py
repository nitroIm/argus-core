# ============================================================
# ARGUS — INGEST v7.6
# ------------------------------------------------------------
# v7.6: + рекурсия по подпапкам (books/trading/, books/crypto/)
#       + категория из пути (books/crypto/ → "crypto")
#       + автообновление book_categories.json
#       + логика удаления не меняется (в workflow)
# ============================================================

import re
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import fitz

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

BOOKS_DIR = REPO_ROOT / "books"
DATA_DIR = REPO_ROOT / "data"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
LAST_INGEST_FILE = DATA_DIR / "last_ingest.json"
CATEGORIES_FILE = DATA_DIR / "book_categories.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

MIN_CHUNK_LEN = 200
MAX_WORD_LEN = 30
MAX_BAD_RUN = 6

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
BAD_CHARS_RE = re.compile(r"([^\w\s])\1{%d,}" % (MAX_BAD_RUN - 1))
LONG_WORD_RE = re.compile(
    r"([A-Za-zА-Яа-яЁё]{%d,})" % MAX_WORD_LEN
)


def md5_file(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(
            lambda: f.read(chunk_size), b""
        ):
            h.update(chunk)
    return h.hexdigest()


def md5_text(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def clean_text(text: str) -> str:
    if HTML_TAG_RE.search(text):
        text = HTML_TAG_RE.sub(" ", text)
    if ENTITY_RE.search(text):
        for k, v in ENTITY_MAP.items():
            text = text.replace(k, v)
    if LONG_WORD_RE.search(text):
        text = LONG_WORD_RE.sub(
            lambda m: m.group(1)[:MAX_WORD_LEN], text,
        )
    if BAD_CHARS_RE.search(text):
        text = BAD_CHARS_RE.sub(r"\1", text)
    text = re.sub(r"(\S)\1{4,}", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_into_chunks(
    text, target=900, min_size=600,
):
    text = re.sub(r"\n+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", text)

    chunks = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        if (len(current) + len(sentence) + 1
                <= target):
            if current:
                current += " " + sentence
            else:
                current = sentence
        else:
            if len(current) >= min_size:
                chunks.append(current)
            elif chunks:
                chunks[-1] += " " + current
            elif current:
                chunks.append(current)
            current = sentence

    if len(current) >= min_size:
        chunks.append(current)
    elif chunks and current:
        chunks[-1] += " " + current
    elif current:
        chunks.append(current)
    return chunks


def is_good_chunk(chunk: str) -> bool:
    if len(chunk) < MIN_CHUNK_LEN:
        return False
    letters = sum(1 for c in chunk if c.isalpha())
    if len(chunk) == 0:
        return False
    return letters / len(chunk) > 0.5


def parse_pdf(filepath):
    doc = fitz.open(str(filepath))
    pages = len(doc)
    text = "".join(
        page.get_text() + "\n" for page in doc
    )
    doc.close()
    return text, pages


def parse_txt(filepath):
    with open(
        filepath, "r", encoding="utf-8",
        errors="ignore",
    ) as f:
        return f.read(), 1


def parse_md(filepath):
    with open(
        filepath, "r", encoding="utf-8",
        errors="ignore",
    ) as f:
        text = f.read()
    text = re.sub(r"#{1,6}\s+", "", text)
    text = re.sub(
        r"\[([^\]]+)\]\([^\)]+\)", r"\1", text
    )
    text = re.sub(
        r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text
    )
    text = re.sub(r"`{1,3}[^`]*`{1,3}", "", text)
    return text, 1


PARSERS = {
    ".pdf": parse_pdf,
    ".txt": parse_txt,
    ".md": parse_md,
    ".markdown": parse_md,
}


# ============================================================
# CATEGORY FROM PATH
# ============================================================
KNOWN_CATEGORIES = {
    "trading", "crypto", "quant", "psychology",
    "philosophy", "physics", "misc", "offtopic",
}


def get_category(filepath: Path) -> str:
    try:
        rel = filepath.relative_to(BOOKS_DIR)
    except ValueError:
        return "misc"
    parts = rel.parts
    if len(parts) >= 2:
        cat = parts[0].lower()
        if cat in KNOWN_CATEGORIES:
            return cat
        return "misc"
    return "misc"


# ============================================================
# LOAD EXISTING KNOWLEDGE
# ============================================================
knowledge = {"books": [], "chunks": []}
if KNOWLEDGE_FILE.exists():
    try:
        with open(
            KNOWLEDGE_FILE, "r", encoding="utf-8"
        ) as f:
            data = json.load(f)
        if isinstance(data, dict) and "chunks" in data:
            knowledge = data
            print(
                f"Loaded: {len(knowledge['books'])} books, "
                f"{len(knowledge['chunks'])} chunks"
            )
        else:
            print("Warning: knowledge.json broken")
    except Exception as e:
        print(f"Warning: cannot read: {e}")

processed_hashes = {
    b.get("file_hash")
    for b in knowledge.get("books", [])
    if b.get("file_hash")
}
processed_names = {
    b.get("file")
    for b in knowledge.get("books", [])
    if b.get("file")
}
existing_chunk_hashes = {
    md5_text(c.get("text", ""))
    for c in knowledge.get("chunks", [])
}


# ============================================================
# CATEGORIES
# ============================================================
categories = {}
if CATEGORIES_FILE.exists():
    try:
        with open(
            CATEGORIES_FILE, "r", encoding="utf-8"
        ) as f:
            categories = json.load(f)
        print(
            f"Categories loaded: {len(categories)}"
        )
    except Exception as e:
        print(f"Warning: book_categories.json: {e}")


# ============================================================
# PROCESS BOOKS — RECURSIVELY
# ============================================================
new_files_ingested = []
total_new_chunks = 0
skipped_already_in_db = 0

if not BOOKS_DIR.is_dir():
    print("Warning: no books/ directory")
else:
    all_files = sorted(
        p for p in BOOKS_DIR.rglob("*")
        if p.is_file() and not p.name.startswith(".")
    )
    print(
        f"Found files (recursive): {len(all_files)}"
    )

    for filepath in all_files:
        ext = filepath.suffix.lower()
        if ext not in PARSERS:
            continue

        filename = filepath.name
        category = get_category(filepath)

        try:
            fhash = md5_file(filepath)
        except Exception as e:
            print(f"Error reading {filename}: {e}")
            continue

        if (fhash in processed_hashes
                or filename in processed_names):
            print(f"Skipped: {filename}")
            categories[filename] = category
            new_files_ingested.append(filename)
            skipped_already_in_db += 1
            continue

        print(f"Processing: {filename} [{category}]")
        try:
            text, pages = PARSERS[ext](filepath)
            if len(text.strip()) < 100:
                print("   Warning: too little text")
                continue

            cleaned = clean_text(text)
            chunks = split_into_chunks(cleaned)
            good_chunks = [
                c for c in chunks if is_good_chunk(c)
            ]

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
                    "category": category,
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
                    "category": category,
                    "processed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                })
                total_new_chunks += added
                processed_names.add(filename)
                processed_hashes.add(fhash)
                categories[filename] = category

            new_files_ingested.append(filename)
            print(
                f"   Added: {added}, dups: {skipped_dup}"
            )

        except Exception as e:
            print(f"   Error parsing: {e}")


# ============================================================
# SAVE
# ============================================================
with open(
    KNOWLEDGE_FILE, "w", encoding="utf-8"
) as f:
    json.dump(
        knowledge, f, ensure_ascii=False,
        indent=2,
    )

with open(
    CATEGORIES_FILE, "w", encoding="utf-8"
) as f:
    json.dump(
        categories, f, ensure_ascii=False,
        indent=2,
    )
print(f"Saved book_categories.json: {len(categories)}")

with open(
    LAST_INGEST_FILE, "w", encoding="utf-8"
) as f:
    json.dump({
        "ingested_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "files": new_files_ingested,
        "new_chunks": total_new_chunks,
        "skipped_already_in_db": skipped_already_in_db,
    }, f, ensure_ascii=False, indent=2)

books_list = [{
    "file": b.get("file", "?"),
    "pages": b.get("pages", 0),
    "chunks": b.get(
        "chunks_total", b.get("chunks", 0)
    ),
    "category": b.get("category", "misc"),
} for b in knowledge.get("books", [])]

summary = {
    "generated_at": datetime.now(
        timezone.utc
    ).isoformat(),
    "total_books": len(books_list),
    "total_chunks": len(knowledge.get("chunks", [])),
    "new_chunks": total_new_chunks,
    "new_files": new_files_ingested,
    "skipped_already_in_db": skipped_already_in_db,
    "books": books_list,
}
with open(
    SUMMARY_FILE, "w", encoding="utf-8"
) as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)

print()
print("INGEST completed.")
print(f"   Total books:   {len(knowledge['books'])}")
print(f"   Total chunks:  {len(knowledge['chunks'])}")
print(f"   New chunks:    {total_new_chunks}")
print(f"   Cleanup files: {len(new_files_ingested)}")
print(f"   Already had:   {skipped_already_in_db}")