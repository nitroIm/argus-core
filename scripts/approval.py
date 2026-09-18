# ============================================================
# ARGUS — ОТПРАВКА КАРТОЧЕК НА ОДОБРЕНИЕ
# v3: с памятью (sent_ids) + чёрный список источников
# ============================================================

import os
import json
import hashlib
import requests
from datetime import datetime

CANDIDATES_FILE = "data/scout_candidates.json"
SENT_IDS_FILE = "data/sent_ids.json"
PENDING_FILE = "data/pending_cards.json"

MAX_CARDS = 5  # не больше 5 карточек за один прогон

# ---------- Чёрный список доменов ----------
# Ссылки с этих доменов не отправляются вообще
BLACKLIST = [
    "archive.org",   # 401 Unauthorized (lending library)
    "sci-hub",       # серые зоны
]

def in_blacklist(url: str) -> bool:
    u = url.lower()
    return any(domain in u for domain in BLACKLIST)

# ---------- Загрузка кандидатов ----------
if not os.path.exists(CANDIDATES_FILE):
    print("❌ Нет файла кандидатов.")
    exit(0)

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

candidates = data.get("candidates", [])

# ---------- Память отправленного ----------
sent_ids = set()
if os.path.exists(SENT_IDS_FILE):
    try:
        with open(SENT_IDS_FILE, "r", encoding="utf-8") as f:
            sent_ids = set(json.load(f))
    except Exception:
        sent_ids = set()

# ---------- Pending ----------
pending = {}
if os.path.exists(PENDING_FILE):
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception:
        pending = {}

# ---------- Настройки Telegram ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
    exit(1)

# ---------- Стабильный id по URL ----------
def url_id(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

# ---------- Отбор новых ----------
def is_new(c: dict) -> bool:
    url = c.get("url", "")
    if not url:
        return False
    if in_blacklist(url):
        return False
    return url_id(url) not in sent_ids

new_candidates = [c for c in candidates if is_new(c)]
skipped_blacklist = [c for c in candidates if c.get("url") and in_blacklist(c["url"])]
print(f"ℹ️ Всего кандидатов: {len(candidates)}, новых: {len(new_candidates)}, "
      f"в чёрном списке: {len(skipped_blacklist)}")

if not new_candidates:
    print("ℹ️ Новых кандидатов нет.")
    exit(0)

# ---------- Отправка ----------
sent = 0
for i, c in enumerate(new_candidates[:MAX_CARDS]):
    title = c.get("title", "?")
    url = c.get("url", "")
    source = c.get("source", "?")
    topic = c.get("topic", "?")
    size = c.get("size_mb")

    short_id = url_id(url)

    message = f"📚 <b>Найдена книга</b>\n\n"
    message += f"<b>{title}</b>\n\n"
    message += f"📡 Источник: {source}\n"
    message += f"🏷 Тема: {topic}\n"
    if size:
        message += f"💾 Размер: {size} МБ\n"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Скачать", "callback_data": f"approve:{short_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject:{short_id}"}
            ]
        ]
    }

    pending[short_id] = {
        "url": url,
        "title": title,
        "sent_at": datetime.utcnow().isoformat(),
    }

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            print(f"✅ Отправлено: {title[:60]}")
            sent_ids.add(short_id)
            sent += 1
        else:
            print(f"⚠️ Ошибка Telegram: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ {e}")

# ---------- Сохраняем ----------
with open(SENT_IDS_FILE, "w", encoding="utf-8") as f:
    json.dump(list(sent_ids), f, ensure_ascii=False, indent=2)

with open(PENDING_FILE, "w", encoding="utf-8") as f:
    json.dump(pending, f, ensure_ascii=False, indent=2)

print(f"\n📤 Отправлено карточек: {sent} (из {len(new_candidates)} новых)")