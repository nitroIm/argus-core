# ============================================================
# ARGUS — BRAIN (Оркестратор)
# Решает, что запускать. Управляет всей системой.
# ============================================================

import os
import json
import requests
from datetime import datetime, timedelta

# ---------- Настройки ----------
GITHUB_REPO = os.getenv("GITHUB_REPO", "nitroIm/argus-core")
GITHUB_PAT = os.getenv("GITHUB_PAT")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "data/brain_state.json"
DECISIONS_FILE = "data/brain_decisions.json"

# Лимиты (сколько раз в день можно запускать)
LIMITS = {
    "observer": 1,
    "analyzer": 1,
    "actor": 1,
    "train": 1,
    "collect": 24,
    "guardian": 2,
}


# ============================================================
# ЗАГРУЗКА СОСТОЯНИЯ
# ============================================================
def load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ПРОВЕРКА ПРАВ
# ============================================================
def can_run(action, state):
    """Проверяет, не превышен ли дневной лимит."""
    today = datetime.utcnow().strftime("%Y-%m-%d")

    if "runs" not in state:
        state["runs"] = {}

    # Сброс счётчиков при смене дня
    if state.get("day") != today:
        state["runs"] = {}
        state["day"] = today

    count = state["runs"].get(action, 0)
    limit = LIMITS.get(action, 1)

    return count < limit


def mark_run(action, state):
    if "runs" not in state:
        state["runs"] = {}
    state["runs"][action] = state["runs"].get(action, 0) + 1


# ============================================================
# ЗАПУСК WORKFLOW
# ============================================================
def trigger_workflow(workflow_file):
    """Запускает workflow через GitHub API."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"

    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28"
    }

    payload = {"ref": "main"}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 204:
            return True, "OK"
        else:
            return False, f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


# ============================================================
# РЕШЕНИЯ
# ============================================================
def decide(state):
    """Решает, что запускать сейчас."""
    decisions = []

    obs = load("data/observation.json", {})
    quality = load("data/quality.json", {})
    proposals = load("data/proposals.json", {})
    prices = load("data/price_history.json", [])

    now = datetime.utcnow()

    # ---------- 1. Observer раз в день ----------
    last_observer = state.get("last_observer")
    if can_run("observer", state):
        if not last_observer or (now - datetime.fromisoformat(last_observer)) > timedelta(hours=23):
            decisions.append({
                "action": "observer",
                "workflow": "observer.yml",
                "reason": "Раз в день — собрать статистику"
            })

    # ---------- 2. Analyzer если есть свежие наблюдения ----------
    if can_run("analyzer", state) and obs.get("total_queries", 0) > 0:
        decisions.append({
            "action": "analyzer",
            "workflow": "analyzer.yml",
            "reason": "Проанализировать наблюдения"
        })

    # ---------- 3. Guardian test если индекс обновлялся ----------
    if can_run("guardian", state) and quality:
        last_test = quality.get("tested_at")
        if not last_test or (now - datetime.fromisoformat(last_test)) > timedelta(days=1):
            decisions.append({
                "action": "guardian",
                "workflow": "guardian.yml",
                "reason": "Проверить качество поиска"
            })

    # ---------- 4. Actor если есть задачи ----------
    if can_run("actor", state):
        auto_tasks = [p for p in proposals.get("proposals", []) if p.get("can_auto")]
        if auto_tasks:
            decisions.append({
                "action": "actor",
                "workflow": "actor.yml",
                "reason": f"Выполнить {len(auto_tasks)} авто-задач"
            })

    # ---------- 5. Train если книг стало больше ----------
    books_count = len(load("data/summary.json", {}).get("books", []))
    last_train_books = state.get("last_train_books", 0)

    if can_run("train", state) and books_count > last_train_books:
        decisions.append({
            "action": "train",
            "workflow": "train_model.yml",
            "reason": f"Книг стало больше: {last_train_books} → {books_count}"
        })

    return decisions


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
state = load(STATE_FILE, {})
decisions = decide(state)

print("🧠 ARGUS BRAIN")
print("=" * 50)
print(f"Решений: {len(decisions)}")
print()

executed = []

for d in decisions:
    print(f"▶️ {d['action']}: {d['reason']}")

    ok, msg = trigger_workflow(d["workflow"])

    if ok:
        mark_run(d["action"], state)
        state[f"last_{d['action']}"] = datetime.utcnow().isoformat()
        if d["action"] == "train":
            state["last_train_books"] = len(load("data/summary.json", {}).get("books", []))
        print(f"   ✅ Запущено")
    else:
        print(f"   ❌ Ошибка: {msg}")

    executed.append({
        "time": datetime.utcnow().isoformat(),
        "action": d["action"],
        "reason": d["reason"],
        "success": ok,
        "error": None if ok else msg
    })

# ---------- Сохранение ----------
save(STATE_FILE, state)
save(DECISIONS_FILE, {
    "generated_at": datetime.utcnow().isoformat(),
    "decisions": executed
})

# ---------- Отчёт в Telegram ----------
if BOT_TOKEN and CHAT_ID and executed:
    msg = "🧠 <b>ARGUS Brain</b>\n\n"
    for e in executed:
        icon = "✅" if e["success"] else "❌"
        msg += f"{icon} {e['action']}: {e['reason']}\n"

    try:
        requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            params={"chat_id": CHAT_ID, "text": msg[:4000], "parse_mode": "HTML"},
            timeout=15
        )
        print("\n📤 Отчёт в Telegram")
    except Exception as e:
        print(f"⚠️ Telegram: {e}")

print("\n" + "=" * 50)
print(f"✅ Запущено: {sum(1 for e in executed if e['success'])}/{len(executed)}")
print("=" * 50)