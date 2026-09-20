# ============================================================
# ARGUS — PROPOSER (v3)
# v3: + читает scout_candidates.json, создаёт pending_cards.json,
#     отправляет в Telegram с кнопками ✅/❌
# ============================================================

import os
import sys
import json
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

ANALYSIS_FILE = DATA_DIR / "analysis.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
PENDING_FILE = DATA_DIR / "pending_cards.json"
OUTPUT_FILE = DATA_DIR / "proposals.json"
SENT_FILE = DATA_DIR / "sent_candidates.json"   # уже отправленные (anti-spam)

# --- Настройки ---
MAX_CANDIDATES_PER_RUN = 5       # сколько кандидатов шлём за раз
CANDIDATES_TTL_DAYS = 14         # кандидаты старше — не отправляем

# --- Telegram ---
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


def load_json(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ {path.name}: {e}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def short_id(url: str) -> str:
    """Стабильный короткий id из URL. Совместим с approve_handler.py."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def send_telegram_with_buttons(title: str, url: str, sid: str, topic: str = ""):
    """Отправляет сообщение с inline-кнопками ✅/❌."""
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        return False

    text = (
        f"📚 <b>Новая книга</b>\n\n"
        f"<b>{title[:200]}</b>\n"
        f"{'Тема: ' + topic if topic else ''}\n\n"
        f"<a href=\"{url}\">Открыть PDF</a>"
    )

    # ВАЖНО: callback_data ограничена 64 байтами.
    # Формат: "approve_<sid>" и "reject_<sid>" — влезает
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Скачать", "callback_data": f"approve_{sid}"},
            {"text": "❌ Пропустить", "callback_data": f"reject_{sid}"},
        ]]
    }

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": keyboard,
            },
            timeout=15,
        )
        if r.status_code == 200:
            return True
        print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram ошибка: {e}")
        return False


def main():
    print("🧠 ARGUS PROPOSER v3")
    print("=" * 50)

    # ---------- 1. Обычные предложения из analysis.json ----------
    analysis = load_json(ANALYSIS_FILE, {})
    findings = analysis.get("findings", [])

    proposals = []
    for f in findings:
        action = f.get("action", "unknown")
        severity = f.get("severity", "medium")

        if action == "download_book":
            proposals.append({
                "priority": severity, "action": "download_book",
                "topic": f.get("topic", ""), "can_auto": True,
                "message": f"📚 Скачать книгу по теме: {f.get('topic', '?')}"
            })
        elif action == "download_multiple":
            proposals.append({
                "priority": "high", "action": "download_multiple",
                "can_auto": True,
                "message": "📚 Загрузить больше книг — много неудачных запросов"
            })
        elif action == "optimize_search":
            proposals.append({
                "priority": "medium", "action": "rebuild_index",
                "can_auto": True,
                "message": "⚡ Оптимизировать поиск"
            })
        elif action == "check_logs":
            proposals.append({
                "priority": "high", "action": "run_observer",
                "can_auto": True,
                "message": "🔍 Запустить Observer"
            })
        else:
            proposals.append({
                "priority": severity, "action": action,
                "can_auto": False,
                "message": f"⚠️ {f.get('message', 'Неизвестное действие')}"
            })

    priority_order = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(key=lambda x: priority_order.get(x["priority"], 3))

    # ---------- 2. Кандидаты из scout_candidates.json ----------
    candidates_data = load_json(CANDIDATES_FILE, {"candidates": []})
    all_candidates = candidates_data.get("candidates", [])

    pending = load_json(PENDING_FILE, {})           # {short_id: {...}}
    sent = load_json(SENT_FILE, {"sent_ids": []})
    sent_ids = set(sent.get("sent_ids", []))

    # Фильтруем: не в pending, не отправлены ранее
    fresh = []
    for c in all_candidates:
        url = c.get("url", "")
        if not url:
            continue
        sid = short_id(url)
        if sid in pending or sid in sent_ids:
            continue
        fresh.append({**c, "_sid": sid})

    # Берём MAX_CANDIDATES_PER_RUN свежих
    to_send = fresh[:MAX_CANDIDATES_PER_RUN]

    print(f"\n📋 Предложений (analysis): {len(proposals)}")
    print(f"📚 Свежих кандидатов: {len(fresh)} (из {len(all_candidates)} всего)")
    print(f"📤 Отправим: {len(to_send)}")

    # ---------- 3. Отправка кандидатов с кнопками ----------
    sent_now = 0
    for c in to_send:
        sid = c["_sid"]
        url = c["url"]
        title = c.get("title", "Без названия")
        topic = c.get("topic", "")

        ok = send_telegram_with_buttons(title, url, sid, topic)
        if ok:
            pending[sid] = {
                "url": url,
                "title": title,
                "topic": topic,
                "source": c.get("source", ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            sent_ids.add(sid)
            sent_now += 1
            print(f"  ✅ Отправлено: {title[:60]}")

    # Сохраняем pending и sent
    save_json(PENDING_FILE, pending)
    save_json(SENT_FILE, {"sent_ids": list(sent_ids)})

    # ---------- 4. Сохранение proposals.json ----------
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(proposals),
        "auto_possible": sum(1 for p in proposals if p["can_auto"]),
        "manual_needed": sum(1 for p in proposals if not p["can_auto"]),
        "candidates_sent": sent_now,
        "candidates_pending": len(pending),
        "proposals": proposals,
    }
    save_json(OUTPUT_FILE, output)

    # ---------- 5. Отправка обычных предложений (одним сообщением) ----------
    if BOT_TOKEN and CHAT_ID and proposals:
        lines = ["🧠 <b>ARGUS — Предложения</b>\n"]
        lines.append(f"Всего: {len(proposals)}")
        lines.append(f"🤖 Авто: {output['auto_possible']}")
        lines.append(f"👤 Ручных: {output['manual_needed']}\n")

        for p in proposals[:10]:
            icon = "🔴" if p["priority"] == "high" else "🟡" if p["priority"] == "medium" else "🟢"
            auto = "🤖" if p["can_auto"] else "👤"
            safe = p['message'].replace("<", "&lt;").replace(">", "&gt;")
            lines.append(f"{icon} {auto} {safe}")

        try:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": "\n".join(lines), "parse_mode": "HTML"},
                timeout=15,
            )
        except Exception as e:
            print(f"⚠️ Telegram: {e}")

    # ---------- 6. Вывод ----------
    print("\n" + "=" * 50)
    print(f"✅ Предложений: {len(proposals)}")
    print(f"📤 Кандидатов отправлено: {sent_now}")
    print(f"📥 В pending: {len(pending)}")
    print("=" * 50)


if __name__ == "__main__":
    main()