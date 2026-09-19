# ============================================================
# ARGUS — ОТЧЁТ ПО НОВОСТЯМ (v3)
# v3: pathlib, безопасный доступ к данным, sys.exit, защита HTML
# ============================================================

import os
import sys
import json
import requests
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"

print(f"[news_report] читаю: {SENTIMENT_FILE}")

if not SENTIMENT_FILE.exists():
    print(f"❌ Нет файла {SENTIMENT_FILE}. Сначала запусти news_analyzer.py")
    sys.exit(0)

try:
    with open(SENTIMENT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception as e:
    print(f"❌ Ошибка чтения {SENTIMENT_FILE}: {e}")
    sys.exit(1)

# ---------- Формируем сообщение (безопасный доступ к ключам) ----------
mood = data.get("mood", "Неизвестно")
avg_sentiment = data.get("avg_sentiment", 0.0)
total_news = data.get("total_news", 0)
bullish_count = data.get("bullish_count", 0)
bearish_count = data.get("bearish_count", 0)
neutral_count = data.get("neutral_count", 0)

msg = f"📰 <b>ARGUS — Настроение рынка</b>\n\n"
msg += f"🎭 {mood}\n"
msg += f"📊 Сентимент: <b>{avg_sentiment:+.3f}</b>\n\n"

msg += f"📈 Всего новостей: {total_news}\n"
msg += f"🟢 Бычьих: {bullish_count}\n"
msg += f"🔴 Медвежьих: {bearish_count}\n"
msg += f"🟡 Нейтральных: {neutral_count}\n\n"

top_bullish = data.get("top_bullish", [])
if top_bullish:
    msg += "<b>🟢 Главные позитивные:</b>\n"
    for n in top_bullish[:3]:
        title = n.get("title", "Без заголовка")[:100]
        msg += f"• {title}\n"
    msg += "\n"

top_bearish = data.get("top_bearish", [])
if top_bearish:
    msg += "<b>🔴 Главные негативные:</b>\n"
    for n in top_bearish[:3]:
        title = n.get("title", "Без заголовка")[:100]
        msg += f"• {title}\n"
    msg += "\n"

msg += "<i>Настроение — один из факторов прогноза ARGUS.</i>"

# ---------- Отправка ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if not bot_token or not chat_id:
    print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID — печатаю в лог:")
    # Очищаем от HTML-тегов для красивого вывода в консоль
    clean_msg = msg.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(clean_msg)
    sys.exit(0)

try:
    r = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": msg,  # Убрали [:4000], так как сообщение гарантированно короткое (~800-1200 символов) и не разорвёт HTML
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    if r.status_code == 200:
        print("📤 Отчёт успешно отправлен в Telegram")
    else:
        print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
except Exception as e:
    print(f"⚠️ Ошибка отправки в Telegram: {e}")
