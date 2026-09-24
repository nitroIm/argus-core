# ============================================================
# ARGUS - DATA AUDIT v2 [PRODUCTION]
# ------------------------------------------------------------
# Проверяет + чистит данные ARGUS Core.
# v2 vs v1:
#   - knowledge.json читается как dict
#   - faiss.index: data/faiss.index
#   - автофикс дублей в summary.json
#   - автофикс HTML в chunks_for_index.json
#   - marker data/reindex_needed.txt
#   - тихий режим если чисто
#   - --dry-run, --force
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import re
import sys
import json
import argparse
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
REINDEX_FLAG = DATA_DIR / "reindex_needed.txt"

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


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                data, f,
                ensure_ascii=False,
                indent=2,
            )
        log.info("wrote: %s", path.name)
        return True
    except Exception as e:
        log.exception("save %s: %s", path.name, e)
        return False


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


def esc(text):
    """Экранирует HTML для Telegram."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# HTML CLEANER
# ============================================================
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


def clean_html(text):
    """Убирает HTML и сущности. Возвращает (text, changed)."""
    if not text:
        return text, False
    before = text
    # Сначала сущности — чтобы &lt;p&gt; стал <p>
    # и потом срезался как тег
    for k, v in ENTITY_MAP.items():
        if k in text:
            text = text.replace(k, v)
    # Убираем теги
    if HTML_TAG_RE.search(text):
        text = HTML_TAG_RE.sub(" ", text)
    # Схлопываем лишние пробелы
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    return text, (before != text)


def has_html(text):
    if not text:
        return False
    if HTML_TAG_RE.search(text):
        return True
    if ENTITY_RE.search(text):
        return True
    return False


# ============================================================
# REINDEX FLAG
# ============================================================
def write_reindex_flag(reason):
    try:
        REINDEX_FLAG.write_text(
            datetime.now(timezone.utc).isoformat()
            + "\nreason: " + reason + "\n",
            encoding="utf-8",
        )
        log.info("reindex flag: %s", reason)
    except Exception as e:
        log.exception("flag: %s", e)


# ============================================================
# CHECK: knowledge.json = {"books":[...]}
# ============================================================
def check_knowledge():
    log.info("check: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, {})
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", [])
    if not isinstance(books, list):
        return {"error": "books not a list"}

    names = []
    total_pages = 0
    total_chunks = 0
    no_file = 0

    for b in books:
        if not isinstance(b, dict):
            continue
        name = b.get("file", "")
        if not name:
            no_file += 1
            continue
        names.append(name)
        total_pages += int(b.get("pages", 0) or 0)
        total_chunks += int(b.get("chunks", 0) or 0)

    counts = Counter(names)
    dups = {
        n: c for n, c in counts.items() if c > 1
    }

    return {
        "total": len(books),
        "unique": len(set(names)),
        "duplicates": dups,
        "no_file": no_file,
        "total_pages": total_pages,
        "total_chunks": total_chunks,
        "generated_at": data.get("generated_at", "?"),
    }


# ============================================================
# CLEAN: summary.json (dedupe books)
# ============================================================
def clean_summary(dry_run):
    log.info("clean: summary.json")
    if not SUMMARY_FILE.exists():
        return {"error": "not found"}

    data = load_json(SUMMARY_FILE, {})
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", [])
    if not isinstance(books, list):
        return {"error": "books not a list"}

    seen = set()
    unique = []
    removed = []
    for b in books:
        if not isinstance(b, dict):
            continue
        name = b.get("file", "")
        if name in seen:
            removed.append(name)
            continue
        seen.add(name)
        unique.append(b)

    result = {
        "before": len(books),
        "after": len(unique),
        "removed": removed,
        "written": False,
    }

    if removed:
        if dry_run:
            log.info("dry-run: skip write summary")
        else:
            data["books"] = unique
            data["audit_cleaned_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            if save_json(SUMMARY_FILE, data):
                result["written"] = True

    return result


# ============================================================
# CLEAN: chunks_for_index.json (HTML)
# ============================================================
def clean_chunks(dry_run):
    log.info("clean: chunks_for_index.json")
    if not CHUNKS_FILE.exists():
        return {"error": "not found"}

    data = load_json(CHUNKS_FILE, [])
    if not isinstance(data, list):
        return {"error": "not a list"}

    total = len(data)
    fixed = 0
    empty_after = 0
    still_html = 0
    samples = []

    for item in data:
        if not isinstance(item, dict):
            continue
        text = item.get("text", "") or ""
        if not text:
            continue

        cleaned, changed = clean_html(text)

        if changed:
            fixed += 1
            if len(samples) < 3:
                samples.append(
                    text[:60].replace("\n", " ")
                )
            item["text"] = cleaned

        if not cleaned.strip():
            empty_after += 1
        if has_html(cleaned):
            still_html += 1

    result = {
        "total": total,
        "fixed_html": fixed,
        "empty_after": empty_after,
        "still_html": still_html,
        "samples": samples,
        "written": False,
    }

    if fixed > 0:
        if dry_run:
            log.info("dry-run: skip write chunks")
        else:
            if save_json(CHUNKS_FILE, data):
                result["written"] = True
                write_reindex_flag(
                    "chunks_for_index.json changed "
                    "(" + str(fixed) + " chunks) — "
                    "faiss.index is stale"
                )

    return result


# ============================================================
# CHECK: books/
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
# CHECK: faiss.index
# ============================================================
def check_faiss():
    log.info("check: faiss.index")
    if not FAISS_FILE.exists():
        return {"error": "not found at data/"}
    size_mb = round(
        FAISS_FILE.stat().st_size / 1024 / 1024, 1
    )
    return {"exists": True, "size_mb": size_mb}


# ============================================================
# BUILD REPORT
# ============================================================
def build_report(kn, sm, ch, bd, fs,
                 dry_run, flag_exists):
    now = datetime.now(timezone.utc)
    lines = []
    lines.append("🔍 <b>ARGUS — аудит данных v2</b>")
    lines.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    if dry_run:
        lines.append("<i>dry-run (без записи)</i>")
    lines.append("")

    # knowledge.json
    lines.append("📚 <b>knowledge.json</b>")
    if "error" in kn:
        lines.append("  ❌ " + esc(kn["error"]))
    else:
        lines.append(
            "  книг: " + str(kn["total"])
        )
        lines.append(
            "  уникальных: " + str(kn["unique"])
        )
        lines.append(
            "  страниц: "
            + format(kn["total_pages"], ",")
        )
        lines.append(
            "  чанков (meta): "
            + format(kn["total_chunks"], ",")
        )
        if kn["no_file"]:
            lines.append(
                "  ⚠️ без file: "
                + str(kn["no_file"])
            )
        if kn["duplicates"]:
            lines.append(
                "  ⚠️ дубли: "
                + str(len(kn["duplicates"]))
            )
            for n, c in list(
                kn["duplicates"].items()
            )[:5]:
                lines.append(
                    "    • " + esc(n[:40])
                    + " ×" + str(c)
                )
        else:
            lines.append("  ✅ дублей нет")
    lines.append("")

    # summary.json
    lines.append("📋 <b>summary.json</b>")
    if "error" in sm:
        lines.append("  ❌ " + esc(sm["error"]))
    else:
        lines.append(
            "  было: " + str(sm["before"])
            + " → стало: " + str(sm["after"])
        )
        if sm["removed"]:
            lines.append(
                "  🧹 удалено дублей: "
                + str(len(sm["removed"]))
            )
            for n in sm["removed"][:8]:
                lines.append(
                    "    • " + esc(n[:40])
                )
            if sm["written"]:
                lines.append("  ✅ записано")
            elif dry_run:
                lines.append("  (dry-run)")
        else:
            lines.append("  ✅ чисто")
    lines.append("")

    # chunks
    lines.append("🗂 <b>chunks_for_index.json</b>")
    if "error" in ch:
        lines.append("  ❌ " + esc(ch["error"]))
    else:
        lines.append(
            "  всего: " + format(ch["total"], ",")
        )
        if ch["fixed_html"]:
            lines.append(
                "  🧹 очищено от HTML: "
                + str(ch["fixed_html"])
            )
            for s in ch["samples"]:
                lines.append(
                    "    • " + esc(s)
                )
            if ch["written"]:
                lines.append("  ✅ записано")
            elif dry_run:
                lines.append("  (dry-run)")
        else:
            lines.append("  ✅ HTML нет")
        if ch["empty_after"]:
            lines.append(
                "  ⚠️ пустых после чистки: "
                + str(ch["empty_after"])
            )
        if ch["still_html"]:
            lines.append(
                "  ⚠️ остался HTML: "
                + str(ch["still_html"])
            )
    lines.append("")

    # books/
    lines.append("📁 <b>books/</b>")
    if "error" in bd:
        lines.append("  ❌ " + esc(bd["error"]))
    else:
        lines.append("  PDF: " + str(bd["count"]))
        lines.append(
            "  размер: " + str(bd["total_mb"]) + " МБ"
        )
        if bd["zero_bytes"]:
            lines.append(
                "  ⚠️ пустых: "
                + str(len(bd["zero_bytes"]))
            )
    lines.append("")

    # faiss
    lines.append("🧠 <b>faiss.index</b>")
    if "error" in fs:
        lines.append("  ❌ " + esc(fs["error"]))
    else:
        lines.append(
            "  размер: " + str(fs["size_mb"]) + " МБ"
        )
    if flag_exists:
        lines.append(
            "  ⚠️ <b>нужен reindex</b> "
            "(data/reindex_needed.txt)"
        )
    lines.append("")

    lines.append(
        "<i>Тихий режим: молчит если чисто</i>"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run", action="store_true",
        help="не писать файлы, только отчёт",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="отчёт даже если чисто",
    )
    args = parser.parse_args()

    log.info("=" * 50)
    log.info("ARGUS data audit v2")
    log.info(
        "dry_run=%s force=%s",
        args.dry_run, args.force,
    )
    log.info("=" * 50)

    kn = check_knowledge()
    sm = clean_summary(args.dry_run)
    ch = clean_chunks(args.dry_run)
    bd = check_books_dir()
    fs = check_faiss()

    flag_exists = REINDEX_FLAG.exists()

    # Что-то изменилось?
    changed = False
    if isinstance(sm, dict) and sm.get("removed"):
        changed = True
    if isinstance(ch, dict) and ch.get("fixed_html"):
        changed = True

    # Что-то сломано?
    problems = False
    for r in (kn, sm, ch, bd, fs):
        if isinstance(r, dict) and "error" in r:
            problems = True
    if isinstance(kn, dict) and kn.get("duplicates"):
        problems = True
    if isinstance(bd, dict) and bd.get("zero_bytes"):
        problems = True

    if not (changed or problems or args.force):
        log.info("quiet: all clean, skip report")
        return

    text = build_report(
        kn, sm, ch, bd, fs,
        args.dry_run, flag_exists,
    )
    log.info("report: %d chars", len(text))
    send_message(text)
    log.info("done")


if __name__ == "__main__":
    main()