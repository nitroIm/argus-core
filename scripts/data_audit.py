# ============================================================
# ARGUS - DATA AUDIT v2 [AUDIT ONLY]
# ------------------------------------------------------------
# Только проверка. Ничего не пишет, не удаляет.
# Цель: собрать полную картину проблем.
# Фиксы будут добавлены отдельно (v3).
# ------------------------------------------------------------
# v2 vs v1:
#   - knowledge.json читается как dict
#   - faiss.index: data/faiss.index
#   - cross-check knowledge vs chunks
#   - больше деталей по каждой проверке
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import re
import json
import hashlib
import logging
import requests
from pathlib import Path
from collections import Counter
from datetime import datetime, timezone

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
BOOKS_DIR = REPO_ROOT / "books"

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
CHUNKS_FILE = DATA_DIR / "chunks_for_index.json"
FAISS_FILE = DATA_DIR / "faiss.index"

# --- Логгер ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("data_audit")

# --- Telegram ---
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

# --- Лимиты ---
MIN_CHUNK_LEN = 20
MAX_CHUNK_LEN = 5000
MAX_BAD_CHARS_RUN = 6
MAX_WORD_LEN = 30
MAX_MSG_LEN = 3800


# ============================================================
# HELPERS
# ============================================================
def load_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("load %s: %s", path.name, e)
        return default


def md5(text):
    return hashlib.md5(
        text.encode("utf-8")
    ).hexdigest()


def esc(text):
    """Экранирует HTML для Telegram."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def split_text(text, max_len):
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current)
            current = line
        else:
            if current:
                current = current + "\n" + line
            else:
                current = line
    if current:
        parts.append(current)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TELEGRAM not configured")
        print(text)
        return False

    parts = split_text(text, MAX_MSG_LEN)
    ok = True
    for i, part in enumerate(parts):
        try:
            url = "https://api.telegram.org/bot"
            url += BOT_TOKEN + "/sendMessage"
            r = requests.post(
                url,
                json={
                    "chat_id": CHAT_ID,
                    "text": part,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=20,
            )
            if r.status_code != 200:
                log.error("tg %d: %s",
                          r.status_code,
                          r.text[:200])
                ok = False
            else:
                log.info("part %d/%d sent",
                         i + 1, len(parts))
        except Exception as e:
            log.exception("send: %s", e)
            ok = False
    return ok


# ============================================================
# ARTIFACT DETECTORS
# ============================================================
HTML_TAG_RE = re.compile(r"<[^>]{1,80}>")
ENTITY_RE = re.compile(r"&[a-z]{2,8};")
BAD_CHARS_RE = re.compile(
    r"[^\w\s]{%d,}" % MAX_BAD_CHARS_RUN
)
LONG_WORD_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё]{%d,}" % MAX_WORD_LEN
)


def scan_text_artifacts(text):
    """Возвращает список типов мусора."""
    flags = []
    if HTML_TAG_RE.search(text):
        flags.append("html")
    if ENTITY_RE.search(text):
        flags.append("entity")
    if BAD_CHARS_RE.search(text):
        flags.append("bad_chars")
    if LONG_WORD_RE.search(text):
        flags.append("long_word")
    return flags


# ============================================================
# CHECK 1: knowledge.json = {"books":[...]}
# ============================================================
def check_knowledge():
    log.info("check: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, None)
    if data is None:
        return {"error": "file missing or bad json"}
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", None)
    if books is None:
        return {"error": "no 'books' key"}
    if not isinstance(books, list):
        return {"error": "'books' not a list"}

    names = []
    total_pages = 0
    total_chunks_meta = 0
    no_file = 0
    bad_entry = 0

    for b in books:
        if not isinstance(b, dict):
            bad_entry += 1
            continue
        name = b.get("file", "")
        if not name:
            no_file += 1
            continue
        names.append(name)
        try:
            total_pages += int(b.get("pages", 0) or 0)
        except Exception:
            pass
        try:
            total_chunks_meta += int(
                b.get("chunks", 0) or 0
            )
        except Exception:
            pass

    counts = Counter(names)
    dups = {
        n: c for n, c in counts.items() if c > 1
    }

    return {
        "total": len(books),
        "unique": len(set(names)),
        "duplicates": dups,
        "no_file": no_file,
        "bad_entry": bad_entry,
        "total_pages": total_pages,
        "total_chunks_meta": total_chunks_meta,
        "generated_at": str(
            data.get("generated_at", "?")
        )[:19],
    }


# ============================================================
# CHECK 2: summary.json
# ============================================================
def check_summary():
    log.info("check: summary.json")
    data = load_json(SUMMARY_FILE, None)
    if data is None:
        return {"error": "file missing or bad json"}
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", None)
    if books is None:
        return {"error": "no 'books' key"}
    if not isinstance(books, list):
        return {"error": "'books' not a list"}

    names = []
    no_file = 0
    for b in books:
        if not isinstance(b, dict):
            continue
        name = b.get("file", "")
        if not name:
            no_file += 1
            continue
        names.append(name)

    counts = Counter(names)
    dups = {
        n: c for n, c in counts.items() if c > 1
    }
    dup_total = sum(c - 1 for c in dups.values())

    return {
        "total": len(books),
        "unique": len(set(names)),
        "duplicates": dups,
        "dup_total": dup_total,
        "no_file": no_file,
        "generated_at": str(
            data.get("generated_at", "?")
        )[:19],
    }


# ============================================================
# CHECK 3: chunks_for_index.json
# ============================================================
def check_chunks():
    log.info("check: chunks_for_index.json")
    data = load_json(CHUNKS_FILE, None)
    if data is None:
        return {"error": "file missing or bad json"}
    if not isinstance(data, list):
        return {"error": "not a list"}

    total = len(data)
    empty_ids = 0
    empty_text = 0
    too_short = 0
    too_long = 0
    dup_ids = 0
    dup_text = 0
    with_artifacts = 0
    artifact_types = Counter()
    book_artifacts = Counter()
    books_seen = Counter()

    seen_ids = set()
    seen_text = set()

    for item in data:
        if not isinstance(item, dict):
            continue

        cid = item.get("id", "")
        book = item.get("book", "?")
        text = item.get("text", "") or ""

        books_seen[book] += 1

        if not cid:
            empty_ids += 1
        elif cid in seen_ids:
            dup_ids += 1
        else:
            seen_ids.add(cid)

        if not text.strip():
            empty_text += 1
            continue

        h = md5(text)
        if h in seen_text:
            dup_text += 1
        else:
            seen_text.add(h)

        if len(text) < MIN_CHUNK_LEN:
            too_short += 1
        if len(text) > MAX_CHUNK_LEN:
            too_long += 1

        flags = scan_text_artifacts(text)
        if flags:
            with_artifacts += 1
            for f in flags:
                artifact_types[f] += 1
            book_artifacts[book] += 1

    return {
        "total": total,
        "books_count": len(books_seen),
        "empty_ids": empty_ids,
        "empty_text": empty_text,
        "too_short": too_short,
        "too_long": too_long,
        "dup_ids": dup_ids,
        "dup_text": dup_text,
        "with_artifacts": with_artifacts,
        "artifact_types": artifact_types,
        "book_artifacts": book_artifacts,
        "books_seen": books_seen,
    }


# ============================================================
# CHECK 4: books/
# ============================================================
def check_books_dir():
    log.info("check: books/")
    if not BOOKS_DIR.exists():
        return {"error": "no books/ dir"}

    pdfs = list(BOOKS_DIR.glob("*.pdf"))
    zero = []
    sizes = []
    for p in pdfs:
        try:
            s = p.stat().st_size
            sizes.append(s)
            if s == 0:
                zero.append(p.name)
        except Exception:
            continue

    total_mb = round(sum(sizes) / 1024 / 1024, 1)

    return {
        "count": len(pdfs),
        "zero_bytes": zero,
        "total_mb": total_mb,
    }


# ============================================================
# CHECK 5: faiss.index
# ============================================================
def check_faiss():
    log.info("check: faiss.index")
    if not FAISS_FILE.exists():
        return {"error": "not found at data/"}
    size_mb = round(
        FAISS_FILE.stat().st_size / 1024 / 1024, 2
    )
    return {
        "exists": True,
        "size_mb": size_mb,
        "size_bytes": FAISS_FILE.stat().st_size,
    }


# ============================================================
# CROSS-CHECK: knowledge vs chunks
# ============================================================
def cross_check(kn, ch):
    log.info("cross-check: knowledge vs chunks")
    if "error" in kn or "error" in ch:
        return {"error": "skipped"}

    meta_chunks = kn["total_chunks_meta"]
    file_chunks = ch["total"]
    diff = meta_chunks - file_chunks

    # Книги в knowledge, но не в chunks
    kn_books = set()
    # Тут we don't have per-book from knowledge easily
    # (only names). Compare counts.
    ch_books = set(ch["books_seen"].keys())

    return {
        "meta_chunks": meta_chunks,
        "file_chunks": file_chunks,
        "diff": diff,
        "ch_books": ch_books,
    }


# ============================================================
# BUILD REPORT
# ============================================================
def build_report(kn, sm, ch, bd, fs, cc):
    now = datetime.now(timezone.utc)
    L = []
    L.append("🔍 <b>ARGUS — аудит данных v2</b>")
    L.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    L.append("")

    # 1. knowledge.json
    L.append("📚 <b>knowledge.json</b>")
    if "error" in kn:
        L.append("  ❌ " + esc(kn["error"]))
    else:
        L.append("  книг: " + str(kn["total"]))
        L.append(
            "  уникальных: " + str(kn["unique"])
        )
        L.append(
            "  страниц: "
            + format(kn["total_pages"], ",")
        )
        L.append(
            "  chunks (meta): "
            + format(kn["total_chunks_meta"], ",")
        )
        L.append(
            "  generated: " + esc(kn["generated_at"])
        )
        probs = []
        if kn["no_file"]:
            probs.append(
                "  ⚠️ без file: "
                + str(kn["no_file"])
            )
        if kn["bad_entry"]:
            probs.append(
                "  ⚠️ битых записей: "
                + str(kn["bad_entry"])
            )
        if kn["duplicates"]:
            probs.append(
                "  ⚠️ дублей книг: "
                + str(len(kn["duplicates"]))
            )
        if probs:
            L.extend(probs)
        else:
            L.append("  ✅ структура ok")
    L.append("")

    # 2. summary.json
    L.append("📋 <b>summary.json</b>")
    if "error" in sm:
        L.append("  ❌ " + esc(sm["error"]))
    else:
        L.append("  записей: " + str(sm["total"]))
        L.append(
            "  уникальных: " + str(sm["unique"])
        )
        L.append(
            "  generated: " + esc(sm["generated_at"])
        )
        if sm["duplicates"]:
            L.append(
                "  ⚠️ <b>дублей: "
                + str(sm["dup_total"])
                + "</b> ("
                + str(len(sm["duplicates"]))
                + " имён)"
            )
            for n, c in list(
                sm["duplicates"].items()
            )[:8]:
                L.append(
                    "    • " + esc(n[:40])
                    + " ×" + str(c)
                )
        else:
            L.append("  ✅ дублей нет")
        if sm["no_file"]:
            L.append(
                "  ⚠️ без file: " + str(sm["no_file"])
            )
    L.append("")

    # 3. chunks_for_index.json
    L.append("🗂 <b>chunks_for_index.json</b>")
    if "error" in ch:
        L.append("  ❌ " + esc(ch["error"]))
    else:
        L.append(
            "  всего: " + format(ch["total"], ",")
        )
        L.append(
            "  книг: " + str(ch["books_count"])
        )
        probs = []
        if ch["empty_ids"]:
            probs.append(
                "  ⚠️ пустых id: "
                + str(ch["empty_ids"])
            )
        if ch["dup_ids"]:
            probs.append(
                "  ⚠️ дублей id: "
                + str(ch["dup_ids"])
            )
        if ch["empty_text"]:
            probs.append(
                "  ⚠️ пустого текста: "
                + str(ch["empty_text"])
            )
        if ch["too_short"]:
            probs.append(
                "  ⚠️ коротких (<"
                + str(MIN_CHUNK_LEN)
                + "): " + str(ch["too_short"])
            )
        if ch["too_long"]:
            probs.append(
                "  ⚠️ длинных (>"
                + str(MAX_CHUNK_LEN)
                + "): " + str(ch["too_long"])
            )
        if ch["dup_text"]:
            probs.append(
                "  ⚠️ дублей текста: "
                + str(ch["dup_text"])
            )
        if ch["with_artifacts"]:
            probs.append(
                "  ⚠️ с артефактами: "
                + str(ch["with_artifacts"])
            )
        if probs:
            L.extend(probs)
        else:
            L.append("  ✅ чисто")
    L.append("")

    # 4. Артефакты
    if ("error" not in ch
            and ch.get("with_artifacts")):
        L.append("🧹 <b>Типы артефактов</b>")
        for t, n in ch[
            "artifact_types"
        ].most_common(5):
            L.append(
                "  • " + esc(t) + ": " + str(n)
            )
        L.append("")
        L.append("📖 <b>Книги с артефактами</b>")
        for b, n in ch[
            "book_artifacts"
        ].most_common(8):
            L.append(
                "  • " + esc(b[:45])
                + ": " + str(n)
            )
        L.append("")

    # 5. Cross-check
    L.append("🔗 <b>Cross-check</b>")
    if "error" in cc:
        L.append("  ❌ " + esc(cc["error"]))
    else:
        L.append(
            "  chunks в knowledge: "
            + format(cc["meta_chunks"], ",")
        )
        L.append(
            "  chunks в файле: "
            + format(cc["file_chunks"], ",")
        )
        d = cc["diff"]
        if d == 0:
            L.append("  ✅ совпадает")
        else:
            L.append(
                "  ⚠️ расхождение: "
                + str(d)
            )
    L.append("")

    # 6. books/
    L.append("📁 <b>books/</b>")
    if "error" in bd:
        L.append("  ❌ " + esc(bd["error"]))
    else:
        L.append("  PDF: " + str(bd["count"]))
        L.append(
            "  размер: " + str(bd["total_mb"])
            + " МБ"
        )
        if bd["zero_bytes"]:
            L.append(
                "  ⚠️ пустых: "
                + str(len(bd["zero_bytes"]))
            )
    L.append("")

    # 7. faiss.index
    L.append("🧠 <b>faiss.index</b>")
    if "error" in fs:
        L.append("  ❌ " + esc(fs["error"]))
    else:
        L.append(
            "  размер: " + str(fs["size_mb"])
            + " МБ"
        )
    L.append("")

    L.append(
        "<i>Режим: audit only (без записи)</i>"
    )

    return "\n".join(L)


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 50)
    log.info("ARGUS data audit v2 (audit only)")
    log.info("=" * 50)

    kn = check_knowledge()
    sm = check_summary()
    ch = check_chunks()
    bd = check_books_dir()
    fs = check_faiss()
    cc = cross_check(kn, ch)

    text = build_report(kn, sm, ch, bd, fs, cc)
    log.info("report: %d chars", len(text))

    send_message(text)
    log.info("done")


if __name__ == "__main__":
    main()