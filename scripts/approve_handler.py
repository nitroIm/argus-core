# ============================================================
# ARGUS — ОБРАБОТКА ОДОБРЕНИЙ
# v3: поддержка pending_cards.json, прямого URL и short_id
# ============================================================

import os
import sys
import json
import subprocess
import hashlib

# --- Пути ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
PENDING_FILE = os.path.join(REPO_ROOT, "data", "pending_cards.json")
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

# --- Загружаем pending ---
pending = {}
if os.path.exists(PENDING_FILE):
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except Exception as e:
        print(f"⚠️ Не удалось прочитать {PENDING_FILE}: {e}")
        pending = {}

url = None
title = TITLE_INPUT or "manual download"
removed_id = None

# --- 1) URL передан напрямую ---
if URL_INPUT:
    url = URL_INPUT
    # попробуем найти соответствующий pending по url
    for sid, item in list(pending.items()):
        if item.get("url") == url:
            if not TITLE_INPUT:
                title = item.get("title", title)
            del pending[sid]
            removed_id = sid
            break
    # если не нашли в pending, всё равно качаем
else:
    # --- 2) Поиск по short_id в pending ---
    item = pending.get(ID_INPUT)
    if item:
        url = item.get("url")
        title = item.get("title", title)
        del pending[ID_INPUT]
        removed_id = ID_INPUT
    else:
        # --- 3) Поиск в scout_candidates.json (на всякий случай) ---
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

# --- Обновляем pending на диске ---
try:
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)
    if removed_id:
        print(f"🧹 Удалён из pending: {removed_id}")
except Exception as e:
    print(f"⚠️ Не удалось записать {PENDING_FILE}: {e}")

print(f"✅ Готово: {title}")