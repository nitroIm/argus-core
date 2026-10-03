# ============================================================
# ARGUS — FIX CATEGORIES
# ------------------------------------------------------------
# One-time script: fix category of .md guides that were
# ingested by old ingest.py (no subfolder support) and got
# "misc" instead of "trading".
# ============================================================

import json
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
CATEGORIES_FILE = DATA_DIR / "book_categories.json"

# Files that must be "trading"
FIX_TO_TRADING = [
    "01_rsi.md",
    "02_macd.md",
    "03_bollinger.md",
    "04_atr.md",
    "05_volume.md",
    "06_candles.md",
    "07_risk.md",
]


def log(msg):
    print(f"[FIX] {msg}", flush=True)


def main():
    if not KNOWLEDGE_FILE.exists():
        log("knowledge.json not found")
        return

    with open(KNOWLEDGE_FILE, "r",
              encoding="utf-8") as f:
        kn = json.load(f)

    # --- Fix books array in knowledge.json ---
    books = kn.get("books", [])
    books_fixed = 0
    for b in books:
        fname = b.get("file", "")
        if fname in FIX_TO_TRADING:
            old = b.get("category", "?")
            if old != "trading":
                b["category"] = "trading"
                books_fixed += 1
                log(f"Book: {fname} {old} -> trading")

    # --- Fix chunks array in knowledge.json ---
    chunks = kn.get("chunks", [])
    chunks_fixed = 0
    for c in chunks:
        fname = c.get("book", "")
        if fname in FIX_TO_TRADING:
            old = c.get("category", "?")
            if old != "trading":
                c["category"] = "trading"
                chunks_fixed += 1

    with open(KNOWLEDGE_FILE, "w",
              encoding="utf-8") as f:
        json.dump(kn, f, ensure_ascii=False,
                  indent=2)

    log(f"knowledge.json: books fixed "
        f"{books_fixed}, chunks fixed {chunks_fixed}")

    # --- Fix book_categories.json ---
    cats = {}
    if CATEGORIES_FILE.exists():
        with open(CATEGORIES_FILE, "r",
                  encoding="utf-8") as f:
            cats = json.load(f)

    cats_fixed = 0
    for fname in FIX_TO_TRADING:
        if fname in cats and cats[fname] != "trading":
            old = cats[fname]
            cats[fname] = "trading"
            cats_fixed += 1
            log(f"Categories: {fname} {old} -> trading")

    with open(CATEGORIES_FILE, "w",
              encoding="utf-8") as f:
        json.dump(cats, f, ensure_ascii=False,
                  indent=2)

    log(f"book_categories.json fixed: {cats_fixed}")
    log("DONE.")


if __name__ == "__main__":
    main()