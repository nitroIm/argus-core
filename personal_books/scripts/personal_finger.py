# ============================================================
# ARGUS — ЛИЧНЫЙ ПОИСК КНИГ (v1)
# Ищет книги по теме, переводит заголовки, присылает в Telegram
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


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception:
        pass


# ============================================================
# ПОИСК ПО ИСТОЧНИКАМ
# ============================================================
def search_arxiv(topic, limit=5):
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
                
                # Извлекаем аннотацию (summary)
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


def search_zenodo(topic, limit=5):
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
                
                if pdf_url and size / 1024 / 1024 < 50:  # Не больше 50 МБ
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


def search_semantic_scholar(topic, limit=5):
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
# ПЕРЕВОД ЗАГОЛОВКОВ
# ============================================================
def translate_item(item):
    """Переводит заголовок и аннотацию, если они на английском."""
    title = item.get("title", "")
    summary = item.get("summary", "")
    
    if is_english(title):
        item["title_original"] = title
        item["title"] = translate_to_ru(title) or title
    
    if summary and is_english(summary):
        item["summary_original"] = summary
        # Переводим только первые 300 символов для экономии времени
        item["summary"] = translate_to_ru(summary[:300]) or summary
    
    return item


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
    
    # Ищем по всем источникам
    all_items = []
    
    for source_name, search_fn in [
        ("arXiv", search_arxiv),
        ("Zenodo", search_zenodo),
        ("Semantic Scholar", search_semantic_scholar),
    ]:
        print(f"\n📡 {source_name}...")
        results = search_fn(topic, limit=5)
        print(f"   Найдено: {len(results)}")
        all_items.extend(results)
    
    if not all_items:
        notify(f"🔍 <b>Личный поиск:</b> '{topic}'\n\n❌ Ничего не найдено.")
        print("\n❌ Ничего не найдено.")
        return
    
    # Переводим заголовки
    print(f"\n🌐 Переводим {len(all_items)} заголовков...")
    for item in all_items:
        translate_item(item)
    
    # Сохраняем в JSON
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    candidates = {
        "topic": topic,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(all_items),
        "items": all_items,
    }
    
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    
    # Формируем сообщение для Telegram
    msg_lines = [f"🔍 <b>Личный поиск:</b> <i>{topic}</i>\n"]
    msg_lines.append(f"📚 Найдено материалов: <b>{len(all_items)}</b>\n")
    
    for i, item in enumerate(all_items[:10], 1):  # Максимум 10 карточек
        title = item.get("title", "Без названия")
        source = item.get("source", "?")
        size = item.get("size_mb")
        size_str = f" ({size} МБ)" if size else ""
        
        msg_lines.append(f"\n<b>{i}.</b> {title}")
        msg_lines.append(f"   📡 {source}{size_str}")
        
        summary = item.get("summary", "")
        if summary:
            msg_lines.append(f"   <i>{summary[:200]}{'...' if len(summary) > 200 else ''}</i>")
        
        msg_lines.append(f"   🔗 {item.get('page_url', item.get('url', ''))}")
    
    msg_lines.append("\n💡 Для скачивания нажми кнопку ниже.")
    
    # Кнопки для скачивания (до 5 штук в первом ряду)
    keyboard = []
    for i, item in enumerate(all_items[:5], 1):
        keyboard.append([{
            "text": f"📥 {i}. Скачать",
            "callback_data": f"personal_dl:{i-1}"
        }])
    
    # Отправляем сообщение с кнопками
    if BOT_TOKEN and CHAT_ID:
        try:
            payload = {
                "chat_id": CHAT_ID,
                "text": "\n".join(msg_lines)[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": keyboard}
            }
            r = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=15,
            )
            if r.status_code == 200:
                print("✅ Отправлено в Telegram")
            else:
                print(f"⚠️ Telegram: {r.status_code} {r.text[:200]}")
        except Exception as e:
            print(f"⚠️ Ошибка отправки: {e}")
    
    print("\n" + "=" * 60)
    print(f"✅ Найдено: {len(all_items)} материалов")
    print(f"💾 Сохранено в: {CANDIDATES_FILE}")


if __name__ == "__main__":
    main()
