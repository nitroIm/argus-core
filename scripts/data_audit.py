# ============================================================
# ARGUS - DATA AUDIT v3 [SAFE + ROTATION]
# ------------------------------------------------------------
# Без флага - отчёт (read-only)
# --fix     - чистка с бэкапами
# ------------------------------------------------------------
# БЕЗОПАСНОСТЬ:
#   - faiss.index НЕ трогается
#   - chunks НЕ удаляются
#   - id чанков НЕ меняются
#   - перед записью бэкап
#   - храним 3 свежих бэкапа
# ------------------------------------------------------------
# Что чистит:
#   - дубли книг в knowledge.books
#   - дубли книг в summary.books
#   - HTML/entity/bad_chars/long_word
#     в knowledge.chunks и chunks_for_index
# ------------------------------------------------------------
# Требования:
#   pip install requests
# ============================================================

import os
import re
import json
import shutil
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

KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
SUMMARY_FILE = DATA_DIR / "summary.json"
CHUNKS_FILE = DATA_DIR / "chunks_for_index.json"
FAISS_FILE = DATA_DIR / "faiss.index"

# --- Лимиты ---
MAX_WORD_LEN = 30
MAX_BAD_RUN = 6
MAX_MSG_LEN = 3800
KEEP_BACKUPS = 3

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
    if not path.exists():
        return None
    ts = datetime.now(timezone.utc).strftime(
        "%Y%m%d_%H%M%S"
    )
    dst = path.with_name(path.name + ".bak." + ts)
    try:
        shutil.copy2(path, dst)
        log.info("backup: %s", dst.name)
        return dst.name
    except Exception as e:
        log.exception("backup: %s", e)
        return None


def cleanup_old_backups(keep=KEEP_BACKUPS):
    """Оставляет N свежих бэкапов, старые удаляет."""
    patterns = [
        "knowledge.json.bak.*",
        "summary.json.bak.*",
        "chunks_for_index.json.bak.*",
    ]
    for pattern in patterns:
        files = sorted(
            DATA_DIR.glob(pattern),
            key=lambda p: p.name,
        )
        if len(files) <= keep:
            continue
        for old in files[:-keep]:
            try:
                old.unlink()
                log.info("cleanup: %s", old.name)
            except Exception as e:
                log.warning(
                    "cleanup %s: %s", old.name, e
                )


def esc(t):
    return (
        str(t)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def split_msg(text, max_len):
    if len(text) <= max_len:
        return [text]
    parts = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > max_len:
            if cur:
                parts.append(cur)
            cur = line
        else:
            cur = cur + "\n" + line if cur else line
    if cur:
        parts.append(cur)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TELEGRAM not configured")
        print(text)
        return False
    parts = split_msg(text, MAX_MSG_LEN)
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
                log.error(
                    "tg %d: %s",
                    r.status_code,
                    r.text[:200],
                )
                ok = False
            else:
                log.info(
                    "part %d/%d",
                    i + 1,
                    len(parts),
                )
        except Exception as e:
            log.exception("send: %s", e)
            ok = False
    return ok


# ============================================================
# CLEANERS
# ============================================================
HTML_TAG_RE = re.compile(r"<[^>]{1,80}>")
ENTITY_RE = re.compile(r"&[a-z]{2,8};")
BAD_CHARS_RE = re.compile(
    r"([^\w\s])\1{%d,}" % (MAX_BAD_RUN - 1)
)
LONG_WORD_RE = re.compile(
    r"([A-Za-zА-Яа-яЁё]{%d,})" % MAX_WORD_LEN
)
ENTITY_MAP = {
    "&amp;": "&",
    "&lt;": "<",
    "&gt;": ">",
    "&quot;": '"',
    "&nbsp;": " ",
    "&#39;": "'",
    "&apos;": "'",
}


def clean_text(text):
    """Возвращает (new_text, flags). Не удаляет."""
    if not text:
        return text, []
    flags = []
    before = text

    if HTML_TAG_RE.search(text):
        text = HTML_TAG_RE.sub(" ", text)
        flags.append("html")

    if ENTITY_RE.search(text):
        for k, v in ENTITY_MAP.items():
            text = text.replace(k, v)
        flags.append("entity")

    if BAD_CHARS_RE.search(text):
        text = BAD_CHARS_RE.sub(r"\1", text)
        flags.append("bad_chars")

    if LONG_WORD_RE.search(text):
        text = LONG_WORD_RE.sub(
            lambda m: m.group(1)[:MAX_WORD_LEN],
            text,
        )
        flags.append("long_word")

    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    if text != before:
        return text, flags
    return text, []


# ============================================================
# AUDIT
# ============================================================
def audit_knowledge():
    log.info("audit: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, None)
    if not isinstance(data, dict):
        return {"error": "not a dict"}
    books = data.get("books", [])
    chunks = data.get("chunks", [])
    if not isinstance(books, list):
        return {"error": "books not a list"}
    if not isinstance(chunks, list):
        return {"error": "chunks not a list"}

    names = []
    for b in books:
        if isinstance(b, dict):
            n = b.get("file", "")
            if n:
                names.append(n)
    counts = Counter(names)
    book_dups = {
        n: c for n, c in counts.items() if c > 1
    }

    with_html = 0
    with_entity = 0
    with_bad = 0
    with_long = 0
    empty_text = 0

    for c in chunks:
        if not isinstance(c, dict):
            continue
        t = c.get("text", "") or ""
        if not t.strip():
            empty_text += 1
            continue
        if HTML_TAG_RE.search(t):
            with_html += 1
        if ENTITY_RE.search(t):
            with_entity += 1
        if BAD_CHARS_RE.search(t):
            with_bad += 1
        if LONG_WORD_RE.search(t):
            with_long += 1

    return {
        "books_total": len(books),
        "books_unique": len(set(names)),
        "books_dups": book_dups,
        "chunks_total": len(chunks),
        "with_html": with_html,
        "with_entity": with_entity,
        "with_bad": with_bad,
        "with_long": with_long,
        "empty_text": empty_text,
    }


def audit_summary():
    log.info("audit: summary.json")
    data = load_json(SUMMARY_FILE, None)
    if not isinstance(data, dict):
        return {"error": "not a dict"}
    books = data.get("books", [])
    if not isinstance(books, list):
        return {"error": "books not a list"}
    names = []
    for b in books:
        if isinstance(b, dict):
            n = b.get("file", "")
            if n:
                names.append(n)
    counts = Counter(names)
    dups = {
        n: c for n, c in counts.items() if c > 1
    }
    return {
        "total": len(books),
        "unique": len(set(names)),
        "dups": dups,
    }


def audit_faiss():
    if not FAISS_FILE.exists():
        return {"error": "not found"}
    return {
        "exists": True,
        "size_mb": round(
            FAISS_FILE.stat().st_size
            / 1024 / 1024, 2
        ),
    }


def audit_metadata():
    if not CHUNKS_FILE.exists():
        return {"missing": True}
    data = load_json(CHUNKS_FILE, None)
    if not isinstance(data, list):
        return {"error": "not a list"}
    return {"total": len(data)}


# ============================================================
# FIX
# ============================================================
def dedupe_books_list(books, key="file"):
    seen = set()
    kept = []
    removed = 0
    for b in books:
        if not isinstance(b, dict):
            kept.append(b)
            continue
        n = b.get(key, "")
        if not n:
            kept.append(b)
            continue
        if n in seen:
            removed += 1
            continue
        seen.add(n)
        kept.append(b)
    return kept, removed


def fix_knowledge():
    """Чистит knowledge.json. НЕ удаляет chunks."""
    log.info("fix: knowledge.json")
    data = load_json(KNOWLEDGE_FILE, {})
    if not isinstance(data, dict):
        return None

    books = data.get("books", [])
    chunks = data.get("chunks", [])

    books_new, books_removed = dedupe_books_list(
        books
    )

    text_cleaned = 0
    html_fixed = 0
    entity_fixed = 0
    bad_fixed = 0
    long_fixed = 0

    for c in chunks:
        if not isinstance(c, dict):
            continue
        t = c.get("text", "") or ""
        if not t:
            continue
        new_t, flags = clean_text(t)
        if "html" in flags:
            html_fixed += 1
        if "entity" in flags:
            entity_fixed += 1
        if "bad_chars" in flags:
            bad_fixed += 1
        if "long_word" in flags:
            long_fixed += 1
        if new_t != t:
            c["text"] = new_t
            text_cleaned += 1

    data["books"] = books_new
    data["audit_cleaned_at"] = (
        datetime.now(timezone.utc).isoformat()
    )

    save_json(KNOWLEDGE_FILE, data)

    return {
        "books_removed": books_removed,
        "chunks_total": len(chunks),
        "text_cleaned": text_cleaned,
        "html_fixed": html_fixed,
        "entity_fixed": entity_fixed,
        "bad_fixed": bad_fixed,
        "long_fixed": long_fixed,
    }


def fix_summary():
    log.info("fix: summary.json")
    data = load_json(SUMMARY_FILE, {})
    if not isinstance(data, dict):
        return None
    books = data.get("books", [])
    books_new, removed = dedupe_books_list(books)
    data["books"] = books_new
    data["audit_cleaned_at"] = (
        datetime.now(timezone.utc).isoformat()
    )
    save_json(SUMMARY_FILE, data)
    return {"books_removed": removed}


def fix_metadata():
    """Чистит chunks_for_index.json."""
    log.info("fix: chunks_for_index.json")
    if not CHUNKS_FILE.exists():
        return None
    data = load_json(CHUNKS_FILE, None)
    if not isinstance(data, list):
        return None

    text_cleaned = 0
    for item in data:
        if not isinstance(item, dict):
            continue
        t = item.get("text", "") or ""
        if not t:
            continue
        new_t, _ = clean_text(t)
        if new_t != t:
            item["text"] = new_t
            text_cleaned += 1

    save_json(CHUNKS_FILE, data)
    return {"text_cleaned": text_cleaned}


# ============================================================
# REPORTS
# ============================================================
def build_audit_report(kn, sm, md, fs):
    now = datetime.now(timezone.utc)
    L = []
    L.append("🔍 <b>ARGUS — аудит v3</b>")
    L.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    L.append("")

    L.append("📚 <b>knowledge.json</b>")
    if "error" in kn:
        L.append("  ❌ " + esc(kn["error"]))
    else:
        L.append(
            "  книг: " + str(kn["books_total"])
            + " / уник: " + str(kn["books_unique"])
        )
        L.append(
            "  чанков: "
            + format(kn["chunks_total"], ",")
        )
        probs = []
        if kn["books_dups"]:
            probs.append(
                "  ⚠️ дублей книг: "
                + str(sum(
                    c - 1 for c in
                    kn["books_dups"].values()
                ))
            )
        if kn["with_html"]:
            probs.append(
                "  ⚠️ HTML: "
                + str(kn["with_html"])
            )
        if kn["with_entity"]:
            probs.append(
                "  ⚠️ entity: "
                + str(kn["with_entity"])
            )
        if kn["with_bad"]:
            probs.append(
                "  ⚠️ bad_chars: "
                + str(kn["with_bad"])
            )
        if kn["with_long"]:
            probs.append(
                "  ⚠️ long_word: "
                + str(kn["with_long"])
            )
        if kn["empty_text"]:
            probs.append(
                "  ⚠️ пустого текста: "
                + str(kn["empty_text"])
            )
        if probs:
            L.extend(probs)
        else:
            L.append("  ✅ чисто")
    L.append("")

    L.append("📋 <b>summary.json</b>")
    if "error" in sm:
        L.append("  ❌ " + esc(sm["error"]))
    else:
        L.append("  записей: " + str(sm["total"]))
        if sm["dups"]:
            L.append(
                "  ⚠️ дублей: "
                + str(sum(
                    c - 1 for c in
                    sm["dups"].values()
                ))
            )
        else:
            L.append("  ✅ чисто")
    L.append("")

    L.append("🔗 <b>Синхронизация</b>")
    if md.get("missing"):
        L.append("  chunks_for_index: ❌ нет")
    elif "error" in md:
        L.append(
            "  chunks_for_index: ❌ "
            + esc(md["error"])
        )
    else:
        L.append(
            "  chunks_for_index: "
            + format(md["total"], ",")
        )
    if "error" in fs:
        L.append(
            "  faiss.index: ❌ " + esc(fs["error"])
        )
    else:
        L.append(
            "  faiss.index: "
            + str(fs["size_mb"]) + " МБ"
        )
    L.append("")
    L.append(
        "<i>Отчёт. Запусти с --fix "
        "чтобы почистить.</i>"
    )
    return "\n".join(L)


def build_fix_report(kr, sr, mr, backups):
    now = datetime.now(timezone.utc)
    L = []
    L.append("🔧 <b>ARGUS — fix v3</b>")
    L.append(now.strftime("%d.%m.%Y %H:%M UTC"))
    L.append("")

    L.append("💾 <b>Бэкапы</b>")
    for b in backups:
        L.append("  • " + esc(b))
    L.append(
        "  (храним " + str(KEEP_BACKUPS)
        + " свежих)"
    )
    L.append("")

    if kr:
        L.append("📚 <b>knowledge.json</b>")
        L.append(
            "  🧹 дублей книг: "
            + str(kr["books_removed"])
        )
        L.append(
            "  📊 чанков: "
            + format(kr["chunks_total"], ",")
            + " (не удалялись)"
        )
        L.append(
            "  ✏️ почищено: "
            + str(kr["text_cleaned"])
        )
        if kr["html_fixed"]:
            L.append(
                "    • HTML: "
                + str(kr["html_fixed"])
            )
        if kr["entity_fixed"]:
            L.append(
                "    • entity: "
                + str(kr["entity_fixed"])
            )
        if kr["bad_fixed"]:
            L.append(
                "    • bad_chars: "
                + str(kr["bad_fixed"])
            )
        if kr["long_fixed"]:
            L.append(
                "    • long_word: "
                + str(kr["long_fixed"])
            )
        L.append("")

    if sr:
        L.append("📋 <b>summary.json</b>")
        L.append(
            "  🧹 дублей: "
            + str(sr["books_removed"])
        )
        L.append("")

    if mr:
        L.append(
            "🗂 <b>chunks_for_index.json</b>"
        )
        L.append(
            "  ✏️ почищено: "
            + str(mr["text_cleaned"])
        )
        L.append("")

    L.append("🧠 <b>faiss.index</b>")
    L.append("  ✅ не тронут (/ask работает)")
    L.append("")
    L.append(
        "<i>Можешь позже /train для "
        "пересбора faiss на чистом тексте.</i>"
    )
    return "\n".join(L)


# ============================================================
# MAIN
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fix", action="store_true",
        help="почистить данные",
    )
    args = ap.parse_args()

    log.info("=" * 50)
    log.info("ARGUS data audit v3 (safe)")
    log.info("fix=%s", args.fix)
    log.info("=" * 50)

    # Ротация бэкапов
    cleanup_old_backups(keep=KEEP_BACKUPS)

    kn = audit_knowledge()
    sm = audit_summary()
    md = audit_metadata()
    fs = audit_faiss()

    if not args.fix:
        text = build_audit_report(kn, sm, md, fs)
        log.info("report: %d chars", len(text))
        send_message(text)
        return

    # FIX MODE
    backups = []
    for f in (
        KNOWLEDGE_FILE,
        SUMMARY_FILE,
        CHUNKS_FILE,
    ):
        b = backup_file(f)
        if b:
            backups.append(b)

    kr = fix_knowledge()
    sr = fix_summary()
    mr = fix_metadata()

    text = build_fix_report(
        kr, sr, mr, backups
    )
    log.info("fix report: %d chars", len(text))
    send_message(text)
    log.info("done")


if __name__ == "__main__":
    main()