# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ
# ============================================================

import os
import sys
import json
import subprocess
import hashlib

# --- Пути относительно скрипта (не cwd!) ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
CANDIDATES_FILE = os.path.join(REPO_ROOT, "data", "scout_candidates.json")
COLLECTOR = os.path.join(SCRIPT_DIR, "collector.py")

# --- Входные данные ---
URL_INPUT = (os.environ.get("APPROVE_URL") or "").strip() or None
ID_INPUT = (os.environ.get("APPROVE_ID") or os.environ.get("SHORT_ID") or "").strip() or None
TITLE_INPUT = (os.environ.get("APPROVE_TITLE") or "").strip() or None

# Обратная совместимость: approve_handler.py <short_id|url>
if len(sys.argv) > 1 and not URL_INPUT and not ID_INPUT:
    arg = sys.argv[1].strip()
    if arg.startswith("http"):
        URL_INPUT = arg
    else:
        ID_INPUT = arg

if not URL_INPUT and not ID_INPUT:
    print("❌ Не указан ни URL (APPROVE_URL), ни short_id (APPROVE_ID / argv[1])")
    exit(1)

# --- Загружаем базу кандидатов ---
data = {}
if os.path.exists(CANDIDATES_FILE):
    try:
        with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Не удалось прочитать {CANDIDATES_FILE}: {e}")
        data = {}
else:
    print(f"⚠️ Файл {CANDIDATES_FILE} не найден")

url = None
title = TITLE_INPUT or "manual download"
removed = False

# --- 1) URL передан напрямую ---
if URL_INPUT:
    url = URL_INPUT
    candidates = data.get("candidates", [])
    for i, c in enumerate(candidates):
        if isinstance(c, dict) and c.get("url") == url:
            if not TITLE_INPUT:
                title = c.get("title", title)
            candidates.pop(i)
            removed = True
            break
    data["candidates"] = candidates
else:
    # --- 2) Поиск по short_id в pending (старая схема) ---
    pending = data.get("pending", {})
    item = pending.get(ID_INPUT) if isinstance(pending, dict) else None
    if item:
        url = item.get("url")
        title = item.get("title", title)
        del pending[ID_INPUT]
        data["pending"] = pending
        removed = True
    else:
        # --- 3) Поиск по short_id в candidates (новая схема от Explorer) ---
        candidates = data.get("candidates", [])
        for i, c in enumerate(candidates):
            if not isinstance(c, dict):
                continue
            c_url = c.get("url", "")
            c_id = c.get("id") or hashlib.md5(c_url.encode()).hexdigest()[:8]
            if c_id == ID_INPUT or str(i) == ID_INPUT:
                url = c_url
                title = c.get("title", title)
                candidates.pop(i)
                removed = True
                break
        data["candidates"] = candidates

if not url:
    print(f"❌ Не найден кандидат: {ID_INPUT or URL_INPUT}")
    exit(1)

print(f"📥 Скачиваю: {title}")
print(f"🔗 URL: {url}")

# --- Скачивание ---
if not os.path.exists(COLLECTOR):
    print(f"❌ Не найден collector: {COLLECTOR}")
    exit(1)

try:
    result = subprocess.run(
        [sys.executable, COLLECTOR, url],
        capture_output=True,
        text=True,
        timeout=600,
    )
except subprocess.TimeoutExpired:
    print("❌ Таймаут скачивания (600 сек)")
    exit(1)

print(result.stdout)
if result.returncode != 0:
    print(result.stderr)
    exit(1)

# --- Обновляем базу ---
if removed:
    try:
        with open(CANDIDATES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не удалось записать {CANDIDATES_FILE}: {e}")

print(f"✅ Готово: {title}")