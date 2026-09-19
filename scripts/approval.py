# ============================================================
# ARGUS — ОТПРАВКА КАРТОЧЕК НА ОДОБРЕНИЕ (v4)
# v4: pathlib, фикс sys.exit, timezone, защита от дублей
# ============================================================

import sys
import json
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
SENT_IDS_FILE = DATA_DIR / "sent_ids.json"
PENDING_FILE = DATA_DIR / "pending_cards.json"

MAX_CARDS = 5  # не больше 5 карточек за один прогон

# ---------- Чёрный список доменов ----------
BLACKLIST = [
    "archive.org",
    "sci-hub",
]

def in_blacklist(url: str) -> bool:
    u = url.lower()
    # Простая, но эффективная проверка подстроки
    return any(domain in u for domain in BLACKLIST)

# ---------- Проверка файлов ----------
if not CANDIDATES_FILE.exists():
    print("❌ Нет файла кандидатов (scout_candidates.json).")
    sys.exit(0)

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

candidates = data.get("candidates", [])

# ---------- Память отправленного ----------
sent_ids = set()
if SENT_IDS_FILE.exists():
    try:
        with open(SENT_IDS_FILE, "r", encoding="utf-8") as f:
            sent_ids = set(json.load(f))
    except Exception:
        sent_ids = set()

# ---------- Pending ----------
pending = {}
if PENDING_FILE.exists():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception:
        pending = {}

# ---------- Настройки Telegram ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID в окружении")
    sys.exit(1)

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

print(f"ℹ️ Всего кандидатов: {len(candidates)}")
print(f"ℹ️ Новых для отправки: {len(new_candidates)}")
print(f"ℹ️ В чёрном списке: {len(skipped_blacklist)}")

if not new_candidates:
    print("✅ Новых кандидатов нет. Выход.")
    sys.exit(0)

# ---------- Отправка ----------
sent_count = 0
for c in new_candidates[:MAX_CARDS]:
    title = c.get("title", "?")
    url = c.get("url", "")
    source = c.get("source", "?")
    topic = c.get("topic", "?")
    size = c.get("size_mb")

    short_id = url_id(url)

    message = (
        f"📚 <b>Найдена книга</b>\n\n"
        f"<b>{title}</b>\n\n"
        f"📡 Источник: {source}\n"
        f"🏷 Тема: {topic}\n"
    )
    if size:
        message += f"💾 Размер: {size} МБ\n"
    
    message += f"\n<a href=\"{url}\">Открыть ссылку</a>"

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
        "sent_at": datetime.now(timezone.utc).isoformat(),
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
            sent_count += 1
        else:
            print(f"⚠️ Ошибка Telegram: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ Исключение при отправке: {e}")

# ---------- Сохраняем состояние ----------
# ВАЖНО: Если этот скрипт работает в GitHub Actions, 
# workflow ДОЛЖЕН сделать git add/commit/push для SENT_IDS_FILE и PENDING_FILE,
# иначе при следующем запуске память обнулится и придут дубли!

DATA_DIR.mkdir(parents=True, exist_ok=True)

with open(SENT_IDS_FILE, "w", encoding="utf-8") as f:
    json.dump(list(sent_ids), f, ensure_ascii=False, indent=2)

with open(PENDING_FILE, "w", encoding="utf-8") as f:
    json.dump(pending, f, ensure_ascii=False, indent=2)

print(f"\n📤 Итого отправлено карточек: {sent_count}")
