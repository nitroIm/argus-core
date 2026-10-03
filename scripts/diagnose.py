# ============================================================
# ARGUS — DIAGNOSE
# ------------------------------------------------------------
# Read-only: показывает, какие книги есть в knowledge.json
# и какие — в chunks_for_index.json. Без ML.
# ============================================================

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
METADATA_FILE = DATA_DIR / "chunks_for_index.json"
INDEX_FILE = DATA_DIR / "faiss.index"


def safe_load(path):
    if not path.exists():
        return None, "missing"
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f), "ok"
    except Exception as e:
        return None, f"error: {e}"


def count_by_book(items):
    d = {}
    for it in items:
        if isinstance(it, dict):
            b = it.get("book") or it.get("source") or "?"
        elif isinstance(it, str):
            b = "(legacy string)"
        else:
            b = "(unknown)"
        d[b] = d.get(b, 0) + 1
    return d


def main():
    print("=" * 55)
    print("ARGUS DIAGNOSE")
    print("=" * 55)

    # --- knowledge.json ---
    kn, status = safe_load(KNOWLEDGE_FILE)
    if status != "ok":
        print(f"knowledge.json: {status}")
        kn_chunks = []
    else:
        kn_chunks = kn.get("chunks", [])
        print(f"knowledge.json: OK")
        print(f"  books array: {len(kn.get('books', []))}")
        print(f"  chunks array: {len(kn_chunks)}")

    kn_books = count_by_book(kn_chunks)
    print(f"  unique books in chunks: {len(kn_books)}")

    # --- chunks_for_index.json ---
    meta, status = safe_load(METADATA_FILE)
    if status != "ok":
        print(f"chunks_for_index.json: {status}")
        meta = []
    else:
        print(f"chunks_for_index.json: OK")
        print(f"  items: {len(meta)}")
        if meta and isinstance(meta[0], str):
            print(f"  format: LEGACY (list of strings)")
        elif meta and isinstance(meta[0], dict):
            print(f"  format: dict list")

    meta_books = count_by_book(meta)
    print(f"  unique books: {len(meta_books)}")

    # --- faiss.index ---
    if INDEX_FILE.exists():
        size_kb = INDEX_FILE.stat().st_size / 1024
        print(f"faiss.index: OK ({size_kb:.0f} KB)")
    else:
        print(f"faiss.index: missing")

    print()
    print("=" * 55)
    print("MISSING IN chunks_for_index.json")
    print("=" * 55)
    missing = set(kn_books.keys()) - set(meta_books.keys())
    if not missing:
        print("(none — все книги из knowledge есть в meta)")
    for b in sorted(missing):
        print(f"  {b}: {kn_books[b]} chunks")

    print()
    print("=" * 55)
    print("ONLY IN chunks_for_index.json")
    print("=" * 55)
    only_meta = set(meta_books.keys()) - set(kn_books.keys())
    if not only_meta:
        print("(none)")
    for b in sorted(only_meta):
        print(f"  {b}: {meta_books[b]} chunks")

    print()
    print("=" * 55)
    print("ALL BOOKS IN knowledge.json")
    print("=" * 55)
    for b, n in sorted(kn_books.items(), key=lambda x: -x[1]):
        print(f"  {b}: {n}")

    print()
    print("=" * 55)
    print("ALL BOOKS IN chunks_for_index.json")
    print("=" * 55)
    for b, n in sorted(meta_books.items(), key=lambda x: -x[1]):
        print(f"  {b}: {n}")


if __name__ == "__main__":
    main()