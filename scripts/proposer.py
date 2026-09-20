# ============================================================
# ARGUS — PROPOSER (v3.1)
# v3.1: callback_data в формате approve:<sid> / reject:<sid>
#       — совместимо с bot_host.py
# ============================================================

import os
import sys
import json
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

ANALYSIS_FILE = DATA_DIR / "analysis.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
PENDING_FILE = DATA_DIR / "pending_cards.json"
OUTPUT_FILE = DATA_DIR / "proposals.json"
SENT_FILE = DATA_DIR / "sent_candidates.json"

MAX_CANDIDATES_PER_RUN = 5

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
    """md5(url)[:16] — совместимо с approve_handler.py."""
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def send_candidate(title: str, url: str, sid: str, topic: str = ""):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        return False

    text = (
        f"📚 <b>Новая книга</b>\n\n"
        f"<b>{title[:200]}</b>\n"
        f"{'Тема: ' + topic if topic else ''}\n\n"
        f"<a href=\"{url}\">Открыть PDF</a>"
    )

    # ВАЖНО: формат approve:<sid> — как ловит bot_host.py
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Скачать", "callback_data": f"approve:{sid}"},
            {"text": "❌ Пропустить", "callback_data": f"reject:{sid}"},
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
        print(f"⚠️ Telegram: {e}")
        return False


def main():
    print("🧠 ARGUS PROPOSER v3.1")
    print("=" * 50)

    # ---- analysis.json ----
    analysis = load_json(ANALYSIS_FILE, {})
    findings = analysis.get("findings", [])

    proposals = []
    for f in findings:
        action = f.get("action", "unknown")
        severity = f.get("severity", "medium")

        if action == "download_book":
            proposals.append({"priority": severity, "action": "download_book",
                              "can_auto": True,
                              "message": f"📚 Скачать: {f.get('topic', '?')}"})
        elif action == "download_multiple":
            proposals.append({"priority": "high", "action": "download_multiple",
                              "can_auto": True, "message": "📚 Загрузить больше книг"})
        elif action == "optimize_search":
            proposals.append({"priority": "medium", "action": "rebuild_index",
                              "can_auto": True, "message": "⚡ Пересобрать индекс"})
        elif action == "check_logs":
            proposals.append({"priority": "high", "action": "run_observer",
                              "can_auto": True, "message": "🔍 Запустить Observer"})
        else:
            proposals.append({"priority": severity, "action": action,
                              "can_auto": False,
                              "message": f"⚠️ {f.get('message', 'Неизвестное действие')}"})

    priority_order = {"high": 0, "medium": 1, "low": 2}
    proposals.sort(key=lambda x: priority_order.get(x["priority"], 3))

    # ---- scout_candidates.json ----
    candidates_data = load_json(CANDIDATES_FILE, {"candidates": []})
    all_candidates = candidates_data.get("candidates", [])

    pending = load_json(PENDING_FILE, {})
    sent = load_json(SENT_FILE, {"sent_ids": []})
    sent_ids = set(sent.get("sent_ids", []))

    fresh = []
    for c in all_candidates:
        url = c.get("url", "")
        if not url:
            continue
        sid = short_id(url)
        if sid in pending or sid in sent_ids:
            continue
        fresh.append({**c, "_sid": sid})

    to_send = fresh[:MAX_CANDIDATES_PER_RUN]

    print(f"\n📋 Предложений: {len(proposals)}")
    print(f"📚 Свежих кандидатов: {len(fresh)} (из {len(all_candidates)})")
    print(f"📤 Отправим: {len(to_send)}")

    sent_now = 0
    for c in to_send:
        sid = c["_sid"]
        url = c["url"]
        title = c.get("title", "Без названия")
        topic = c.get("topic", "")

        if send_candidate(title, url, sid, topic):
            pending[sid] = {
                "url": url,
                "title": title,
                "topic": topic,
                "source": c.get("source", ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            sent_ids.add(sid)
            sent_now += 1
            print(f"  ✅ {title[:60]}")

    save_json(PENDING_FILE, pending)
    save_json(SENT_FILE, {"sent_ids": list(sent_ids)})

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

    # ---- Обычные предложения ----
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

    print("\n" + "=" * 50)
    print(f"✅ Предложений: {len(proposals)}")
    print(f"📤 Кандидатов отправлено: {sent_now}")
    print(f"📥 В pending: {len(pending)}")
    print("=" * 50)


if __name__ == "__main__":
    main()