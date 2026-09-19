# ============================================================
# ARGUS — ПРЕДЛОЖЕНИЯ (v2)
# v2: pathlib, сортировка по приоритету, безопасный Telegram, sys.exit
# ============================================================

import os
import sys
import json
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

ANALYSIS_FILE = DATA_DIR / "analysis.json"
OUTPUT_FILE = DATA_DIR / "proposals.json"

# ---------- Проверки ----------
if not ANALYSIS_FILE.exists():
    print("❌ Нет analysis.json. Сначала запусти analyzer.py")
    sys.exit(0)

try:
    with open(ANALYSIS_FILE, "r", encoding="utf-8") as f:
        analysis = json.load(f)
except Exception as e:
    print(f"❌ Ошибка чтения {ANALYSIS_FILE}: {e}")
    sys.exit(1)

findings = analysis.get("findings", [])

# ---------- Формируем предложения ----------
proposals = []

for f in findings:
    action = f.get("action", "unknown")
    severity = f.get("severity", "medium")

    if action == "download_book":
        proposals.append({
            "priority": severity,
            "action": "download_book",
            "topic": f.get("topic", ""),
            "can_auto": True,
            "message": f"📚 Скачать книгу по теме: {f.get('topic', '?')}"
        })

    elif action == "download_multiple":  # Исправлено под логику analyzer.py
        proposals.append({
            "priority": "high",
            "action": "download_multiple",
            "can_auto": True,
            "message": "📚 Загрузить больше книг — слишком много неудачных запросов"
        })

    elif action == "optimize_search":
        proposals.append({
            "priority": "medium",
            "action": "rebuild_index",  # Actor умеет делать rebuild_index
            "can_auto": True,
            "message": "⚡ Оптимизировать поиск — пересобрать индекс из-за медленного отклика"
        })

    elif action == "check_logs":
        proposals.append({
            "priority": "high",
            "action": "run_observer",  # Запуск observer поможет собрать детали
            "can_auto": True,
            "message": "🔍 Запустить Observer для сбора деталей об ошибках"
        })

    elif action == "retrain_index" or action == "rebuild_index":
        proposals.append({
            "priority": "medium",
            "action": "rebuild_index",
            "can_auto": True,
            "message": "🔄 Пересобрать индекс — низкое качество эмбеддингов"
        })

    else:
        # Fallback для неизвестных действий
        proposals.append({
            "priority": severity,
            "action": action,
            "can_auto": False,
            "message": f"⚠️ {f.get('message', 'Неизвестное действие')}"
        })

# ---------- Сортировка по приоритету (high -> medium -> low) ----------
priority_order = {"high": 0, "medium": 1, "low": 2}
proposals.sort(key=lambda x: priority_order.get(x["priority"], 3))

# ---------- Сохранение ----------
output = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "total": len(proposals),
    "auto_possible": sum(1 for p in proposals if p["can_auto"]),
    "manual_needed": sum(1 for p in proposals if not p["can_auto"]),
    "proposals": proposals
}

DATA_DIR.mkdir(parents=True, exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# ---------- Отправка в Telegram (БЕЗОПАСНАЯ) ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    message_lines = ["🧠 <b>ARGUS — Предложения</b>\n"]

    if not proposals:
        message_lines.append("✅ Всё в порядке. Улучшения не требуются.")
    else:
        message_lines.append(f"Всего предложений: {len(proposals)}")
        message_lines.append(f"🤖 Автоматически: {output['auto_possible']}")
        message_lines.append(f"👤 Нужен ты: {output['manual_needed']}\n")

        # Показываем максимум 10 предложений, чтобы не разорвать HTML и не превысить лимит
        display_proposals = proposals[:10]
        for p in display_proposals:
            icon = "🔴" if p["priority"] == "high" else "🟡" if p["priority"] == "medium" else "🟢"
            auto = "🤖" if p["can_auto"] else "👤"
            # Экранируем сообщение на всякий случай
            safe_msg = p['message'].replace("<", "&lt;").replace(">", "&gt;")
            message_lines.append(f"{icon} {auto} {safe_msg}")
            
        if len(proposals) > 10:
            message_lines.append(f"\n<i>...и ещё {len(proposals) - 10} предложений (см. proposals.json)</i>")

    message = "\n".join(message_lines)

    try:
        # Используем POST с json, это надежнее для специальных символов, чем GET с params
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=15
        )
        if r.status_code == 200:
            print("📤 Отправлено в Telegram")
        else:
            print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"⚠️ Ошибка Telegram: {e}")

# ---------- Вывод в консоль ----------
print("🎯 ПРЕДЛОЖЕНИЯ ARGUS")
print("=" * 50)
print(f"Всего: {len(proposals)}")
print(f"Авто: {output['auto_possible']}, Ручных: {output['manual_needed']}")
print()

for p in proposals:
    auto = "🤖" if p["can_auto"] else "👤"
    prio = "🔴" if p["priority"] == "high" else "🟡" if p["priority"] == "medium" else "🟢"
    print(f"{prio} {auto} {p['message']}")
    
print("=" * 50)
