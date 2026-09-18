# ============================================================
# ARGUS — ОТЧЁТЫ
# Раз в неделю присылает сводку в Telegram
# ============================================================

import os
import json
import requests
from datetime import datetime

OBS_FILE = "data/observation.json"
QUALITY_FILE = "data/quality.json"
ACTIONS_FILE = "data/actions_history.json"
PRICES_FILE = "data/price_history.json"


def load(path, default=None):
    if not os.path.exists(path):
        return default or {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default or {}


obs = load(OBS_FILE)
quality = load(QUALITY_FILE)
actions = load(ACTIONS_FILE, [])
prices = load(PRICES_FILE, [])


# ---------- Формируем отчёт ----------
message = "🧠 <b>ARGUS — недельный отчёт</b>\n"
message += f"📅 {datetime.utcnow().strftime('%d.%m.%Y')}\n\n"

# Знания
total_queries = obs.get("total_queries", 0)
failed_percent = obs.get("failed_percent", 0)

message += f"📊 <b>Запросы</b>\n"
message += f"  Всего: {total_queries}\n"
message += f"  Без ответа: {failed_percent}%\n\n"

# Качество
if quality:
    acc = quality.get("accuracy", 0)
    message += f"🎯 <b>Точность поиска:</b> {acc}%\n\n"

# Действия
if actions:
    success = sum(1 for a in actions if a.get("success"))
    message += f"🤖 <b>Авто-действий:</b> {success}/{len(actions)}\n\n"

# Данные
if prices:
    message += f"💾 <b>Свечей BTC:</b> {len(prices)}\n"
    if prices:
        last = prices[-1]
        message += f"  Последняя цена: ${last.get('close', '?')}\n"
    message += "\n"

message += "🔧 Система работает стабильно."


# ---------- Отправка ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    try:
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            params={
                "chat_id": chat_id,
                "text": message[:4000],
                "parse_mode": "HTML"
            },
            timeout=15
        )
        print("📤 Отчёт отправлен")
    except Exception as e:
        print(f"⚠️ {e}")
else:
    print(message)