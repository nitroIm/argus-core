# ============================================================
# ARGUS — BRAIN (v5)
# v5: pathlib, безопасная работа с часовыми поясами, фикс обрезки HTML, sys.exit
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

GITHUB_REPO = os.getenv("GITHUB_REPO", "nitroIm/argus-core")
GITHUB_PAT = os.getenv("GITHUB_PAT")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

STATE_FILE = DATA_DIR / "brain_state.json"
DECISIONS_FILE = DATA_DIR / "brain_decisions.json"

BRAIN_COOLDOWN_MIN = 20
ACTION_COOLDOWN_HOURS = 20
EXPLORER_COOLDOWN_HOURS = 24

LIMITS = {
    "observer": 1, "analyzer": 1, "proposer": 1, "actor": 1,
    "train": 1, "collect": 24, "guardian": 2, "explorer": 1,
}


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_dt(s):
    """Безопасный парсинг даты, устойчивый к naive/aware конфликтам."""
    if not s:
        return None
    try:
        # Заменяем 'Z' на '+00:00' для совместимости с fromisoformat
        if isinstance(s, str) and s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.fromisoformat(s)
        # Если время "наивное" (без tzinfo), делаем его UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def hours_since(state, key):
    dt = parse_dt(state.get(key))
    if not dt:
        return 999
    now = datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 3600


def can_run(action, state):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    state.setdefault("runs", {})
    if state.get("day") != today:
        state["runs"] = {}
        state["day"] = today
    return state["runs"].get(action, 0) < LIMITS.get(action, 1)


def mark_run(action, state):
    state.setdefault("runs", {})
    state["runs"][action] = state["runs"].get(action, 0) + 1
    state[f"last_{action}"] = datetime.now(timezone.utc).isoformat()


def trigger_workflow(workflow_file):
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/dispatches"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"token {GITHUB_PAT}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        r = requests.post(url, headers=headers, json={"ref": "main"}, timeout=20)
        if r.status_code == 204:
            return True, "OK"
        return False, f"{r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)


def decide(state):
    decisions = []

    # ---------- 0. Explorer ----------
    if can_run("explorer", state) and hours_since(state, "last_explorer") > EXPLORER_COOLDOWN_HOURS:
        decisions.append({
            "action": "explorer", "workflow": "explorer.yml",
            "reason": "Раз в сутки — поиск новых книг",
        })

    obs = load_json(DATA_DIR / "observation.json", {})
    analysis = load_json(DATA_DIR / "analysis.json", {})
    quality = load_json(DATA_DIR / "quality.json", {})
    proposals = load_json(DATA_DIR / "proposals.json", {})

    # ---------- 1. Observer ----------
    if can_run("observer", state) and hours_since(state, "last_observer") > 23:
        decisions.append({
            "action": "observer", "workflow": "observer.yml",
            "reason": "Раз в день — собрать статистику",
        })

    # ---------- 2. Analyzer ----------
    if can_run("analyzer", state) and obs.get("total_queries", 0) > 0:
        if hours_since(state, "last_analyzer") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "analyzer", "workflow": "analyzer.yml",
                "reason": "Проанализировать наблюдения",
            })

    # ---------- 3. Proposer ----------
    if can_run("proposer", state) and analysis.get("findings_count", 0) > 0:
        if hours_since(state, "last_proposer") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "proposer", "workflow": "proposer.yml",
                "reason": f"Сформировать план ({analysis['findings_count']} находок)",
            })

    # ---------- 4. Actor ----------
    if can_run("actor", state):
        auto_tasks = [p for p in proposals.get("proposals", []) if p.get("can_auto")]
        if auto_tasks and hours_since(state, "last_actor") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "actor", "workflow": "actor.yml",
                "reason": f"Выполнить {len(auto_tasks)} авто-задач",
            })

    # ---------- 5. Guardian ----------
    if can_run("guardian", state) and quality:
        if hours_since(state, "last_guardian") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "guardian", "workflow": "guardian.yml",
                "reason": "Проверить качество поиска",
            })

    # ---------- 6. Train ----------
    summary = load_json(DATA_DIR / "summary.json", {})
    books_count = len(summary.get("books", []))
    last_train_books = state.get("last_train_books", 0)

    if can_run("train", state) and books_count > last_train_books:
        if hours_since(state, "last_train") > ACTION_COOLDOWN_HOURS:
            decisions.append({
                "action": "train", "workflow": "train_model.yml",
                "reason": f"Книг стало больше: {last_train_books} → {books_count}",
                "_books_count": books_count,
            })

    return decisions


# ============================================================
# ОСНОВНОЕ
# ============================================================
state = load_json(STATE_FILE, {})

if hours_since(state, "last_brain_run") < (BRAIN_COOLDOWN_MIN / 60):
    print(f"🧠 Brain: cooldown {BRAIN_COOLDOWN_MIN} мин не прошёл — выход.")
    print(f"   Последний запуск: {state.get('last_brain_run')}")
    sys.exit(0)

state["last_brain_run"] = datetime.now(timezone.utc).isoformat()
decisions = decide(state)

print("🧠 ARGUS BRAIN")
print("=" * 50)
print(f"Решений: {len(decisions)}\n")

executed = []
for d in decisions:
    print(f"▶️ {d['action']}: {d['reason']}")
    mark_run(d["action"], state)
    ok, msg = trigger_workflow(d["workflow"])
    if ok:
        if d["action"] == "train" and "_books_count" in d:
            state["last_train_books"] = d["_books_count"]
        print("   ✅ Запущено")
    else:
        print(f"   ❌ {msg}")
        # Откат счётчика при ошибке, чтобы попробовать снова позже
        state["runs"][d["action"]] = max(0, state["runs"].get(d["action"], 1) - 1)
    
    executed.append({
        "time": datetime.now(timezone.utc).isoformat(),
        "action": d["action"], 
        "reason": d["reason"],
        "success": ok, 
        "error": None if ok else msg,
    })

save_json(STATE_FILE, state)
save_json(DECISIONS_FILE, {
    "generated_at": datetime.now(timezone.utc).isoformat(), 
    "decisions": executed
})

# --- Отчёт в Telegram (БЕЗОПАСНАЯ обрезка) ---
if BOT_TOKEN and CHAT_ID and executed:
    # Ограничиваем количество элементов, а не символы, чтобы не разорвать HTML
    report_items = executed[:15]
    
    msg_lines = ["🧠 <b>ARGUS Brain</b>\n"]
    for e in report_items:
        icon = "✅" if e["success"] else "❌"
        safe_reason = e["reason"].replace("<", "&lt;").replace(">", "&gt;")
        msg_lines.append(f"{icon} <b>{e['action']}</b>: {safe_reason}")
    
    if len(executed) > 15:
        msg_lines.append(f"\n<i>...и ещё {len(executed) - 15} действий</i>")
    
    msg = "\n".join(msg_lines)
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"},
            timeout=15,
        )
        print("\n📤 Отчёт в Telegram")
    except Exception as e:
        print(f"⚠️ Telegram: {e}")

print("\n" + "=" * 50)
print(f"✅ Запущено: {sum(1 for e in executed if e['success'])}/{len(executed)}")
print("=" * 50)
