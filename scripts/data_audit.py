# ============================================================
# ARGUS - DATA AUDIT v1 [PRODUCTION]
# ------------------------------------------------------------
# Проверяет целостность данных ARGUS Core.
# НЕ правит, НЕ удаляет. Только отчёт в TG.
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import re
import sys
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
FAISS_FILE = REPO_ROOT / "faiss.index"

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
MIN_CHUNKS_PER_BOOK = 3
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
HTML_RE = re.compile(r"<[^>]{1,50}>")
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
    if HTML_RE.search(text):
        flags.append("html")
    if ENTITY_RE.search(text):
        flags.append("entity")
    if BAD_CHARS_RE.search(text):
        flags.append("bad_chars")
    if LONG_WORD_RE.search(text):
        flags.append("long_word")
    return flags


# ============================================================
# CHECKS
# ============================================================
def check_knowledge():
    """Проверка knowledge.json."""
    log.info("check: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, [])
    if not isinstance(data, list):
        return {"error": "not a list"}, []

    result = {
        "total": len(data),
        "empty_ids": 0,
        "empty_text": 0,
        "too_short": 0,
        "too_long": 0,
        "dup_ids": 0,
        "dup_text": 0,
        "by_book": Counter(),
        "with_artifacts": 0,
        "artifact_types": Counter(),
        "book_artifacts": Counter(),
    }

    seen_ids = set()
    seen_text = set()

    for item in data:
        if not isinstance(item, dict):
            continue

        cid = item.get("id", "")
        book = item.get("book", "?")
        text = item.get("text", "") or ""

        result["by_book"][book] += 1

        if not cid:
            result["empty_ids"] += 1
        elif cid in seen_ids:
            result["dup_ids"] += 1
        else:
            seen_ids.add(cid)

        if not text.strip():
            result["empty_text"] += 1
            continue

        h = md5(text)
        if h in seen_text:
            result["dup_text"] += 1
        else:
            seen_text.add(h)

        if len(text) < MIN_CHUNK_LEN:
            result["too_short"] += 1
        if len(text) > MAX_CHUNK_LEN:
            result["too_long"] += 1

        flags = scan_text_artifacts(text)
        if flags:
            result["with_artifacts"] += 1
            for f in flags:
                result["artifact_types"][f] += 1
            result["book_artifacts"][book] += 1

    return result, data


def check_summary():
    """Проверка summary.json."""
    log.info("check: summary.json")
    data = load_json(SUMMARY_FILE, {})
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", [])
    names = [b.get("file", "?") for b in books]
    counts = Counter(names)

    dups = {
        n: c for n, c in counts.items() if c > 1
    }

    return {
        "total": len(books),
        "unique": len(set(names)),
        "duplicates": dups,
        "generated_at": data.get("generated_at", "?"),
    }


def check_chunks_file():
    """Проверка chunks_for_index.json."""
    log.info("check: chunks_for_index.json")
    data = load_json(CHUNKS_FILE, [])
    if not isinstance(data, list):
        return {"error": "not a list"}

    no_id = 0
    no_text = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        if not item.get("id"):
            no_id += 1
        if not item.get("text"):
            no_text += 1

    return {
        "total": len(data),
        "no_id": no_id,
        "no_text": no_text,
    }


def check_books_dir():
    """Проверка books/."""
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


def check_faiss():
    """Проверка faiss.index."""
    log.info("check: faiss.index")
    if not FAISS_FILE.exists():
        return {"error": "not found"}
    size_mb = round(
        FAISS_FILE.stat().st_size / 1024 / 1024, 1
    )
    return {"exists": True, "size_mb": size_mb}


# ============================================================
# BUILD REPORT
# ============================================================
def build_report(kn, sm, ch, bd, fs):
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🔍 <b>ARGUS — аудит данных</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    lines.append("")

    # knowledge.json
    lines.append("📚 <b>knowledge.json</b>")
    if "error" in kn:
        lines.append("  ❌ " + kn["error"])
    else:
        lines.append(
            "  всего чанков: "
            + format(kn["total"], ",")
        )
        lines.append(
            "  книг: " + str(len(kn["by_book"]))
        )
        lines.append("")

        problems = []
        if kn["empty_ids"]:
            problems.append(
                "  ⚠️ пустых id: " + str(kn["empty_ids"])
            )
        if kn["dup_ids"]:
            problems.append(
                "  ⚠️ дублей id: " + str(kn["dup_ids"])
            )
        if kn["empty_text"]:
            problems.append(
                "  ⚠️ пустого текста: "
                + str(kn["empty_text"])
            )
        if kn["too_short"]:
            problems.append(
                "  ⚠️ слишком коротких (<"
                + str(MIN_CHUNK_LEN) + "): "
                + str(kn["too_short"])
            )
        if kn["too_long"]:
            problems.append(
                "  ⚠️ слишком длинных (>"
                + str(MAX_CHUNK_LEN) + "): "
                + str(kn["too_long"])
            )
        if kn["dup_text"]:
            problems.append(
                "  ⚠️ дублей текста: "
                + str(kn["dup_text"])
            )
        if kn["with_artifacts"]:
            problems.append(
                "  ⚠️ с артефактами OCR: "
                + str(kn["with_artifacts"])
            )

        if problems:
            lines.append("  <b>Проблемы:</b>")
            lines.extend(problems)
        else:
            lines.append("  ✅ чисто")
    lines.append("")

    # Артефакты
    if "error" not in kn and kn["with_artifacts"]:
        lines.append("🧹 <b>Типы артефактов</b>")
        for t, n in kn["artifact_types"].most_common(5):
            lines.append("  • " + t + ": " + str(n))
        lines.append("")
        lines.append("📖 <b>Книги с артефактами</b>")
        items = kn["book_artifacts"].most_common(8)
        for b, n in items:
            short = b[:50]
            lines.append(
                "  • " + short + ": " + str(n)
            )
        lines.append("")

    # summary.json
    lines.append("📋 <b>summary.json</b>")
    if "error" in sm:
        lines.append("  ❌ " + sm["error"])
    else:
        lines.append("  записей: " + str(sm["total"]))
        lines.append(
            "  уникальных: " + str(sm["unique"])
        )
        lines.append(
            "  от: " + str(sm["generated_at"])[:16]
        )
        if sm["duplicates"]:
            lines.append("")
            lines.append(
                "  ⚠️ <b>Дубликаты в списке:</b>"
            )
            for name, cnt in list(
                sm["duplicates"].items()
            )[:10]:
                short = name[:45]
                lines.append(
                    "  • " + short + " ×" + str(cnt)
                )
        else:
            lines.append("  ✅ дубликатов нет")
    lines.append("")

    # chunks_for_index.json
    lines.append("🗂 <b>chunks_for_index.json</b>")
    if "error" in ch:
        lines.append("  ❌ " + ch["error"])
    else:
        lines.append("  всего: " + str(ch["total"]))
        problems = []
        if ch["no_id"]:
            problems.append(
                "  ⚠️ без id: " + str(ch["no_id"])
            )
        if ch["no_text"]:
            problems.append(
                "  ⚠️ без text: " + str(ch["no_text"])
            )
        if problems:
            lines.extend(problems)
        else:
            lines.append("  ✅ чисто")
    lines.append("")

    # books/
    lines.append("📁 <b>books/</b>")
    if "error" in bd:
        lines.append("  ❌ " + bd["error"])
    else:
        lines.append("  PDF: " + str(bd["count"]))
        lines.append(
            "  размер: " + str(bd["total_mb"]) + " МБ"
        )
        if bd["zero_bytes"]:
            lines.append(
                "  ⚠️ пустых файлов: "
                + str(len(bd["zero_bytes"]))
            )
            for n in bd["zero_bytes"][:5]:
                lines.append("  • " + n[:50])
        else:
            lines.append("  ✅ пустых нет")
    lines.append("")

    # faiss
    lines.append("🧠 <b>faiss.index</b>")
    if "error" in fs:
        lines.append("  ❌ " + fs["error"])
    else:
        lines.append(
            "  размер: " + str(fs["size_mb"]) + " МБ"
        )
    lines.append("")

    # Итог
    total_problems = 0
    if "error" not in kn:
        total_problems += (
            kn["empty_ids"] + kn["dup_ids"]
            + kn["empty_text"] + kn["too_short"]
            + kn["too_long"] + kn["dup_text"]
            + kn["with_artifacts"]
        )
    if "error" not in sm and sm["duplicates"]:
        total_problems += len(sm["duplicates"])

    if total_problems == 0:
        lines.append("✨ <i>Данные чистые</i>")
    else:
        lines.append(
            "⚠️ <b>Всего проблем: "
            + str(total_problems) + "</b>"
        )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("=" * 50)
    log.info("ARGUS data audit v1")
    log.info("=" * 50)

    kn, _ = check_knowledge()
    sm = check_summary()
    ch = check_chunks_file()
    bd = check_books_dir()
    fs = check_faiss()

    text = build_report(kn, sm, ch, bd, fs)
    log.info("report: %d chars", len(text))

    send_message(text)
    log.info("done")


if __name__ == "__main__":
    main()