# ============================================================
# ARGUS — BRAIN (Оркестратор)
# v3: сам дёргает Explorer раз в сутки + защита от циклов
# ============================================================

import os
import json
import requests
from datetime import datetime, timedelta

# ---------- Настройки ----------
GITHUB_REPO = os.getenv("GITHUB_REPO", "nitroIm/argus-core")
GITHUB_PAT = os.getenv("GITHUB_PAT")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "data/brain_state.json"
DECISIONS_FILE = "data/brain_decisions.json"

# ---------- Защита от циклов ----------
BRAIN_COOLDOWN_MIN = 20          # Brain не работает чаще 1 раза в 20 минут
ACTION_COOLDOWN_HOURS = 20       # между одинаковыми действиями минимум 20 часов
EXPLORER_COOLDOWN_HOURS = 24     # Explorer — не чаще раза в сутки

# ---------- Дневные лимиты ----------
LIMITS = {
    "observer": 1,
    "analyzer": 1,
    "actor": 1,
    "train": 1,
    "collect": 24,
    "guardian": 2,
    "explorer": 1,
}


# ============================================================
# УТИЛИТЫ
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


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def hours_since(state, key):
    dt = parse_dt(state.get(key))
    if not dt:
        return 999
    return (datetime.utcnow() - dt).total_seconds() / 3600


# ============================================================
# ПРОВЕРКА ЛИМИТОВ
# ============================================================
def can_run(action, state):
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if "runs" not in state:
        state["runs"] = {}

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
    state[f"last_{action}"] = datetime.utcnow().isoformat()


# ============================================================
# ЗАПУСК WORKFLOW
# ============================================================
def trigger_workflow(workflow_file):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    payload = {"ref": "main"}
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        if r.status_code == 204:
            return True, "OK"
        return False, f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


# ============================================================
# РЕШЕНИЯ
# ============================================================
def decide(state):
    decisions = []

    # ---------- 0. Explorer: раз в сутки ----------
    if can_run("explorer", state):
        if hours_since(state, "last_explorer") > EXPLORER_COOLDOWN_HOURS:
            decisions.append({
                "action": "explorer",
                "workflow": "explorer.yml",
                "reason": "Раз в сутки — поиск новых книг",
            })

    obs = load("data/observation.json", {})
    quality = load("data/quality.json", {})
    proposals = load("data/proposals.json", {})

    # ---------- 1. Observer: раз в 23 часа ----------
    if can_run("observer", state):
        if hours_since(state, "last_observer") > 23:
            decisions.append({
                "action": "observer",
                "workflow": "observer.yml",
                "reason": "Раз в день — собрать статистику",
            })

    # ---------- 2. Analyzer: только если есть наблюдения ----------
    if can_run("analyzer", state) and obs.get("total_queries", 0) > 0:
        if hours_since(state, "last_analyzer") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "analyzer",
                "workflow": "analyzer.yml",
                "reason": "Проанализировать наблюдения",
            })

    # ---------- 3. Guardian: раз в сутки ----------
    if can_run("guardian", state) and quality:
        if hours_since(state, "last_guardian") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "guardian",
                "workflow": "guardian.yml",
                "reason": "Проверить качество поиска",
            })

    # ---------- 4. Actor: если есть auto-задачи ----------
    if can_run("actor", state):
        auto_tasks = [p for p in proposals.get("proposals", []) if p.get("can_auto")]
        if auto_tasks and hours_since(state, "last_actor") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "actor",
                "workflow": "actor.yml",
                "reason": f"Выполнить {len(auto_tasks)} авто-задач",
            })

    # ---------- 5. Train: если книг стало больше ----------
    summary = load("data/summary.json", {})
    books_count = len(summary.get("books", []))
    last_train_books = state.get("last_train_books", 0)

    if can_run("train", state) and books_count > last_train_books:
        if hours_since(state, "last_train") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "train",
                "workflow": "train_model.yml",
                "reason": f"Книг стало больше: {last_train_books} → {books_count}",
                "_books_count": books_count,
            })

    return decisions


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
state = load(STATE_FILE, {})

# Cooldown самого Brain
if hours_since(state, "last_brain_run") < (BRAIN_COOLDOWN_MIN / 60):
    print(f"🧠 Brain: cooldown {BRAIN_COOLDOWN_MIN} мин не прошёл — выход.")
    print(f"   Последний запуск: {state.get('last_brain_run')}")
    exit(0)

state["last_brain_run"] = datetime.utcnow().isoformat()

decisions = decide(state)

print("🧠 ARGUS BRAIN")
print("=" * 50)
print(f"Решений: {len(decisions)}")
print()

executed = []

for d in decisions:
    print(f"▶️ {d['action']}: {d['reason']}")

    mark_run(d["action"], state)

    ok, msg = trigger_workflow(d["workflow"])

    if ok:
        if d["action"] == "train" and "_books_count" in d:
            state["last_train_books"] = d["_books_count"]
        print(f"   ✅ Запущено")
    else:
        print(f"   ❌ Ошибка: {msg}")
        state["runs"][d["action"]] = max(0, state["runs"].get(d["action"], 1) - 1)

    executed.append({
        "time": datetime.utcnow().isoformat(),
        "action": d["action"],
        "reason": d["reason"],
        "success": ok,
        "error": None if ok else msg,
    })

# ---------- Сохранение ----------
save(STATE_FILE, state)
save(DECISIONS_FILE, {
    "generated_at": datetime.utcnow().isoformat(),
    "decisions": executed,
})

# ---------- Отчёт в Telegram ----------
if BOT_TOKEN and CHAT_ID and executed:
    msg = "🧠 <b>ARGUS Brain</b>\n\n"
    for e in executed:
        icon = "✅" if e["success"] else "❌"
        msg += f"{icon} {e['action']}: {e['reason']}\n"
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg[:4000], "parse_mode": "HTML"},
            timeout=15,
        )
        print("\n📤 Отчёт в Telegram")
    except Exception as e:
        print(f"⚠️ Telegram: {e}")

print("\n" + "=" * 50)
print(f"✅ Запущено: {sum(1 for e in executed if e['success'])}/{len(executed)}")
print("=" * 50)