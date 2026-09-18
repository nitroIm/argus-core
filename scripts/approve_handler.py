# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ
# v4: с уведомлениями в Telegram (успех/провал)
# ============================================================

import os
import sys
import json
import subprocess
import hashlib
import requests

# --- Пути ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
PENDING_FILE = os.path.join(REPO_ROOT, "data", "pending_cards.json")
CANDIDATES_FILE = os.path.join(REPO_ROOT, "data", "scout_candidates.json")
COLLECTOR = os.path.join(SCRIPT_DIR, "collector.py")

# --- Telegram ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print(f"[notify] пропуск: нет токена или chat_id")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception as e:
        print(f"[notify] ошибка: {e}")

# --- Входные данные ---
URL_INPUT = (os.environ.get("APPROVE_URL") or "").strip() or None
ID_INPUT = (os.environ.get("APPROVE_ID") or os.environ.get("SHORT_ID") or "").strip() or None
TITLE_INPUT = (os.environ.get("APPROVE_TITLE") or "").strip() or None

if len(sys.argv) > 1 and not URL_INPUT and not ID_INPUT:
    arg = sys.argv[1].strip()
    if arg.startswith("http"):
        URL_INPUT = arg
    else:
        ID_INPUT = arg

if not URL_INPUT and not ID_INPUT:
    msg = "❌ approve_handler: не указан ни URL, ни short_id"
    print(msg)
    notify(msg)
    exit(1)

# --- Загружаем pending ---
pending = {}
if os.path.exists(PENDING_FILE):
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception as e:
        print(f"⚠️ {PENDING_FILE}: {e}")
        pending = {}

url = None
title = TITLE_INPUT or "manual download"
removed_id = None

# --- Определяем url ---
if URL_INPUT:
    url = URL_INPUT
    for sid, item in list(pending.items()):
        if item.get("url") == url:
            if not TITLE_INPUT:
                title = item.get("title", title)
            del pending[sid]
            removed_id = sid
            break
else:
    item = pending.get(ID_INPUT)
    if item:
        url = item.get("url")
        title = item.get("title", title)
        del pending[ID_INPUT]
        removed_id = ID_INPUT
    else:
        if os.path.exists(CANDIDATES_FILE):
            try:
                with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candidates = data.get("candidates", [])
                for i, c in enumerate(candidates):
                    c_url = c.get("url", "")
                    c_id = hashlib.md5(c_url.encode()).hexdigest()[:16]
                    if c_id == ID_INPUT or str(i) == ID_INPUT:
                        url = c_url
                        title = c.get("title", title)
                        break
            except Exception as e:
                print(f"⚠️ scout_candidates.json: {e}")

if not url:
    msg = f"❌ <b>Не найден кандидат</b>\n\n{ID_INPUT or URL_INPUT}"
    print(msg)
    notify(msg)
    exit(1)

print(f"📥 Скачиваю: {title}")
print(f"🔗 URL: {url}")

# --- Скачивание ---
if not os.path.exists(COLLECTOR):
    msg = f"❌ Не найден collector: {COLLECTOR}"
    print(msg)
    notify(msg)
    exit(1)

try:
    result = subprocess.run(
        [sys.executable, COLLECTOR, url],
        capture_output=True,
        text=True,
        timeout=600,
    )
except subprocess.TimeoutExpired:
    msg = f"⏱ <b>Таймаут скачивания</b>\n\n{title}"
    print(msg)
    notify(msg)
    exit(1)

print(result.stdout)
if result.stderr:
    print(result.stderr)

# --- Проверяем успех: искали "Скачано: 1" и НЕ "Ошибка" в выводе ---
stdout = result.stdout or ""
success = (
    result.returncode == 0
    and "Скачано: 1" in stdout
    and "Ошибка" not in stdout
    and "Уже есть" not in stdout   # уже есть — считаем не новым
)

# --- Обновляем pending ---
try:
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
except Exception as e:
    print(f"⚠️ {PENDING_FILE}: {e}")

# --- Уведомление ---
short_title = title[:150]
if success:
    notify(f"✅ <b>Скачано</b>\n\n{short_title}\n\nДобавлено в books/")
elif "Уже есть" in stdout:
    notify(f"⏭ <b>Уже в базе</b>\n\n{short_title}")
elif "Ошибка" in stdout or result.returncode != 0:
    # найдём строку ошибки
    err_line = ""
    for line in stdout.splitlines():
        if "Ошибка" in line or "error" in line.lower():
            err_line = line.strip()
            break
    notify(f"❌ <b>Не скачалось</b>\n\n{short_title}\n\n{err_line[:200]}")
else:
    notify(f"⚠️ <b>Неясный результат</b>\n\n{short_title}")

if result.returncode != 0:
    exit(1)

print(f"✅ Готово: {title}")