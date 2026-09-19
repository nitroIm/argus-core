# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ (v5)
# v5: pathlib, sys.exit, надёжная проверка скачивания (через books/)
# ============================================================

import os
import sys
import json
import subprocess
import hashlib
import requests
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
BOOKS_DIR = REPO_ROOT / "books"

PENDING_FILE = DATA_DIR / "pending_cards.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
COLLECTOR = SCRIPT_DIR / "collector.py"

# --- Telegram ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[notify] пропуск: нет токена или chat_id")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID, 
                "text": text, 
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
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
    sys.exit(1)

# --- Загружаем pending ---
pending = {}
if PENDING_FILE.exists():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {PENDING_FILE}: {e}")
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
        # Fallback: ищем в исходном списке кандидатов по хэшу или индексу
        if CANDIDATES_FILE.exists():
            try:
                with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                candidates = data.get("candidates", [])
                for i, c in enumerate(candidates):
                    c_url = c.get("url", "")
                    c_id = hashlib.md5(c_url.encode("utf-8")).hexdigest()[:16]
                    if c_id == ID_INPUT or str(i) == ID_INPUT:
                        url = c_url
                        title = c.get("title", title)
                        break
            except Exception as e:
                print(f"⚠️ Ошибка чтения {CANDIDATES_FILE}: {e}")

if not url:
    msg = f"❌ <b>Не найден кандидат</b>\n\n<code>{ID_INPUT or URL_INPUT}</code>"
    print(msg)
    notify(msg)
    sys.exit(1)

print(f"📥 Скачиваю: {title}")
print(f"🔗 URL: {url}")

# --- Скачивание ---
if not COLLECTOR.exists():
    msg = f"❌ Не найден скрипт collector: {COLLECTOR}"
    print(msg)
    notify(msg)
    sys.exit(1)

# Запоминаем файлы в books/ ДО скачивания, чтобы проверить появление нового
books_before = set(os.listdir(BOOKS_DIR)) if BOOKS_DIR.exists() else set()

try:
    result = subprocess.run(
        [sys.executable, str(COLLECTOR), url],
        capture_output=True,
        text=True,
        timeout=600,  # 10 минут на скачивание
    )
except subprocess.TimeoutExpired:
    msg = f"⏱ <b>Таймаут скачивания (10 мин)</b>\n\n{title[:100]}"
    print(msg)
    notify(msg)
    sys.exit(1)

stdout = result.stdout or ""
stderr = result.stderr or ""

print(stdout)
if stderr:
    print("STDERR:", stderr)

# --- Проверяем успех ---
# 1. Код возврата 0
# 2. В выводе есть "скачано" или "уже есть" (регистронезависимо)
# 3. ИЛИ в папке books/ появился новый файл (самая надёжная проверка)
books_after = set(os.listdir(BOOKS_DIR)) if BOOKS_DIR.exists() else set()
new_files = books_after - books_before

is_downloaded = "скачано" in stdout.lower()
is_already_exists = "уже есть" in stdout.lower()
has_new_file = len(new_files) > 0

success = (result.returncode == 0) and (is_downloaded or has_new_file)
already_exists = (result.returncode == 0) and is_already_exists

# --- Обновляем pending ---
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
except Exception as e:
    print(f"⚠️ Ошибка записи {PENDING_FILE}: {e}")

# --- Уведомление ---
short_title = title[:150]

if success:
    notify(f"✅ <b>Скачано</b>\n\n{short_title}\n\nДобавлено в books/")
elif already_exists:
    notify(f"⏭ <b>Уже в базе</b>\n\n{short_title}")
elif "ошибка" in stdout.lower() or "error" in stderr.lower() or result.returncode != 0:
    # Находим первую строку с ошибкой
    err_line = ""
    for line in (stdout + "\n" + stderr).splitlines():
        if "ошибка" in line.lower() or "error" in line.lower() or "failed" in line.lower():
            err_line = line.strip()
            break
    
    notify(f"❌ <b>Не скачалось</b>\n\n{short_title}\n\n<code>{err_line[:200]}</code>")
else:
    notify(f"⚠️ <b>Неясный результат</b>\n\n{short_title}\n\nПроверь логи Actions.")

if result.returncode != 0:
    sys.exit(1)

print(f"✅ Готово: {title}")
