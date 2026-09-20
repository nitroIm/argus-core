# ============================================================
# ARGUS — ПОКАЗАТЬ СЛЕДУЮЩИЕ КАРТОЧКИ (v1)
# Читает personal_candidates.json и шлёт следующую порцию
# ============================================================

import os
import sys
import json
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CANDIDATES_FILE = DATA_DIR / "personal_candidates.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

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


def format_card(i: int, item: dict) -> str:
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


def main():
    if not CANDIDATES_FILE.exists():
        notify("❌ Нет сохранённых результатов. Сначала используй /find <тема>")
        sys.exit(1)
    
    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    
    items = candidates.get("items", [])
    offset = candidates.get("offset", 0)
    topic = candidates.get("topic", "?")
    
    if offset >= len(items):
        notify(f"📚 <b>{topic}</b>\n\n🏁 Все материалы уже показаны.")
        return
    
    # Берём следующую порцию
    next_page = items[offset:offset + PAGE_SIZE]
    remaining = len(items) - (offset + len(next_page))
    
    msg_lines = [f"🔍 <b>Продолжение:</b> <i>{topic}</i>\n"]
    msg_lines.append(f"📖 Показаны {offset+1}–{offset+len(next_page)} из {len(items)}\n")
    
    for i, item in enumerate(next_page, start=offset + 1):
        msg_lines.append(format_card(i, item))
        msg_lines.append("")
    
    # Кнопки
    keyboard_buttons = []
    for i, _ in enumerate(next_page):
        global_idx = offset + i
        keyboard_buttons.append([{
            "text": f"📥 {global_idx+1}. Скачать",
            "callback_data": f"personal_dl:{global_idx}"
        }])
    
    if remaining > 0:
        keyboard_buttons.append([{
            "text": f"Показать ещё ▶ ({remaining} осталось)",
            "callback_data": f"personal_next:{offset + len(next_page)}"
        }])
    
    # Обновляем offset в JSON
    candidates["offset"] = offset + len(next_page)
    with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=2)
    
    notify("\n".join(msg_lines), {"inline_keyboard": keyboard_buttons})
    print(f"✅ Показано {len(next_page)} материалов (offset: {offset} → {candidates['offset']})")


if __name__ == "__main__":
    main()
