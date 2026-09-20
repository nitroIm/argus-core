# ============================================================
# ARGUS — BRAIN (ЦЕНТРАЛЬНЫЙ ПЛАНИРОВЩИК) v2
# v2: Читает schedule.json и запускает задачи по расписанию
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
SCHEDULE_FILE = DATA_DIR / "schedule.json"
STATE_FILE = DATA_DIR / "brain_state.json"

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- GitHub ---
GITHUB_PAT = os.getenv("GITHUB_PAT")
GITHUB_REPO = os.getenv("GITHUB_REPO")


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception:
        pass


def load_schedule() -> dict:
    """Загружает расписание из schedule.json."""
    if not SCHEDULE_FILE.exists():
        print("❌ schedule.json не найден")
        return {"enabled": False, "tasks": {}}
    
    with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    """Загружает состояние мозга."""
    if STATE_FILE.exists():
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"last_runs": {}, "decisions": []}


def save_state(state: dict):
    """Сохраняет состояние мозга."""
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def should_run_task(task_name: str, interval_minutes: int, last_runs: dict) -> bool:
    """Проверяет, нужно ли запустить задачу."""
    last_run = last_runs.get(task_name)
    if not last_run:
        return True
    
    last_time = datetime.fromisoformat(last_run)
    now = datetime.now(timezone.utc)
    minutes_since = (now - last_time).total_seconds() / 60
    
    return minutes_since >= interval_minutes


def trigger_workflow(workflow_name: str) -> bool:
    """Запускает workflow через repository_dispatch."""
    if not GITHUB_PAT or not GITHUB_REPO:
        print(f"️ Нет GITHUB_PAT или GITHUB_REPO")
        return False
    
    url = f"https://api.github.com/repos/{GITHUB_REPO}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    payload = {
        "event_type": f"run_{workflow_name.lower().replace(' ', '_')}",
        "client_payload": {"triggered_by": "brain"}
    }
    
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=15)
        return r.status_code == 204
    except Exception as e:
        print(f"⚠️ Ошибка запуска {workflow_name}: {e}")
        return False


def main():
    print(f"🧠 ARGUS Brain: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    # Загружаем расписание
    schedule = load_schedule()
    
    if not schedule.get("enabled", True):
        print("⏸️ Brain отключён в schedule.json")
        return
    
    # Загружаем состояние
    state = load_state()
    last_runs = state.get("last_runs", {})
    
    now = datetime.now(timezone.utc)
    triggered = []
    
    # Проверяем каждую задачу
    for task_name, task_config in schedule.get("tasks", {}).items():
        if not task_config.get("enabled", True):
            print(f"   ⏭️ {task_name}: отключена")
            continue
        
        interval = task_config.get("interval_minutes", 60)
        workflow = task_config.get("workflow", task_name)
        
        if should_run_task(task_name, interval, last_runs):
            print(f"   ✅ {task_name}: пора запустить (интервал: {interval} мин)")
            
            if trigger_workflow(workflow):
                print(f"      🚀 {workflow} запущен")
                triggered.append(task_name)
                last_runs[task_name] = now.isoformat()
            else:
                print(f"      ❌ Не удалось запустить {workflow}")
        else:
            last_run = last_runs.get(task_name, "никогда")
            print(f"   ⏳ {task_name}: следующий запуск позже (последний: {last_run[:19]})")
    
    # Сохраняем состояние
    state["last_runs"] = last_runs
    state["last_check"] = now.isoformat()
    state["triggered"] = triggered
    save_state(state)
    
    # Уведомление
    if triggered:
        msg = f"🧠 <b>Brain: запущены задачи</b>\n\n"
        for t in triggered:
            msg += f"✅ {t}\n"
        notify(msg)
        print(f"\n🎯 Запущено: {', '.join(triggered)}")
    else:
        print("\n💤 Все задачи в ожидании")
    
    print("=" * 60)


if __name__ == "__main__":
    main()
