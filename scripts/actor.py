# ============================================================
# ARGUS — АКТОР
# Выполняет безопасные действия из proposals.json
# ============================================================

import os
import json
import subprocess
import requests
from datetime import datetime

PROPOSALS_FILE = "data/proposals.json"
HISTORY_FILE = "data/actions_history.json"

# ---------- Проверки ----------
if not os.path.exists(PROPOSALS_FILE):
    print("❌ Нет proposals.json. Сначала запусти proposer.")
    exit(0)

with open(PROPOSALS_FILE, "r", encoding="utf-8") as f:
    proposals_data = json.load(f)

proposals = proposals_data.get("proposals", [])

# ---------- История действий ----------
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        actions_history = json.load(f)
else:
    actions_history = []


# ============================================================
# БЕЗОПАСНЫЕ ДЕЙСТВИЯ
# ============================================================

def rebuild_index():
    """Пересобирает FAISS-индекс."""
    print("🔄 Пересобираю индекс...")
    try:
        result = subprocess.run(
            ["python", "scripts/build_index.py"],
            capture_output=True,
            text=True,
            timeout=600
        )
        if result.returncode == 0:
            return True, "Индекс пересобран"
        else:
            return False, f"Ошибка: {result.stderr[:200]}"
    except Exception as e:
        return False, f"Ошибка: {e}"


def run_observer():
    """Запускает Observer."""
    print("🔄 Запускаю Observer...")
    try:
        result = subprocess.run(
            ["python", "scripts/observer.py"],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0, "Observer выполнен"
    except Exception as e:
        return False, f"Ошибка: {e}"


def run_collect():
    """Собирает свежие данные с бирж."""
    print("🔄 Собираю данные с бирж...")
    try:
        result = subprocess.run(
            ["python", "scripts/collect_data.py"],
            capture_output=True,
            text=True,
            timeout=120
        )
        return result.returncode == 0, "Данные собраны"
    except Exception as e:
        return False, f"Ошибка: {e}"


# ============================================================
# КАРТА ДЕЙСТВИЙ
# ============================================================

SAFE_ACTIONS = {
    "rebuild_index": rebuild_index,
    "retrain_index": rebuild_index,
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

        executed.append({
            "time": datetime.utcnow().isoformat(),
            "action": action,
            "success": success,
            "message": msg
        })

        print(f"   {'✅' if success else '❌'} {msg}")
    else:
        print(f"\n⏭ Пропускаю: {action} (нет в списке безопасных)")


# ============================================================
# СОХРАНЕНИЕ
# ============================================================

actions_history.extend(executed)
if len(actions_history) > 500:
    actions_history = actions_history[-500:]

os.makedirs("data", exist_ok=True)
with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(actions_history, f, ensure_ascii=False, indent=2)


# ============================================================
# ОТЧЁТ В TELEGRAM
# ============================================================

bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id and executed:
    message = "🤖 <b>ARGUS — выполнил действия</b>\n\n"
    for e in executed:
        icon = "✅" if e["success"] else "❌"
        message += f"{icon} {e['message']}\n"

    try:
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            params={
                "chat_id": chat_id,
                "text": message[:4000],
                "parse_mode": "HTML"
            },
            timeout=15
        )
        print("\n📤 Отчёт отправлен в Telegram")
    except Exception as e:
        print(f"⚠️ Telegram: {e}")


# ============================================================
# ВЫВОД
# ============================================================

print("\n" + "=" * 50)
print(f"✅ Выполнено действий: {len(executed)}")
print(f"📊 Всего в истории: {len(actions_history)}")
print("=" * 50)