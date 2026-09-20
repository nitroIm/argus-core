# ============================================================
# ARGUS — ЛИЧНЫЙ ПОИСК КНИГ (v5 — финал)
# v5: честный фильтр релевантности (границы слов + характерные слова)
# ============================================================

import os
import sys
import json
import html
import re
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CANDIDATES_FILE = DATA_DIR / "personal_candidates.json"

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Настройки выдачи ---
PAGE_SIZE = 5
MAX_CARDS = 30

# --- Модели перевода ---
TRANSLATE_AVAILABLE = False
_model_en_ru = None
_model_ru_en = None
_tok_en_ru = None
_tok_ru_en = None


def _load_models():
    global TRANSLATE_AVAILABLE, _model_en_ru, _model_ru_en, _tok_en_ru, _tok_ru_en
    try:
        from transformers import MarianMTModel, MarianTokenizer
        print("🌐 Загрузка моделей перевода...")
        _tok_ru_en = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-ru-en")
        _model_ru_en = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-ru-en")
        _tok_en_ru = MarianTokenizer.from_pretrained("Helsinki-NLP/opus-mt-en-ru")
        _model_en_ru = MarianMTModel.from_pretrained("Helsinki-NLP/opus-mt-en-ru")
        TRANSLATE_AVAILABLE = True
        print("✅ Модели перевода загружены")
    except Exception as e:
        print(f"⚠️ Переводчик недоступен: {e}")


def translate_to_en(text: str) -> str:
    if not TRANSLATE_AVAILABLE or not text:
        return text
    try:
        batch = _tok_ru_en([text], return_tensors="pt", padding=True, truncation=True, max_length=128)
        out = _model_ru_en.generate(**batch)
        return _tok_ru_en.decode(out[0], skip_special_tokens=True)
    except Exception:
        return text


def translate_to_ru(text: str) -> str:
    if not TRANSLATE_AVAILABLE or not text:
        return text
    try:
        batch = _tok_en_ru([text], return_tensors="pt", padding=True, truncation=True, max_length=512)
        out = _model_en_ru.generate(**batch)
        return _tok_en_ru.decode(out[0], skip_special_tokens=True)
    except Exception:
        return text


# ============================================================
# ПРОВЕРКА ЯЗЫКА: ДВА ВАРИАНТА
# ============================================================
def is_cyrillic(text: str) -> bool:
    return any('а' <= c.lower() <= 'я' or c.lower() == 'ё' for c in text)


def ensure_russian(text: str) -> str:
    if not text:
        return text
    if is_cyrillic(text):
        return text
    return translate_to_ru(text) or text


def ensure_english(text: str) -> str:
    if not text:
        return text
    if not is_cyrillic(text):
        return text
    return translate_to_en(text) or text


def notify(text: str, keyboard=None):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        payload = {
            "chat_id": CHAT_ID,
            "text": text[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard:
            payload["reply_markup"] = keyboard
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
    except Exception:
        pass


# ============================================================
# ПОИСК ПО ИСТОЧНИКАМ
# ============================================================
def search_arxiv(topic_en: str, limit: int = 10):
    results = []
    queries = [f'all:"{topic_en}"', f'all:{topic_en}']
    for q in queries:
        try:
            r = requests.get(
                "http://export.arxiv.org/api/query",
                params={"search_query": q, "start": 0,
                        "max_results": limit, "sortBy": "relevance"},
                timeout=20,
            )
            r.raise_for_status()
            for entry in r.text.split("<entry>")[1:]:
                try:
                    title = entry.split("<title>")[1].split("</title>")[0].strip()
                    title = " ".join(title.split())
                    link = entry.split("<id>")[1].split("</id>")[0].strip()
                    arxiv_id = link.split("/abs/")[-1]
                    summary = ""
                    try:
                        summary = entry.split("<summary>")[1].split("</summary>")[0].strip()
                        summary = " ".join(summary.split())[:600]
                    except Exception:
                        pass
                    if any(x["url"].endswith(arxiv_id + ".pdf") for x in results):
                        continue
                    results.append({
                        "title": title,
                        "summary": summary,
                        "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                        "page_url": link,
                        "source": "arxiv",
                        "type": "paper",
                    })
                except Exception:
                    continue
            if len(results) >= 3:
                break
        except Exception as e:
            print(f"⚠️ arXiv: {e}")
    return results


def search_zenodo(topic_en: str, limit: int = 10):
    results = []
    queries = [f'"{topic_en}"', topic_en]
    for q in queries:
        try:
            r = requests.get(
                "https://zenodo.org/api/records",
                params={"q": q, "size": limit, "file_type": "pdf"},
                timeout=20,
            )
            r.raise_for_status()
            for hit in r.json().get("hits", {}).get("hits", []):
                try:
                    title = hit.get("metadata", {}).get("title", "?")
                    description = (hit.get("metadata", {}).get("description") or "")[:600]
                    record_id = hit.get("id")
                    pdf_url = None
                    size = 0
                    for f in hit.get("files", []):
                        if f.get("key", "").lower().endswith(".pdf"):
                            pdf_url = f["links"]["self"]
                            size = f.get("size", 0)
                            break
                    if pdf_url and size / 1024 / 1024 < 100:
                        if any(x["url"] == pdf_url for x in results):
                            continue
                        results.append({
                            "title": title,
                            "summary": description,
                            "url": pdf_url,
                            "page_url": f"https://zenodo.org/records/{record_id}",
                            "source": "zenodo",
                            "type": "book",
                            "size_mb": round(size / 1024 / 1024, 1),
                        })
                except Exception:
                    continue
            if len(results) >= 3:
                break
        except Exception as e:
            print(f"⚠️ Zenodo: {e}")
    return results


def search_semantic_scholar(topic_en: str, limit: int = 10):
    results = []
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": topic_en, "limit": limit,
                    "fields": "title,abstract,openAccessPdf"},
            timeout=20,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            pdf = item.get("openAccessPdf")
            if pdf and pdf.get("url"):
                results.append({
                    "title": item.get("title", "?"),
                    "summary": (item.get("abstract") or "")[:600],
                    "url": pdf["url"],
                    "source": "semantic_scholar",
                    "type": "paper",
                })
    except Exception as e:
        print(f"⚠️ Semantic Scholar: {e}")
    return results


# ============================================================
# ФИЛЬТР РЕЛЕВАНТНОСТИ (v5: границы слов + характерные слова)
# ============================================================
def _words_present(text: str, words: list) -> int:
    hits = 0
    for w in words:
        if re.search(r"\b" + re.escape(w) + r"\b", text):
            hits += 1
    return hits


def relevance_score(item: dict, topic_en: str) -> float:
    """
    Правила:
    1. Фраза темы целиком в тексте      -> 1.0
    2. Есть характерные слова (6+ букв) -> доля совпавших
    3. Тема только из коротких слов     -> нужно ВСЕ слова
    Иначе 0.0 — карточка отбрасывается.
    """
    text = (item.get("title", "") + " " + item.get("summary", "")).lower()
    phrase = topic_en.lower().strip()

    if phrase and phrase in text:
        return 1.0

    words_all = [w for w in phrase.split() if len(w) > 2]
    distinctive = [w for w in words_all if len(w) >= 6]

    if distinctive:
        hits = _words_present(text, distinctive)
        return hits / len(distinctive) if hits > 0 else 0.0

    if words_all:
        hits = _words_present(text, words_all)
        return 1.0 if hits == len(words_all) else 0.0

    return 0.0


# ============================================================
# ФОРМАТ КАРТОЧКИ (HTML-escape)
# ============================================================
def format_card(i: int, item: dict) -> str:
    title = html.escape(item.get("title", "Без названия"))
    source = html.escape(item.get("source", "?"))
    size = item.get("size_mb")
    size_str = f" ({size} МБ)" if size else ""

    lines = [f"<b>{i}.</b> {title}"]
    lines.append(f"   📡 {source}{size_str}")

    summary = item.get("summary", "")
    if summary:
        short = summary[:200]
        lines.append(f"   <i>{html.escape(short)}{'...' if len(summary) > 200 else ''}</i>")

    lines.append(f"   🔗 {item.get('page_url', item.get('url', ''))}")
    return "\n".join(lines)


# ============================================================
# ОСНОВНОЕ
# ============================================================
def main():
    if len(sys.argv) < 2:
        print("Использование: python personal_finder.py <тема>")
        sys.exit(1)

    topic = " ".join(sys.argv[1:]).strip()
    print(f"🔍 Личный поиск: '{topic}'")

    _load_models()

    topic_en = ensure_english(topic)
    print(f"🌐 Тема для поиска (EN): '{topic_en}'")
    print("=" * 60)

    all_items = []
    for name, fn in [("arXiv", search_arxiv), ("Zenodo", search_zenodo),
                     ("Semantic Scholar", search_semantic_scholar)]:
        print(f"\n📡 {name}...")
        res = fn(topic_en, limit=10)
        print(f"   Найдено: {len(res)}")
        all_items.extend(res)

    if not all_items:
        notify(f"🔍 <b>Личный поиск:</b> {html.escape(topic)}\n\n❌ Ничего не найдено.")
        print("\n❌ Ничего не найдено.")
        return

    # Фильтр релевантности
    for item in all_items:
        item["score"] = relevance_score(item, topic_en)
    scored = [x for x in all_items if x["score"] > 0]
    scored.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n🎯 После фильтра релевантности: {len(scored)} из {len(all_items)}")

    if not scored:
        notify(
            f"🔍 <b>Личный поиск:</b> {html.escape(topic)}\n\n"
            f"❌ Точных совпадений по теме не найдено.\n"
            f"💡 Попробуй английское название (например: <i>Marcus Aurelius</i>) "
            f"или более узкую тему."
        )
        return

    # Перевод карточек: EN -> RU, RU не трогаем
    to_translate = scored[:MAX_CARDS]
    print(f"🌐 Проверяем и переводим {len(to_translate)} карточек...")
    for item in to_translate:
        orig_title = item["title"]
        item["title"] = ensure_russian(orig_title)
        if item["title"] != orig_title:
            item["title_original"] = orig_title
        orig_sum = item.get("summary", "")
        if orig_sum:
            item["summary"] = ensure_russian(orig_sum[:300])

    # Сохраняем всё найденное
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = {
        "topic": topic,
        "topic_en": topic_en,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(scored),
        "offset": PAGE_SIZE,
        "items": scored,
    }
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)

    # Сообщение: только первые 5
    first_page = to_translate[:PAGE_SIZE]
    msg_lines = [f"🔍 <b>Личный поиск:</b> <i>{html.escape(topic)}</i>\n"]
    msg_lines.append(f"📚 Релевантных материалов: <b>{len(scored)}</b> (показано {len(first_page)})\n")

    for i, item in enumerate(first_page, 1):
        msg_lines.append(format_card(i, item))
        msg_lines.append("")

    keyboard_buttons = []
    for i in range(len(first_page)):
        keyboard_buttons.append([{
            "text": f"📥 {i+1}. Скачать",
            "callback_data": f"personal_dl:{i}"
        }])
    if len(scored) > PAGE_SIZE:
        keyboard_buttons.append([{
            "text": f"Показать ещё ▶ ({len(scored) - PAGE_SIZE} осталось)",
            "callback_data": "personal_next:0"
        }])

    notify("\n".join(msg_lines), {"inline_keyboard": keyboard_buttons})

    print("\n" + "=" * 60)
    print(f"✅ Релевантных: {len(scored)}, показано: {len(first_page)}")


if __name__ == "__main__":
    main()
