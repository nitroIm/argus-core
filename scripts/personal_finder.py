# ============================================================
# ARGUS - ЛИЧНЫЙ ПОИСК КНИГ v6
# ------------------------------------------------------------
# v6: карточки приходят ПО ОДНОЙ.
#     Каждая с [Скачать] [Отклонить].
#     HTML entities и мусор чистятся.
# ============================================================

import os
import sys
import json
import html
import re
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CANDIDATES_FILE = DATA_DIR / "personal_candidates.json"

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
)
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

PAGE_SIZE = 5
MAX_CARDS = 30

TRANSLATE_AVAILABLE = False
_model_en_ru = None
_model_ru_en = None
_tok_en_ru = None
_tok_ru_en = None


def _load_models():
    global TRANSLATE_AVAILABLE
    global _model_en_ru, _model_ru_en
    global _tok_en_ru, _tok_ru_en
    try:
        from transformers import (
            MarianMTModel, MarianTokenizer,
        )
        print("Loading translation models...")
        name1 = "Helsinki-NLP/opus-mt-ru-en"
        _tok_ru_en = MarianTokenizer.from_pretrained(name1)
        _model_ru_en = MarianMTModel.from_pretrained(name1)
        name2 = "Helsinki-NLP/opus-mt-en-ru"
        _tok_en_ru = MarianTokenizer.from_pretrained(name2)
        _model_en_ru = MarianMTModel.from_pretrained(name2)
        TRANSLATE_AVAILABLE = True
        print("Translation models loaded")
    except Exception as e:
        print("Translator off: " + str(e))


def translate_to_en(text):
    if not TRANSLATE_AVAILABLE or not text:
        return text
    try:
        batch = _tok_ru_en(
            [text], return_tensors="pt",
            padding=True, truncation=True,
            max_length=128,
        )
        out = _model_ru_en.generate(**batch)
        return _tok_ru_en.decode(
            out[0], skip_special_tokens=True,
        )
    except Exception:
        return text


def translate_to_ru(text):
    if not TRANSLATE_AVAILABLE or not text:
        return text
    try:
        batch = _tok_en_ru(
            [text], return_tensors="pt",
            padding=True, truncation=True,
            max_length=512,
        )
        out = _model_en_ru.generate(**batch)
        return _tok_en_ru.decode(
            out[0], skip_special_tokens=True,
        )
    except Exception:
        return text


def is_cyrillic(text):
    for c in text:
        low = c.lower()
        if 'а' <= low <= 'я':
            return True
        if low == 'ё':
            return True
    return False


def ensure_russian(text):
    if not text:
        return text
    if is_cyrillic(text):
        return text
    return translate_to_ru(text) or text


def ensure_english(text):
    if not text:
        return text
    if not is_cyrillic(text):
        return text
    return translate_to_en(text) or text


# ============================================================
# CLEAN TEXT - убираем мусор
# ============================================================
def clean_text(text):
    """Чистка артефактов: entities, теги, мусор."""
    if not text:
        return ""

    # HTML entities: &iacute; &aacute; &nbsp; &quot; ...
    text = html.unescape(text)

    # HTML теги и мусорные обёртки
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"::", " ", text)
    text = re.sub(r"&[a-zA-Z]+;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)

    # Битые кавычки и точки
    text = re.sub(r'^["\'\s]+', "", text)
    text = re.sub(r'["\'\s]+$', "", text)

    # Множественные пробелы
    text = re.sub(r"\s+", " ", text)

    # Мусор из начала (часто идёт "p" или "span")
    text = re.sub(r'^["\']?p["\']?\s+', "", text)
    text = re.sub(r'^span>?\s*', "", text)

    return text.strip()


def notify_with_buttons(text, keyboard=None):
    if not BOT_TOKEN or not CHAT_ID:
        print("no token")
        print(text)
        return None
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        r = requests.post(
            "https://api.telegram.org/bot"
            + BOT_TOKEN + "/sendMessage",
            json=payload,
            timeout=15,
        )
        if r.status_code == 200:
            return r.json().get("result", {})
    except Exception:
        pass
    return None


# ============================================================
# ПОИСК
# ============================================================
def search_arxiv(topic_en, limit=10):
    results = []
    queries = ['all:"' + topic_en + '"', "all:" + topic_en]
    for q in queries:
        try:
            r = requests.get(
                "http://export.arxiv.org/api/query",
                params={
                    "search_query": q,
                    "start": 0,
                    "max_results": limit,
                    "sortBy": "relevance",
                },
                timeout=20,
            )
            r.raise_for_status()
            entries = r.text.split("<entry>")[1:]
            for entry in entries:
                try:
                    t = entry.split("<title>")[1]
                    title = t.split("</title>")[0].strip()
                    title = " ".join(title.split())
                    link = entry.split("<id>")[1]
                    link = link.split("</id>")[0].strip()
                    arxiv_id = link.split("/abs/")[-1]
                    summary = ""
                    try:
                        s = entry.split("<summary>")[1]
                        summary = s.split("</summary>")[0]
                        summary = " ".join(summary.split())
                        summary = summary[:600]
                    except Exception:
                        pass
                    dup = False
                    for x in results:
                        if x["url"].endswith(arxiv_id + ".pdf"):
                            dup = True
                            break
                    if dup:
                        continue
                    results.append({
                        "title": title,
                        "summary": summary,
                        "url": "https://arxiv.org/pdf/"
                               + arxiv_id + ".pdf",
                        "page_url": link,
                        "source": "arxiv",
                        "type": "paper",
                    })
                except Exception:
                    continue
            if len(results) >= 3:
                break
        except Exception as e:
            print("arxiv: " + str(e))
    return results


def search_zenodo(topic_en, limit=10):
    results = []
    queries = ['"' + topic_en + '"', topic_en]
    for q in queries:
        try:
            r = requests.get(
                "https://zenodo.org/api/records",
                params={
                    "q": q,
                    "size": limit,
                    "file_type": "pdf",
                },
                timeout=20,
            )
            r.raise_for_status()
            hits = r.json().get("hits", {}).get("hits", [])
            for hit in hits:
                try:
                    meta = hit.get("metadata", {})
                    title = meta.get("title", "?")
                    desc = (meta.get("description") or "")
                    desc = desc[:600]
                    rec_id = hit.get("id")
                    pdf_url = None
                    size = 0
                    for f in hit.get("files", []):
                        key = f.get("key", "").lower()
                        if key.endswith(".pdf"):
                            pdf_url = f["links"]["self"]
                            size = f.get("size", 0)
                            break
                    if not pdf_url:
                        continue
                    if size / 1024 / 1024 > 100:
                        continue
                    dup = False
                    for x in results:
                        if x["url"] == pdf_url:
                            dup = True
                            break
                    if dup:
                        continue
                    results.append({
                        "title": title,
                        "summary": desc,
                        "url": pdf_url,
                        "page_url": "https://zenodo.org/records/"
                                    + str(rec_id),
                        "source": "zenodo",
                        "type": "book",
                        "size_mb": round(
                            size / 1024 / 1024, 1,
                        ),
                    })
                except Exception:
                    continue
            if len(results) >= 3:
                break
        except Exception as e:
            print("zenodo: " + str(e))
    return results


def search_semantic(topic_en, limit=10):
    results = []
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={
                "query": topic_en,
                "limit": limit,
                "fields": "title,abstract,openAccessPdf",
            },
            timeout=20,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            pdf = item.get("openAccessPdf")
            if not pdf or not pdf.get("url"):
                continue
            results.append({
                "title": item.get("title", "?"),
                "summary": (item.get("abstract") or "")[:600],
                "url": pdf["url"],
                "source": "semantic_scholar",
                "type": "paper",
            })
    except Exception as e:
        print("ss: " + str(e))
    return results


# ============================================================
# RELEVANCE
# ============================================================
def _words_present(text, words):
    hits = 0
    for w in words:
        pat = r"\b" + re.escape(w) + r"\b"
        if re.search(pat, text):
            hits += 1
    return hits


def relevance_score(item, topic_en):
    text = item.get("title", "") + " "
    text += item.get("summary", "")
    text = text.lower()
    phrase = topic_en.lower().strip()
    if phrase and phrase in text:
        return 1.0
    words = [w for w in phrase.split() if len(w) > 2]
    distinctive = [w for w in words if len(w) >= 6]
    if distinctive:
        hits = _words_present(text, distinctive)
        if hits == 0:
            return 0.0
        return hits / len(distinctive)
    if words:
        hits = _words_present(text, words)
        if hits == len(words):
            return 1.0
        return 0.0
    return 0.0


# ============================================================
# КАРТОЧКА
# ============================================================
def format_card(i, item, total):
    title = clean_text(item.get("title", "?"))
    title = html.escape(title)
    source = html.escape(item.get("source", "?"))
    size = item.get("size_mb")
    size_str = ""
    if size:
        size_str = " (" + str(size) + " МБ)"

    lines = []
    lines.append("<b>" + str(i) + "/" + str(total) + "</b>")
    lines.append("<b>" + title + "</b>")
    lines.append("")

    src_line = "📡 " + source + size_str
    lines.append(src_line)
    lines.append("")

    summary = clean_text(item.get("summary", ""))
    if summary:
        short = summary[:280]
        if len(summary) > 280:
            short += "..."
        lines.append("<i>" + html.escape(short) + "</i>")
        lines.append("")

    page = item.get("page_url", item.get("url", ""))
    lines.append('<a href="' + page + '">Открыть источник</a>')

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: personal_finder.py <topic>")
        sys.exit(1)

    topic = " ".join(sys.argv[1:]).strip()
    print("Search: " + topic)

    _load_models()

    topic_en = ensure_english(topic)
    print("EN topic: " + topic_en)
    print("=" * 60)

    all_items = []
    for name, fn in [
        ("arXiv", search_arxiv),
        ("Zenodo", search_zenodo),
        ("Semantic Scholar", search_semantic),
    ]:
        print(name + "...")
        res = fn(topic_en, limit=10)
        print("  found: " + str(len(res)))
        all_items.extend(res)

    if not all_items:
        txt = "🔍 Личный поиск: " + html.escape(topic)
        txt += "\n\nНичего не найдено."
        notify_with_buttons(txt)
        return

    # Фильтр
    for it in all_items:
        it["score"] = relevance_score(it, topic_en)
    scored = [x for x in all_items if x["score"] > 0]
    scored.sort(key=lambda x: x["score"], reverse=True)

    print("After filter: " + str(len(scored)))
    print("=" * 60)

    if not scored:
        txt = "🔍 Личный поиск: " + html.escape(topic)
        txt += "\n\nТочных совпадений нет.\n"
        txt += "Попробуй английское название."
        notify_with_buttons(txt)
        return

    # Перевод
    to_show = scored[:MAX_CARDS]
    print("Translating " + str(len(to_show)) + " items...")
    for it in to_show:
        orig = it["title"]
        it["title"] = ensure_russian(orig)
        if it["title"] != orig:
            it["title_original"] = orig
        s = it.get("summary", "")
        if s:
            it["summary"] = ensure_russian(s[:400])

    # Сохраняем всё
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = {
        "topic": topic,
        "topic_en": topic_en,
        "generated_at": datetime.now(
            timezone.utc,
        ).isoformat(),
        "total": len(scored),
        "offset": PAGE_SIZE,
        "items": scored,
    }
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(
            candidates, f,
            ensure_ascii=False, indent=2,
        )

    # Первое сообщение - заголовок
    total = len(scored)
    header = "🔍 <b>Личный поиск:</b> "
    header += html.escape(topic) + "\n\n"
    header += "Найдено: <b>" + str(total) + "</b> материалов\n"
    header += "Покажу первые " + str(PAGE_SIZE)
    header += " по одному:"
    notify_with_buttons(header)

    # Карточки по одной
    for i in range(PAGE_SIZE):
        if i >= len(to_show):
            break
        item = to_show[i]
        card_text = format_card(i + 1, item, PAGE_SIZE)
        kb = {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Скачать",
                        "callback_data": "personal_dl:"
                                         + str(i),
                    },
                    {
                        "text": "❌ Отклонить",
                        "callback_data": "personal_reject:"
                                         + str(i),
                    },
                ],
            ],
        }
        notify_with_buttons(card_text, kb)
        print("Card sent: " + str(i + 1))

    # Кнопка "ещё"
    if len(scored) > PAGE_SIZE:
        left = len(scored) - PAGE_SIZE
        kb = {
            "inline_keyboard": [
                [
                    {
                        "text": "Показать ещё " + str(left),
                        "callback_data": "personal_next:0",
                    },
                ],
            ],
        }
        notify_with_buttons(
            "Всего найдено: " + str(total),
            kb,
        )

    print("Done")


if __name__ == "__main__":
    main()