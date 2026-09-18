# ============================================================
# ARGUS — ОТЧЁТ ПО НОВОСТЯМ
# v2: правильные пути + уведомление
# ============================================================

import os
import json
import requests

# --- Пути от корня репо, а не от cwd ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
SENTIMENT_FILE = os.path.join(REPO_ROOT, "data", "news_sentiment.json")

print(f"[news_report] читаю: {SENTIMENT_FILE}")

if not os.path.exists(SENTIMENT_FILE):
    print(f"❌ Нет файла {SENTIMENT_FILE}")
    exit(0)

with open(SENTIMENT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

# ---------- Формируем сообщение ----------
msg = "📰 <b>ARGUS — Настроение рынка</b>\n\n"
msg += f"🎭 {data['mood']}\n"
msg += f"📊 Сентимент: <b>{data['avg_sentiment']:+.3f}</b>\n\n"

msg += f"📈 Новостей: {data['total_news']}\n"
msg += f"🟢 Бычьих: {data['bullish_count']}\n"
msg += f"🔴 Медвежьих: {data['bearish_count']}\n"
msg += f"🟡 Нейтральных: {data['neutral_count']}\n\n"

if data.get("top_bullish"):
    msg += "<b>🟢 Главные позитивные:</b>\n"
    for n in data["top_bullish"][:3]:
        title = n.get("title", "")[:100]
        msg += f"• {title}\n"
    msg += "\n"

if data.get("top_bearish"):
    msg += "<b>🔴 Главные негативные:</b>\n"
    for n in data["top_bearish"][:3]:
        title = n.get("title", "")[:100]
        msg += f"• {title}\n"
    msg += "\n"

msg += "<i>Настроение — один из факторов прогноза.</i>"

# ---------- Отправка ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if not bot_token or not chat_id:
    print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID — печатаю в лог:")
    print(msg)
    exit(0)

try:
    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": msg[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if r.status_code == 200:
        print("📤 Отчёт отправлен в Telegram")
    else:
        print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
except Exception as e:
    print(f"⚠️ {e}")