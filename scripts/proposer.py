# ============================================================
# ARGUS — ПРЕДЛОЖЕНИЯ
# Формирует план действий из анализа
# ============================================================

import os
import json
import requests
from datetime import datetime

ANALYSIS_FILE = "data/analysis.json"
OUTPUT_FILE = "data/proposals.json"

# ---------- Проверки ----------
if not os.path.exists(ANALYSIS_FILE):
    print("❌ Нет analysis.json. Сначала запусти analyzer.")
    exit(0)

with open(ANALYSIS_FILE, "r", encoding="utf-8") as f:
    analysis = json.load(f)

findings = analysis.get("findings", [])

# ---------- Формируем предложения ----------
proposals = []

for f in findings:
    action = f.get("action", "unknown")

    if action == "download_book":
        proposals.append({
            "priority": f["severity"],
            "action": "download_book",
            "topic": f.get("topic", ""),
            "can_auto": True,   # можем сами, если есть источник
            "message": f"📚 Скачать книгу по теме: {f.get('topic', '?')}"
        })

    elif action == "download_books":
        proposals.append({
            "priority": "high",
            "action": "download_multiple",
            "can_auto": True,
            "message": "📚 Загрузить больше книг — слишком много неудачных запросов"
        })

    elif action == "optimize_search":
        proposals.append({
            "priority": "medium",
            "action": "optimize",
            "can_auto": False,   # нужно вмешательство
            "message": "⚡ Оптимизировать поиск — медленный отклик"
        })

    elif action == "check_logs":
        proposals.append({
            "priority": "high",
            "action": "investigate",
            "can_auto": False,
            "message": "🔍 Проверить логи — много ошибок"
        })

    elif action == "retrain_index":
        proposals.append({
            "priority": "medium",
            "action": "rebuild_index",
            "can_auto": True,
            "message": "🔄 Пересобрать индекс — низкое качество эмбеддингов"
        })

    else:
        proposals.append({
            "priority": f["severity"],
            "action": action,
            "can_auto": False,
            "message": f["message"]
        })

# ---------- Сохранение ----------
output = {
    "generated_at": datetime.utcnow().isoformat(),
    "total": len(proposals),
    "auto_possible": sum(1 for p in proposals if p["can_auto"]),
    "manual_needed": sum(1 for p in proposals if not p["can_auto"]),
    "proposals": proposals
}

os.makedirs("data", exist_ok=True)
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

# ---------- Отправка в Telegram ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    message = "🧠 <b>ARGUS — Предложения</b>\n\n"

    if not proposals:
        message += "✅ Всё в порядке. Улучшения не требуются."
    else:
        message += f"Всего предложений: {len(proposals)}\n"
        message += f"🤖 Автоматически: {output['auto_possible']}\n"
        message += f"👤 Нужен ты: {output['manual_needed']}\n\n"

        for i, p in enumerate(proposals, 1):
            icon = "🔴" if p["priority"] == "high" else "🟡"
            auto = "🤖" if p["can_auto"] else "👤"
            message += f"{icon} {auto} {p['message']}\n"

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
        print("📤 Отправлено в Telegram")
    except Exception as e:
        print(f"⚠️ Ошибка Telegram: {e}")

# ---------- Вывод ----------
print("🎯 ПРЕДЛОЖЕНИЯ ARGUS")
print("=" * 50)
print(f"Всего: {len(proposals)}")
print(f"Авто: {output['auto_possible']}, Ручных: {output['manual_needed']}")
print()

for p in proposals:
    auto = "🤖" if p["can_auto"] else "👤"
    print(f"{auto} {p['message']}")

print("=" * 50)