# ============================================================
# ARGUS — ОТПРАВКА КАРТОЧЕК НА ОДОБРЕНИЕ
# С инлайн-кнопками в Telegram
# ============================================================

import os
import json
import requests
from datetime import datetime

CANDIDATES_FILE = "data/scout_candidates.json"

if not os.path.exists(CANDIDATES_FILE):
    print("❌ Нет кандидатов.")
    exit(0)

with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

candidates = data.get("candidates", [])

if not candidates:
    print("ℹ️ Новых кандидатов нет.")
    exit(0)

# ---------- Настройки ----------
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not BOT_TOKEN or not CHAT_ID:
    print("❌ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
    exit(1)

# ---------- Отправляем по одному ----------
# Ограничение: не больше 5 карточек за раз
MAX_CARDS = 5

sent = 0

for i, c in enumerate(candidates[:MAX_CARDS]):
    title = c.get("title", "?")
    url = c.get("url", "")
    source = c.get("source", "?")
    topic = c.get("topic", "?")
    size = c.get("size_mb")

    # Короткое имя для callback
    short_id = f"s{i}_{int(datetime.utcnow().timestamp())}"

    message = f"📚 <b>Найдена книга #{i+1}</b>\n\n"
    message += f"<b>{title}</b>\n\n"
    message += f"📡 Источник: {source}\n"
    message += f"🏷 Тема: {topic}\n"
    if size:
        message += f"💾 Размер: {size} МБ\n"

    # Inline-клавиатура
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Скачать", "callback_data": f"approve_{short_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject_{short_id}"}
            ]
        ]
    }

    # Сохраняем mapping short_id → url
    if "pending" not in data:
        data["pending"] = {}
    data["pending"][short_id] = {"url": url, "title": title}

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "reply_markup": keyboard,
                "disable_web_page_preview": True
            },
            timeout=15
        )
        if r.status_code == 200:
            print(f"✅ Отправлено: {title[:50]}")
            sent += 1
        else:
            print(f"⚠️ Ошибка: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ {e}")

# ---------- Сохраняем обновлённый файл ----------
with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"\n📤 Отправлено карточек: {sent}")