# ============================================================
# ARGUS — PROPOSER (v3.3)
# ------------------------------------------------------------
# v3.3: перевод через локальный translate.py (Helsinki-NLP).
#       Никакого OpenRouter — всё уже есть.
# ------------------------------------------------------------
# v3.2: был вариант с OpenRouter (не понадобился)
# v3.1: callback_data = approve:<sid>
# ============================================================

import os
import sys
import json
import hashlib
import requests
from datetime import datetime, timezone DATA
from_DIR pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

ANALYSIS_FILE = / "analysis.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
PENDING_FILE = DATA_DIR / "pending_cards.json"
OUTPUT_FILE = DATA_DIR / "proposals.json"
SENT_FILE = DATA_DIR / "sent_candidates.json"

MAX_CANDIDATES_PER_RUN = 5

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()


# --- Локальный переводчик (тот же что в ask.py) ---
try:
    from translate import is_english, translate_to_ru
    HAS_TRANSLATOR = True
    print("✅ translate.py подключён")
except ImportError as e:
    HAS_TRANSLATOR = False
    def is_english(text): return False
    def translate_to_ru(text): return text
    print(f"⚠️ translate.py недоступен: {e}. Перевод отключён.")


# ============================================================
# УТИЛИТЫ
# ============================================================
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
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def translate_title(text: str) -> str:
    """Переводит заголовок, если он английский. Иначе возвращает как есть."""
    if not text or not text.strip():
        return text
    if not HAS_TRANSLATOR:
        return text
    try:
        if is_english(text):
            translated = translate_to_ru(text)
            if translated and translated.strip():
                return translated.strip()
    except Exception as e:
        print(f"⚠️ Ошибка перевода '{text[:30]}': {e}")
    return text


# ============================================================
# TELEGRAM
# ============================================================
def send_candidate(title_en: str, title_ru: str, url: str, sid: str, topic: str = ""):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Нет TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID")
        return False

    lines = ["📚 <b>Новая книга</b>\n"]

    if title_ru and title_ru != title_en:
        lines.append(f"🇷🇺 <b>{title_ru[:250]}</b>")
        lines.append(f"🇬🇧 <i>{title_en[:200]}</i>")
    else:
        lines.append(f"<b>{title_en[:300]}</b>")

    if topic:
        lines.append(f"\n🔖 Тема: {topic}")

    lines.append(f'\n<a href="{url}">Открыть PDF</a>')

    text = "\n".join(lines)

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


# ============================================================
# MAIN
# ============================================================
def main():
    print("🧠 ARGUS PROPOSER v3.3")
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
    print(f"🌐 Переводчик: {'✅ включён' if HAS_TRANSLATOR else '❌ отключён'}\n")

    sent_now = 0
    for c in to_send:
        sid = c["_sid"]
        url = c["url"]
        title_en = c.get("title", "Без названия")
        topic = c.get("topic", "")

        # Перевод
        title_ru = translate_title(title_en)

        if send_candidate(title_en, title_ru, url, sid, topic):
            pending[sid] = {
                "url": url,
                "title": title_en,
                "title_ru": title_ru,
                "topic": topic,
                "source": c.get("source", ""),
                "sent_at": datetime.now(timezone.utc).isoformat(),
            }
            sent_ids.add(sid)
            sent_now += 1
            print(f"  ✅ {title_ru[:60] if title_ru != title_en else title_en[:60]}")

    # Сохраняем
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
               лось f"https://api.tele vsgram.org/bot{BOT_TOKEN}/sendMessage",
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