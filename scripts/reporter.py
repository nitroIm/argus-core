# ============================================================
# ARGUS — НЕДЕЛЬНЫЙ ОТЧЁТ (v3)
# ------------------------------------------------------------
# v3: убрана крипто-секция (price_history переехал в crypto/).
#     Добавлена сводка по книгам из data/summary.json.
#     Отчёт ТОЛЬКО по ARGUS (книги, поиск, автономия).
#     Крипто-отчёт — отдельно, в crypto/report/weekly.py.
# ------------------------------------------------------------
# v2: pathlib, фикс бага load() со списками, timezone
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

OBS_FILE = DATA_DIR / "observation.json"
QUALITY_FILE = DATA_DIR / "quality.json"
ACTIONS_FILE = DATA_DIR / "actions_history.json"
SUMMARY_FILE = DATA_DIR / "summary.json"


def load_json(path, default=None):
    """Безопасная загрузка, корректно обрабатывающая и dict, и list."""
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# --- Загрузка ---
obs = load_json(OBS_FILE, {})
quality = load_json(QUALITY_FILE, {})
actions = load_json(ACTIONS_FILE, [])
summary = load_json(SUMMARY_FILE, {})


# ============================================================
# ФОРМИРОВАНИЕ ОТЧЁТА
# ============================================================
now = datetime.now(timezone.utc)
message = f"🧠 <b>ARGUS — недельный отчёт</b>\n"
message += f"📅 {now.strftime('%d.%m.%Y %H:%M')} (UTC)\n\n"

# --- 1. Знания (книги) ---
total_books = summary.get("total_books", 0)
total_chunks = summary.get("total_chunks", 0)
message += f"📚 <b>База знаний</b>\n"
message += f"  Книг: {total_books}\n"
message += f"  Чанков: {total_chunks}\n\n"

# --- 2. Поисковые запросы ---
total_queries = obs.get("total_queries", 0)
failed_percent = obs.get("failed_percent", 0.0)
avg_score = obs.get("avg_score", 0.0)

message += f"📊 <b>Поисковые запросы</b>\n"
message += f"  Всего: {total_queries}\n"
message += f"  Без ответа: {failed_percent}%\n"
message += f"  Ср. качество: {avg_score:.3f}\n\n"

# --- 3. Точность поиска (Guardian) ---
if quality and "accuracy" in quality:
    acc = quality.get("accuracy", 0)
    message += f"🎯 <b>Точность (Guardian):</b> {acc}%\n\n"

# --- 4. Авто-действия (Actor) ---
if isinstance(actions, list) and actions:
    success_count = sum(1 for a in actions if a.get("success"))
    message += f"🤖 <b>Авто-действия:</b> {success_count} из {len(actions)}\n\n"
else:
    message += f"🤖 <b>Авто-действия:</b> не выполнялись\n\n"

# --- Итог ---
message += "🔧 <i>Система работает стабильно. ARGUS на страже.</i>"


# ============================================================
# ОТПРАВКА
# ============================================================
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
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
        print(f"⚠️ Ошибка отправки: {e}")
else:
    print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID. Вывод в консоль:")
    clean_msg = (message
                 .replace("<b>", "").replace("</b>", "")
                 .replace("<i>", "").replace("</i>", ""))
    print(clean_msg)