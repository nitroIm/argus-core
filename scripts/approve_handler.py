# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ (v6)
# v6: чистая версия, явные логи, поддержка short_id от bot_host
# ============================================================

import os
import sys
import json
import subprocess
import hashlib
import requests
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
BOOKS_DIR = REPO_ROOT / "books"

PENDING_FILE = DATA_DIR / "pending_cards.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
COLLECTOR = SCRIPT_DIR / "collector.py"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        print("[notify] нет токена/chat_id")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
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

print(f"🎯 ID_INPUT: {ID_INPUT}")
print(f"🎯 URL_INPUT: {URL_INPUT}")

# --- Загружаем pending ---
pending = {}
if PENDING_FILE.exists():
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception as e:
        print(f"⚠️ Ошибка чтения {PENDING_FILE}: {e}")

url = None
title = TITLE_INPUT or "manual download"
removed_id = None

# --- Ищем URL ---
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
        print(f"✅ Найден в pending: {title[:60]}")
    else:
        # Fallback: ищем в scout_candidates.json
        print(f"⚠️ Не найден в pending по {ID_INPUT}, ищу в candidates...")
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
                        print(f"✅ Найден в candidates: {title[:60]}")
                        break
            except Exception as e:
                print(f"⚠️ Ошибка чтения candidates: {e}")

if not url:
    msg = f"❌ <b>Не найден кандидат</b>\n\n<code>{ID_INPUT or URL_INPUT}</code>"
    print(msg)
    notify(msg)
    sys.exit(1)

print(f"📥 Скачиваю: {title}")
print(f"🔗 URL: {url}")

# --- Проверка collector ---
if not COLLECTOR.exists():
    msg = f"❌ Не найден collector: {COLLECTOR}"
    print(msg)
    notify(msg)
    sys.exit(1)

# --- Файлы до ---
books_before = set()
if BOOKS_DIR.exists():
    books_before = {p.name for p in BOOKS_DIR.iterdir() if p.is_file()}
print(f"📂 Файлов в books/ до: {len(books_before)}")

# --- Скачивание ---
try:
    result = subprocess.run(
        [sys.executable, str(COLLECTOR), url],
        capture_output=True,
        text=True,
        timeout=600,
    )
except subprocess.TimeoutExpired:
    msg = f"⏱ Таймаут скачивания (10 мин)\n\n{title[:100]}"
    print(msg)
    notify(msg)
    sys.exit(1)

stdout = result.stdout or ""
stderr = result.stderr or ""

print("=== collector stdout ===")
print(stdout)
if stderr:
    print("=== collector stderr ===")
    print(stderr)

# --- Файлы после ---
books_after = set()
if BOOKS_DIR.exists():
    books_after = {p.name for p in BOOKS_DIR.iterdir() if p.is_file()}
new_files = books_after - books_before
print(f"📂 Файлов в books/ после: {len(books_after)}")
print(f"🆕 Новых: {new_files}")

# --- Проверка успеха ---
is_downloaded = "скачано" in stdout.lower() or "✅" in stdout
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
    files_info = ", ".join(list(new_files)[:3]) if new_files else "уже был"
    notify(f"✅ <b>Скачано</b>\n\n{short_title}\n\nФайлы: <code>{files_info}</code>")
elif already_exists:
    notify(f"⏭ <b>Уже в базе</b>\n\n{short_title}")
else:
    err_line = ""
    for line in (stdout + "\n" + stderr).splitlines():
        if "ошибка" in line.lower() or "error" in line.lower() or "failed" in line.lower():
            err_line = line.strip()
            break
    notify(f"❌ <b>Не скачалось</b>\n\n{short_title}\n\n<code>{err_line[:200]}</code>")

if result.returncode != 0:
    sys.exit(1)

print(f"✅ Готово: {title}")