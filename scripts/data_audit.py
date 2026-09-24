# ============================================================
# ARGUS - DATA AUDIT v3 [AUDIT + FIX]
# ------------------------------------------------------------
# Режимы:
#   (без флагов)            - audit only
#   --fix                   - dry preview фиксов
#   --fix --apply           - применить (с бэкапом)
# ------------------------------------------------------------
# Что чинит:
#   - дубли в knowledge.books (по file)
#   - дубли в summary.books
#   - HTML + entity в knowledge.chunks[].text
# ------------------------------------------------------------
# Что НЕ делает:
#   - не удаляет chunks
#   - не меняет id
#   - не трогает long_word/bad_chars
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import re
import json
import shutil
import hashlib
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


def save_json(path, data):
    """Атомарная запись."""
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data, f,
                ensure_ascii=False,
                indent=2,
            )
        tmp.replace(path)
        log.info("wrote: %s", path.name)
        return True
    except Exception as e:
        log.exception("save %s: %s", path.name, e)
        return False


def backup_file(path):
    """Копия рядом с оригиналом. Возвращает имя или None."""
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    dst = path.with_name(
        path.name + ".bak." + ts
    )
    try:
        shutil.copy2(path, dst)
        log.info("backup: %s", dst.name)
        return dst.name
    except Exception as e:
        log.exception("backup: %s", e)
        return None


def md5(text):
    return hashlib.md5(
        text.encode("utf-8")
    ).hexdigest()


def esc(text):
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
BAD_CHARS_RE = re.compile(
    r"[^\w\s]{%d,}" % MAX_BAD_CHARS_RUN
)
LONG_WORD_RE = re.compile(
    r"[A-Za-zА-Яа-яЁё]{%d,}" % MAX_WORD_LEN
)


def clean_html(text):
    """Убирает HTML и entity. Возвращает (text, changed)."""
    if not text:
        return text, False
    before = text
    for k, v in ENTITY_MAP.items():
        if k in text:
            text = text.replace(k, v)
    if HTML_TAG_RE.search(text):
        text = HTML_TAG_RE.sub(" ", text)
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


def scan_artifacts(text):
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
# CHECK: knowledge.json
# ============================================================
def check_knowledge():
    log.info("check: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, None)
    if data is None:
        return {"error": "missing or bad json"}
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", [])
    chunks = data.get("chunks", [])

    if not isinstance(books, list):
        return {"error": "books not a list"}
    if not isinstance(chunks, list):
        return {"error": "chunks not a list"}

    names = []
    no_file = 0
    for b in books:
        if not isinstance(b, dict):
            continue
        n = b.get("file", "")
        if not n:
            no_file += 1
            continue
        names.append(n)

    counts = Counter(names)
    dups = {n: c for n, c in counts.items() if c > 1}

    return {
        "books_total": len(books),
        "books_unique": len(set(names)),
        "books_dups": dups,
        "books_no_file": no_file,
        "chunks_total": len(chunks),
    }


# ============================================================
# CHECK: summary.json
# ============================================================
def check_summary():
    log.info("check: summary.json")
    data = load_json(SUMMARY_FILE, None)
    if data is None:
        return {"error": "missing or bad json"}
    if not isinstance(data, dict):
        return {"error": "not a dict"}

    books = data.get("books", [])
    if not isinstance(books, list):
        return {"error": "books not a list"}

    names = []
    for b in books:
        if not isinstance(b, dict):
            continue
        n = b.get("file", "")
        if n:
            names.append(n)

    counts = Counter(names)
    dups = {n: c for n, c in counts.items() if c > 1}

    return {
        "total": len(books),
        "unique": len(set(names)),
        "dups": dups,
        "generated_at": str(
            data.get("generated_at", "?")
        )[:19],
    }


# ============================================================
# CHECK: chunks (в knowledge + в файле)
# ============================================================
def check_chunks_in_knowledge():
    log.info("check: knowledge.chunks")
    data = load_json(KNOWLEDGE_FILE, {})
    chunks = data.get("chunks", []) if isinstance(
        data, dict
    ) else []
    if not isinstance(chunks, list):
        return {"error": "chunks not a list"}

    total = len(chunks)
    empty_ids = 0
    empty_text = 0
    too_short = 0
    too_long = 0
    with_html = 0
    with_entity = 0
    with_artifacts = 0
    artifact_types = Counter()
    book_artifacts = Counter()
    books_seen = Counter()

    for c in chunks:
        if not isinstance(c, dict):
            continue
        cid = c.get("id", "")
        book = c.get("book", "?")
        text = c.get("text", "") or ""
        books_seen[book] += 1
        if not cid:
            empty_ids += 1
        if not text.strip():
            empty_text += 1
            continue
        if len(text) < MIN_CHUNK_LEN:
            too_short += 1
        if len(text) > MAX_CHUNK_LEN:
            too_long += 1
        if HTML_TAG_RE.search(text):
            with_html += 1
        if ENTITY_RE.search(text):
            with_entity += 1
        flags = scan_artifacts(text)
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
        "with_html": with_html,
        "with_entity": with_entity,
        "with_artifacts": with_artifacts,
        "artifact_types": artifact_types,
        "book_artifacts": book_artifacts,
    }


def check_metadata_file():
    log.info("check: chunks_for_index.json")
    if not CHUNKS_FILE.exists():
        return {"missing": True}
    data = load_json(CHUNKS_FILE, None)
    if not isinstance(data, list):
        return {"error": "not a list"}
    return {"total": len(data)}


def check_faiss():
    log.info("check: faiss.index")
    if not FAISS_FILE.exists():
        return {"error": "not found at data/"}
    size_mb = round(
        FAISS_FILE.stat().st_size / 1024 / 1024, 2
    )
    return {"exists": True, "size_mb": size_mb}


def check_books_dir():
    log.info("check: books/")
    if not BOOKS_DIR.exists():
        return {"error": "no books/ dir"}
    pdfs = list(BOOKS_DIR.glob("*.pdf"))
    return {
        "count": len(pdfs),
        "names": [p.name for p in pdfs[:10]],
    }


# ============================================================
# FIX: dedupe books
# ============================================================
def plan_dedupe_books(books, key="file"):
    """Оставляет первую запись по key. Возвращает (kept, removed)."""
    seen = set()
    kept = []
    removed = []
    for b in books:
        if not isinstance(b, dict):
            continue
        name = b.get(key, "")
        if not name:
            kept.append(b)
            continue
        if name in seen:
            removed.append(name)
            continue
        seen.add(name)
        kept.append(b)
    return kept, removed


# ============================================================
# FIX: clean HTML in chunks
# ============================================================
def plan_clean_chunks(chunks):
    """Возвращает (fixed_count, samples, empty_after)."""
    fixed = 0
    samples = []
    empty_after = 0
    for c in chunks:
        if not isinstance(c, dict):
            continue
        text = c.get("text", "") or ""
        if not text:
            continue
        cleaned, changed = clean_html(text)
        if changed:
            fixed += 1
            if len(samples) < 3:
                samples.append(text[:60].replace("\n", " "))
        if not cleaned.strip():
            empty_after += 1
    return fixed, samples, empty_after


def apply_clean_chunks(chunks):
    """Чистит HTML на месте. Возвращает число изменений."""
    fixed = 0
    for c in chunks:
        if not isinstance(c, dict):
            continue
        text = c.get("text", "") or ""
        if not text:
            continue
        cleaned, changed = clean_html(text)
        if changed:
            c["text"] = cleaned
            fixed += 1
    return fixed


# ============================================================
# REINDEX PREP
# ============================================================
def prepare_reindex():
    """Удаляет faiss + metadata, пишет флаг."""
    removed = []
    for p in (FAISS_FILE, CHUNKS_FILE):
        if p.exists():
            try:
                p.unlink()
                removed.append(p.name)
                log.info("removed: %s", p.name)
            except Exception as e:
                log.exception("remove %s: %s", p.name, e)
    try:
        REINDEX_FLAG.write_text(
            datetime.now(timezone.utc).isoformat()
            + "\nreason: chunks changed\n",
            encoding="utf-8",
        )
        log.info("wrote: reindex_needed.txt")
    except Exception as e:
        log.exception("flag: %s", e)
    return removed


# ============================================================
# REPORT: AUDIT
# ============================================================
def build_audit_report(kn, sm, ch, md, fs, bd):
    now = datetime.now(timezone.utc)
    L = []
    L.append("🔍 <b>ARGUS — аудит v3</b>")
    L.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    L.append("")

    # knowledge
    L.append("📚 <b>knowledge.json</b>")
    if "error" in kn:
        L.append("  ❌ " + esc(kn["error"]))
    else:
        L.append("  книг: " + str(kn["books_total"]))
        L.append(
            "  уникальных: " + str(kn["books_unique"])
        )
        L.append(
            "  чанков: "
            + format(kn["chunks_total"], ",")
        )
        if kn["books_dups"]:
            L.append(
                "  ⚠️ дублей книг: "
                + str(len(kn["books_dups"]))
            )
            for n, c in list(
                kn["books_dups"].items()
            )[:6]:
                L.append(
                    "    • " + esc(n[:42])
                    + " ×" + str(c)
                )
        else:
            L.append("  ✅ дублей книг нет")
    L.append("")

    # summary
    L.append("📋 <b>summary.json</b>")
    if "error" in sm:
        L.append("  ❌ " + esc(sm["error"]))
    else:
        L.append("  записей: " + str(sm["total"]))
        if sm["dups"]:
            L.append(
                "  ⚠️ дублей: "
                + str(len(sm["dups"]))
            )
        else:
            L.append("  ✅ чисто")
        L.append(
            "  generated: " + esc(sm["generated_at"])
        )
    L.append("")

    # chunks
    L.append("🗂 <b>knowledge.chunks</b>")
    if "error" in ch:
        L.append("  ❌ " + esc(ch["error"]))
    else:
        L.append(
            "  всего: " + format(ch["total"], ",")
        )
        if ch["with_html"]:
            L.append(
                "  ⚠️ HTML: " + str(ch["with_html"])
            )
        if ch["with_entity"]:
            L.append(
                "  ⚠️ entity: "
                + str(ch["with_entity"])
            )
        if ch["too_long"]:
            L.append(
                "  ⚠️ длинных ("
                + str(MAX_CHUNK_LEN) + "): "
                + str(ch["too_long"])
            )
        if ch["empty_text"]:
            L.append(
                "  ⚠️ пустого текста: "
                + str(ch["empty_text"])
            )
        if not (ch["with_html"] or ch["with_entity"]):
            L.append("  ✅ HTML/entity нет")
    L.append("")

    # artifacts
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
        ].most_common(6):
            L.append(
                "  • " + esc(b[:42])
                + ": " + str(n)
            )
        L.append("")

    # metadata + faiss
    L.append("🔗 <b>Синхронизация</b>")
    if "error" in md or md.get("missing"):
        L.append("  chunks_for_index: ❌ нет")
    else:
        L.append(
            "  chunks_for_index: "
            + format(md["total"], ",")
        )
    if "error" in fs:
        L.append("  faiss.index: ❌ " + esc(fs["error"]))
    else:
        L.append(
            "  faiss.index: "
            + str(fs["size_mb"]) + " МБ"
        )
    L.append("")

    # books/
    L.append("📁 <b>books/</b>")
    if "error" in bd:
        L.append("  ❌ " + esc(bd["error"]))
    else:
        L.append("  PDF: " + str(bd["count"]))
    L.append("")

    L.append(
        "<i>Режим: audit only</i>"
    )
    return "\n".join(L)


# ============================================================
# REPORT: FIX PREVIEW
# ============================================================
def build_fix_preview(kn, sm, ch, apply_mode,
                      backups, removed_files,
                      chunks_fixed):
    now = datetime.now(timezone.utc)
    L = []
    mode = "APPLIED" if apply_mode else "DRY-RUN"
    L.append("🔧 <b>ARGUS — fix v3 [" + mode + "]</b>")
    L.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    L.append("")

    # Backups
    if apply_mode:
        L.append("💾 <b>Бэкапы</b>")
        if backups:
            for b in backups:
                L.append("  • " + esc(b))
        else:
            L.append("  (нечего бэкапить)")
        L.append("")

    # Dedupe knowledge.books
    L.append("📚 <b>knowledge.books</b>")
    if "error" in kn:
        L.append("  ❌ " + esc(kn["error"]))
    else:
        dups = kn.get("books_dups", {})
        if dups:
            L.append(
                "  🧹 удалить дублей: "
                + str(sum(
                    c - 1 for c in dups.values()
                ))
            )
            for n, c in list(dups.items())[:6]:
                L.append(
                    "    • " + esc(n[:42])
                    + " ×" + str(c)
                )
        else:
            L.append("  ✅ нечего чистить")
    L.append("")

    # Dedupe summary.books
    L.append("📋 <b>summary.books</b>")
    if "error" in sm:
        L.append("  ❌ " + esc(sm["error"]))
    else:
        dups = sm.get("dups", {})
        if dups:
            L.append(
                "  🧹 удалить дублей: "
                + str(sum(
                    c - 1 for c in dups.values()
                ))
            )
        else:
            L.append("  ✅ нечего чистить")
    L.append("")

    # HTML clean
    L.append("🧹 <b>HTML/entity в chunks</b>")
    if "error" in ch:
        L.append("  ❌ " + esc(ch["error"]))
    elif chunks_fixed:
        L.append(
            "  🧹 очищено чанков: "
            + str(chunks_fixed)
        )
    else:
        L.append("  ✅ нечего чистить")
    L.append("")

    # Reindex
    if apply_mode and chunks_fixed:
        L.append("🔁 <b>Reindex</b>")
        L.append("  ⚠️ chunks изменились")
        for f in removed_files:
            L.append("  🗑 удалён: " + esc(f))
        L.append(
            "  📌 напиши /train в боте"
        )
        L.append("")

    if not apply_mode:
        L.append(
            "<i>Это preview. Запусти с "
            "--apply чтобы применить.</i>"
        )
    return "\n".join(L)


# ============================================================
# MAIN
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fix", action="store_true",
        help="показать план фиксов",
    )
    ap.add_argument(
        "--apply", action="store_true",
        help="применить фиксы (требует --fix)",
    )
    args = ap.parse_args()

    log.info("=" * 50)
    log.info("ARGUS data audit v3")
    log.info(
        "fix=%s apply=%s",
        args.fix, args.apply,
    )
    log.info("=" * 50)

    if args.apply and not args.fix:
        log.error("--apply требует --fix")
        return

    # ---- AUDIT (всегда) ----
    kn = check_knowledge()
    sm = check_summary()
    ch = check_chunks_in_knowledge()
    md = check_metadata_file()
    fs = check_faiss()
    bd = check_books_dir()

    if not args.fix:
        text = build_audit_report(
            kn, sm, ch, md, fs, bd
        )
        log.info("audit report: %d chars", len(text))
        send_message(text)
        return

    # ---- FIX ----
    backups = []
    removed_files = []
    chunks_fixed = 0

    # Preview count
    if "error" not in ch:
        tmp = load_json(KNOWLEDGE_FILE, {})
        chunks = tmp.get("chunks", []) if isinstance(
            tmp, dict
        ) else []
        cfix, _, _ = plan_clean_chunks(chunks)
    else:
        cfix = 0

    if args.apply:
        # Backup
        b1 = backup_file(KNOWLEDGE_FILE)
        b2 = backup_file(SUMMARY_FILE)
        for b in (b1, b2):
            if b:
                backups.append(b)

        # Fix knowledge
        if "error" not in kn:
            kdata = load_json(KNOWLEDGE_FILE, {})
            books = kdata.get("books", [])
            kept, _ = plan_dedupe_books(books, "file")
            kdata["books"] = kept
            chunks_fixed = apply_clean_chunks(
                kdata.get("chunks", [])
            )
            kdata["audit_cleaned_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            save_json(KNOWLEDGE_FILE, kdata)

        # Fix summary
        if "error" not in sm:
            sdata = load_json(SUMMARY_FILE, {})
            books = sdata.get("books", [])
            kept, _ = plan_dedupe_books(books, "file")
            sdata["books"] = kept
            sdata["audit_cleaned_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            save_json(SUMMARY_FILE, sdata)

        # Reindex prep
        if chunks_fixed > 0:
            removed_files = prepare_reindex()

    text = build_fix_preview(
        kn, sm, ch, args.apply,
        backups, removed_files, cfix,
    )
    log.info("fix report: %d chars", len(text))
    send_message(text)
    log.info("done")


if __name__ == "__main__":
    main()