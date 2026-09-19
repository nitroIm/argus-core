# ============================================================
# ARGUS — НЕДЕЛЬНЫЙ ОТЧЁТ (v2)
# v2: pathlib, фикс бага load() со списками, timezone, безопасный Telegram
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
PRICES_FILE = DATA_DIR / "price_history.json"


def load_json(path, default=None):
    """Безопасная загрузка, корректно обрабатывающая и dict, и list."""
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


# Загружаем данные (явно указываем типы по умолчанию, чтобы избежать бага or {})
obs = load_json(OBS_FILE, {})
quality = load_json(QUALITY_FILE, {})
actions = load_json(ACTIONS_FILE, [])  # Теперь это гарантированно список
prices = load_json(PRICES_FILE, [])    # Теперь это гарантированно список


# ---------- Формируем отчёт ----------
now = datetime.now(timezone.utc)
message = f"🧠 <b>ARGUS — недельный отчёт</b>\n"
message += f"📅 {now.strftime('%d.%m.%Y %H:%M')} (UTC)\n\n"

# 1. Знания и запросы
total_queries = obs.get("total_queries", 0)
failed_percent = obs.get("failed_percent", 0.0)
avg_score = obs.get("avg_score", 0.0)  # Новая метрика из observer.py v4

message += f"📊 <b>Поисковые запросы</b>\n"
message += f"  Всего: {total_queries}\n"
message += f"  Без ответа: {failed_percent}%\n"
message += f"  Ср. качество (score): {avg_score:.3f}\n\n"

# 2. Качество поиска (Guardian)
if quality and "accuracy" in quality:
    acc = quality.get("accuracy", 0)
    message += f"🎯 <b>Точность поиска (Guardian):</b> {acc}%\n\n"

# 3. Авто-действия (Actor)
if isinstance(actions, list) and actions:
    success_count = sum(1 for a in actions if a.get("success"))
    message += f"🤖 <b>Авто-действия:</b> {success_count} успешно из {len(actions)}\n\n"
else:
    message += f"🤖 <b>Авто-действия:</b> не выполнялись\n\n"

# 4. Рыночные данные (Collect)
if isinstance(prices, list) and prices:
    message += f"💾 <b>Рыночные данные:</b>\n"
    message += f"  Свечей в базе: {len(prices)}\n"
    last = prices[-1]
    if isinstance(last, dict) and "close" in last:
        message += f"  Последняя цена BTC: ${last.get('close'):,.2f}\n"
    message += "\n"

message += "🔧 <i>Система работает стабильно. ARGUS на страже.</i>"


# ---------- Отправка ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    try:
        # Используем POST с json= для надежной отправки HTML и спецсимволов
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,  # Обрезка не нужна, текст ~500-800 символов
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
        print(f"⚠️ Ошибка отправки: {e}")
else:
    print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID. Вывод в консоль:")
    # Очищаем от HTML для красивого вывода в терминал
    clean_msg = message.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", "")
    print(clean_msg)
