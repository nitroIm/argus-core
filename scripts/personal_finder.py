# ============================================================
# ARGUS — ЛИЧНЫЙ ПОИСК КНИГ (v2)
# v2: В Telegram шлём только топ-5, остальное сохраняем для /findnext
# ============================================================

import os
import sys
import json
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

# --- Переводчик ---
try:
    from translate import translate_to_ru, is_english
    TRANSLATE_AVAILABLE = True
except Exception:
    TRANSLATE_AVAILABLE = False
    def translate_to_ru(t): return t
    def is_english(t): return False

# Сколько карточек показываем за раз
PAGE_SIZE = 5


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
def search_arxiv(topic, limit=10):
    results = []
    try:
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{topic}", "start": 0,
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
                    summary = " ".join(summary.split())[:500]
                except Exception:
                    pass
                
                results.append({
                    "title": title,
                    "summary": summary,
                    "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "page_url": link,
                    "source": "arxiv",
                    "topic": topic,
                    "type": "paper",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ arXiv: {e}")
    return results


def search_zenodo(topic, limit=10):
    results = []
    try:
        r = requests.get(
            "https://zenodo.org/api/records",
            params={"q": topic, "size": limit, "type": "publication", "file_type": "pdf"},
            timeout=20,
        )
        r.raise_for_status()
        for hit in r.json().get("hits", {}).get("hits", []):
            try:
                title = hit.get("metadata", {}).get("title", "?")
                description = hit.get("metadata", {}).get("description", "")[:500]
                record_id = hit.get("id")
                
                pdf_url = None
                size = 0
                for f in hit.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdf_url = f["links"]["self"]
                        size = f.get("size", 0)
                        break
                
                if pdf_url and size / 1024 / 1024 < 50:
                    results.append({
                        "title": title,
                        "summary": description,
                        "url": pdf_url,
                        "page_url": f"https://zenodo.org/records/{record_id}",
                        "source": "zenodo",
                        "topic": topic,
                        "type": "book",
                        "size_mb": round(size / 1024 / 1024, 1),
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"⚠️ Zenodo: {e}")
    return results


def search_semantic_scholar(topic, limit=10):
    results = []
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": topic, "limit": limit,
                    "fields": "title,abstract,openAccessPdf"},
            timeout=20,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            pdf = item.get("openAccessPdf")
            if pdf and pdf.get("url"):
                results.append({
                    "title": item.get("title", "?"),
                    "summary": (item.get("abstract") or "")[:500],
                    "url": pdf["url"],
                    "source": "semantic_scholar",
                    "topic": topic,
                    "type": "paper",
                })
    except Exception as e:
        print(f"⚠️ Semantic Scholar: {e}")
    return results


# ============================================================
# ПЕРЕВОД
# ============================================================
def translate_item(item):
    title = item.get("title", "")
    summary = item.get("summary", "")
    
    if is_english(title):
        item["title_original"] = title
        item["title"] = translate_to_ru(title) or title
    
    if summary and is_english(summary):
        item["summary_original"] = summary
        item["summary"] = translate_to_ru(summary[:300]) or summary
    
    return item


# ============================================================
# ФОРМИРОВАНИЕ КАРТОЧКИ
# ============================================================
def format_card(i: int, item: dict) -> str:
    """Форматирует одну карточку материала."""
    title = item.get("title", "Без названия")
    source = item.get("source", "?")
    size = item.get("size_mb")
    size_str = f" ({size} МБ)" if size else ""
    
    lines = [f"<b>{i}.</b> {title}"]
    lines.append(f"   📡 {source}{size_str}")
    
    summary = item.get("summary", "")
    if summary:
        lines.append(f"   <i>{summary[:200]}{'...' if len(summary) > 200 else ''}</i>")
    
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
    print(f"🌐 Переводчик: {'активен' if TRANSLATE_AVAILABLE else 'НЕДОСТУПЕН'}")
    print("=" * 60)
    
    # Ищем по всем источникам (больше, чем раньше, чтобы было что пагинировать)
    all_items = []
    for source_name, search_fn in [
        ("arXiv", search_arxiv),
        ("Zenodo", search_zenodo),
        ("Semantic Scholar", search_semantic_scholar),
    ]:
        print(f"\n📡 {source_name}...")
        results = search_fn(topic, limit=10)
        print(f"   Найдено: {len(results)}")
        all_items.extend(results)
    
    if not all_items:
        notify(f"🔍 <b>Личный поиск:</b> '{topic}'\n\n❌ Ничего не найдено.")
        print("\n❌ Ничего не найдено.")
        return
    
    # Переводим все заголовки
    print(f"\n🌐 Переводим {len(all_items)} заголовков...")
    for item in all_items:
        translate_item(item)
    
    # Сохраняем ВСЁ в JSON с offset = 0 (показали первую страницу)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = {
        "topic": topic,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_items),
        "offset": PAGE_SIZE,  # Уже показали первые PAGE_SIZE
        "items": all_items,
    }
    
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    
    # Формируем сообщение ТОЛЬКО с первыми PAGE_SIZE карточками
    first_page = all_items[:PAGE_SIZE]
    
    msg_lines = [f"🔍 <b>Личный поиск:</b> <i>{topic}</i>\n"]
    msg_lines.append(f"📚 Найдено материалов: <b>{len(all_items)}</b> (показано {len(first_page)})\n")
    
    for i, item in enumerate(first_page, 1):
        msg_lines.append(format_card(i, item))
        msg_lines.append("")
    
    # Кнопки: скачать (топ-5) + "Показать ещё" если есть что
    keyboard_buttons = []
    for i in range(len(first_page)):
        keyboard_buttons.append([{
            "text": f"📥 {i+1}. Скачать",
            "callback_data": f"personal_dl:{i}"
        }])
    
    if len(all_items) > PAGE_SIZE:
        keyboard_buttons.append([{
            "text": f"Показать ещё ▶ ({len(all_items) - PAGE_SIZE} осталось)",
            "callback_data": "personal_next:0"
        }])
    
    notify("\n".join(msg_lines), {"inline_keyboard": keyboard_buttons})
    
    print("\n" + "=" * 60)
    print(f"✅ Найдено всего: {len(all_items)}")
    print(f"📤 Отправлено в Telegram: {len(first_page)}")
    print(f"💾 Сохранено в: {CANDIDATES_FILE}")


if __name__ == "__main__":
    main()
