# ============================================================
# ARGUS — ОТЧЁТ ПО НОВОСТЯМ
# Отправляет сводку сентимента в Telegram
# ============================================================

import os
import json
import requests

DATA_DIR = "data"
SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")

if not os.path.exists(SENTIMENT_FILE):
    print("❌ Нет данных о новостях.")
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
        title = n["title"][:100]
        msg += f"• {title}\n"
    msg += "\n"

if data.get("top_bearish"):
    msg += "<b>🔴 Главные негативные:</b>\n"
    for n in data["top_bearish"][:3]:
        title = n["title"][:100]
        msg += f"• {title}\n"
    msg += "\n"

msg += "<i>Настроение — один из факторов прогноза.</i>"


# ---------- Отправка ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": msg[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        print("📤 Отчёт отправлен")
    except Exception as e:
        print(f"⚠️ {e}")
else:
    print(msg)