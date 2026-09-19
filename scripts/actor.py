# ============================================================
# ARGUS — АКТОР (v3)
# v3: фикс импорта sys, безопасная обрезка HTML, pathlib, timezone
# ============================================================

import os
import sys
import json
import subprocess
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

PROPOSALS_FILE = DATA_DIR / "proposals.json"
HISTORY_FILE = DATA_DIR / "actions_history.json"

if not PROPOSALS_FILE.exists():
    print(f"❌ Нет {PROPOSALS_FILE}. Сначала запусти proposer.")
    sys.exit(0)

with open(PROPOSALS_FILE, "r", encoding="utf-8") as f:
    proposals_data = json.load(f)

proposals = proposals_data.get("proposals", [])

if HISTORY_FILE.exists():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            actions_history = json.load(f)
    except Exception:
        actions_history = []
else:
    actions_history = []

# --- Переменные окружения ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GITHUB_REPO = os.getenv("GITHUB_REPO", "nitroIm/argus-core")
GITHUB_PAT = os.getenv("GITHUB_PAT")


# ============================================================
# БЕЗОПАСНЫЕ ДЕЙСТВИЯ
# ============================================================
def rebuild_index():
    print("🔄 Пересобираю индекс...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "build_index.py")],
            capture_output=True, text=True, timeout=900,
        )
        if result.returncode == 0:
            return True, "Индекс пересобран"
        return False, f"Ошибка: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def run_observer():
    print("🔄 Запускаю Observer...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "observer.py")],
            capture_output=True, text=True, timeout=120,
        )
        return result.returncode == 0, "Observer выполнен"
    except Exception as e:
        return False, f"Ошибка: {e}"


def run_collect():
    print("🔄 Собираю данные с бирж...")
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT_DIR / "collect_data.py")],
            capture_output=True, text=True, timeout=180,
        )
        return result.returncode == 0, "Данные собраны"
    except Exception as e:
        return False, f"Ошибка: {e}"


def run_explorer_for_topic(topic: str):
    """
    Запускает Explorer для поиска книг по конкретной теме через GitHub Actions.
    """
    if not GITHUB_PAT:
        return False, "Нет GITHUB_PAT — не могу дёрнуть Explorer"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    body = {
        "event_type": "explore_topic",
        "client_payload": {"topic": topic},
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=20)
        if r.status_code == 204:
            return True, f"Explorer запущен по теме: {topic}"
        return False, f"GitHub {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Ошибка: {e}"


SAFE_ACTIONS = {
    "rebuild_index": rebuild_index,
    "retrain_index": rebuild_index, # Пока просто пересборка, полноценный retrain лучше делать через workflow
    "run_observer": run_observer,
    "collect_data": run_collect,
}


# ============================================================
# ВЫПОЛНЕНИЕ
# ============================================================
executed = []

for p in proposals:
    if not p.get("can_auto"):
        continue

    action = p.get("action")

    if action in SAFE_ACTIONS:
        print(f"\n▶️ Выполняю: {action}")
        success, msg = SAFE_ACTIONS[action]()

    elif action == "download_book":
        topic = p.get("topic", "")
        print(f"\n▶️ Скачиваю книгу по теме: {topic}")
        success, msg = run_explorer_for_topic(topic)

    elif action == "download_multiple":
        print(f"\n▶️ Запускаю общий Explorer")
        success, msg = run_explorer_for_topic("")

    else:
        print(f"\n⏭ Пропускаю: {action} (нет в списке безопасных)")
        continue

    executed.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "topic": p.get("topic", ""),
        "success": success,
        "message": msg,
    })
    print(f"   {'✅' if success else '❌'} {msg}")


# ============================================================
# СОХРАНЕНИЕ + ОТЧЁТ
# ============================================================
actions_history.extend(executed)
if len(actions_history) > 500:
    actions_history = actions_history[-500:]

DATA_DIR.mkdir(parents=True, exist_ok=True)
with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(actions_history, f, ensure_ascii=False, indent=2)

if BOT_TOKEN and CHAT_ID and executed:
    # БЕЗОПАСНАЯ сборка сообщения: ограничиваем количество элементов, а не символы, 
    # чтобы не разорвать HTML-теги
    report_items = executed[:15]  # Максимум 15 последних действий в отчете
    
    message_lines = ["🤖 <b>ARGUS — выполнил действия</b>\n"]
    for e in report_items:
        icon = "✅" if e["success"] else "❌"
        # Экранируем текст сообщения на случай спецсимволов
        safe_msg = e["message"].replace("<", "&lt;").replace(">", "&gt;")
        message_lines.append(f"{icon} {safe_msg}")
    
    if len(executed) > 15:
        message_lines.append(f"\n<i>...и ещё {len(executed) - 15} действий (см. actions_history.json)</i>")

    message = "\n".join(message_lines)
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID, 
                "text": message, 
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15,
        )
        print("\n📤 Отчёт отправлен в Telegram")
    except Exception as e:
        print(f"⚠️ Telegram error: {e}")

print("\n" + "=" * 50)
print(f"✅ Выполнено действий: {len(executed)}")
print(f"📊 Всего в истории: {len(actions_history)}")
print("=" * 50)
