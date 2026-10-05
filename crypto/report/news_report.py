# ============================================================
# ARGUS - NEWS REPORT v7
# ------------------------------------------------------------
# v7: should_send_now — only by Kaliningrad hour.
#     No manual override. No GITHUB_EVENT_NAME check.
# v6: SEND_HOURS_LOCAL in Kaliningrad.
# ============================================================

import os
import sys
import json
import hashlib
import logging
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
SENT_HISTORY_FILE = DATA_DIR / "news_sent_history.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.news_report")

sys.path.insert(0, str(SCRIPT_DIR))
TRANSLATE_AVAILABLE = False
try:
    from translate import translate_batch
    TRANSLATE_AVAILABLE = True
except Exception as e:
    log.warning("translate unavailable: " + str(e))
    def translate_batch(t):
        return t

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

MAX_MESSAGE_LEN = 3800
HISTORY_DAYS = 7
STALE_HOURS = 6

# ============================================================
# ВРЕМЯ ОТПРАВКИ (в КАЛИНИНГРАДЕ)
# ============================================================
TZ = ZoneInfo("Europe/Kaliningrad")

# Часы по КЛГ когда слать отчёт в TG.
# 9 = утро, 20 = вечер.
SEND_HOURS_LOCAL = {9, 20}


def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)


def should_send_now():
    """True only if current Kaliningrad hour
    in SEND_HOURS_LOCAL."""
    local = now_local()
    return local.hour in SEND_HOURS_LOCAL


def esc(s):
    if not s:
        return ""
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def split_text(text, max_len):
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current)
            current = line
        else:
            if current:
                current = current + "\n" + line
            else:
                current = line
    if current:
        parts.append(current)
    return parts


def send_message(text):
    if not BOT_TOKEN or not CHAT_ID:
        log.warning("TELEGRAM not configured")
        log.info(text)
        return False
    parts = split_text(text, MAX_MESSAGE_LEN)
    ok_all = True
    for i, part in enumerate(parts):
        try:
            url = "https://api.telegram.org/bot"
            url += BOT_TOKEN + "/sendMessage"
            payload = {
                "chat_id": CHAT_ID,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            r = requests.post(
                url, json=payload, timeout=20,
            )
            if r.status_code != 200:
                log.error(
                    "tg err %d: %s",
                    r.status_code, r.text[:200],
                )
                ok_all = False
        except Exception as e:
            log.exception("send: %s", e)
            ok_all = False
    return ok_all


def load_json(path, default=None):
    if default is None:
        default = {}
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("load %s: %s", path.name, e)
        return default


def save_json(path, data):
    try:
        path.parent.mkdir(
            parents=True, exist_ok=True,
        )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(
                data, f,
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        log.error("save %s: %s", path.name, e)


def _title_hash(text):
    t = (text or "").strip().lower()
    return hashlib.md5(
        t.encode("utf-8"), usedforsecurity=False,
    ).hexdigest()


def file_age_hours(path):
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    now = datetime.now(timezone.utc).timestamp()
    return (now - mtime) / 3600


def fmt_age(h):
    if h is None:
        return "?"
    if h < 1:
        return str(int(h * 60)) + "м"
    if h < 24:
        return format(h, ".1f") + "ч"
    return format(h / 24, ".1f") + "д"


def load_sent_history():
    data = load_json(
        SENT_HISTORY_FILE, {"entries": []},
    )
    if not isinstance(data, dict):
        data = {"entries": []}
    if "entries" not in data:
        data["entries"] = []
    return data


def clean_history(data):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=HISTORY_DAYS)
    entries = []
    for e in data.get("entries", []):
        ts_str = e.get("sent_at")
        if not ts_str:
            continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                entries.append(e)
        except Exception:
            continue
    data["entries"] = entries
    return data


def get_sent_hashes(data):
    out = set()
    for e in data.get("entries", []):
        h = e.get("hash")
        if h:
            out.add(h)
    return out


def add_sent_hash(data, h):
    now_iso = datetime.now(timezone.utc).isoformat()
    data.setdefault("entries", []).append({
        "hash": h,
        "sent_at": now_iso,
    })


def filter_fresh(items, seen):
    out = []
    for it in items:
        t = it.get("title_original")
        if not t:
            t = it.get("title", "")
        h = _title_hash(t)
        if h in seen:
            continue
        out.append(it)
    return out


def translate_top(items, n=3):
    if not items or not TRANSLATE_AVAILABLE:
        return []
    originals = []
    for it in items[:n]:
        t = it.get("title_original")
        if not t:
            t = it.get("title", "")
        if t:
            originals.append(t)
    if not originals:
        return []
    try:
        log.info(
            "translating %d items", len(originals),
        )
        return translate_batch(originals)
    except Exception as e:
        log.exception("translate_top: %s", e)
        return originals


def build_report(
    data, bull_items, bear_items,
    bull_ru, bear_ru, age_h,
):
    mood = data.get("mood", "Неизвестно")
    avg = data.get("avg_sentiment", 0.0)
    total = data.get("total_news", 0)
    bull = data.get("bullish_count", 0)
    bear = data.get("bearish_count", 0)
    neu = data.get("neutral_count", 0)
    fake = data.get("fake_count", 0)
    cross = data.get("cross_confirmed", 0)

    now = now_local()
    lines = []
    lines.append("📰 <b>ARGUS — Новости</b> v7")
    line = now.strftime("%d.%m %H:%M")
    line += " КЛГ"
    lines.append(line)
    lines.append("")

    if age_h is not None:
        icon = "✅" if age_h <= STALE_HOURS else "⚠️"
        line = icon + " Данные: "
        line += fmt_age(age_h) + " назад"
        if age_h > STALE_HOURS:
            line += " (устарели)"
        lines.append(line)
    lines.append("")

    lines.append("🎭 " + esc(mood))
    lines.append(
        "📊 Сентимент: <b>"
        + format(avg, "+.3f") + "</b>"
    )
    lines.append("")

    lines.append("📈 Всего: " + str(total))
    lines.append("🟢 Бычьих: " + str(bull))
    lines.append("🔴 Медвежьих: " + str(bear))
    lines.append("🟡 Нейтральных: " + str(neu))

    if fake:
        lines.append("⚠️ Подозрительных: " + str(fake))
    if cross:
        lines.append(
            "🔍 Подтв. источниками: " + str(cross)
        )
    lines.append("")

    if bull_ru:
        lines.append("🟢 <b>Позитив:</b>")
        for t in bull_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")
    elif bull_items:
        lines.append("🟢 Позитив: новых нет")
        lines.append("")

    if bear_ru:
        lines.append("🔴 <b>Негатив:</b>")
        for t in bear_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")
    elif bear_items:
        lines.append("🔴 Негатив: новых нет")
        lines.append("")

    lines.append(
        "<i>Настроение — один из факторов "
        "прогноза ARGUS.</i>"
    )
    return "\n".join(lines)


def main():
    log.info("news report v7")
    local = now_local()
    log.info(
        "local time: %s КЛГ (hour=%d, send_hours=%s)",
        local.strftime("%H:%M"), local.hour,
        sorted(SEND_HOURS_LOCAL),
    )

    if not should_send_now():
        log.info("skip send — hour not in list")
        return

    if not SENTIMENT_FILE.exists():
        log.error("no sentiment file")
        sys.exit(0)

    data = load_json(SENTIMENT_FILE)
    if not data:
        log.error("sentiment file empty/broken")
        sys.exit(1)

    age_h = file_age_hours(SENTIMENT_FILE)
    log.info("data age: %s",
             fmt_age(age_h) if age_h else "?")

    history = load_sent_history()
    history = clean_history(history)
    seen = get_sent_hashes(history)
    log.info("history: %d entries", len(seen))

    top_bull = data.get("top_bullish", [])
    top_bear = data.get("top_bearish", [])

    bull_fresh = filter_fresh(top_bull, seen)
    bear_fresh = filter_fresh(top_bear, seen)

    log.info(
        "bull: %d total, %d fresh",
        len(top_bull), len(bull_fresh),
    )
    log.info(
        "bear: %d total, %d fresh",
        len(top_bear), len(bear_fresh),
    )

    bull_ru = translate_top(bull_fresh, 3)
    bear_ru = translate_top(bear_fresh, 3)

    text = build_report(
        data, bull_fresh, bear_fresh,
        bull_ru, bear_ru, age_h,
    )
    log.info("report: %d chars", len(text))

    ok = send_message(text)

    if ok:
        for it in bull_fresh[:3]:
            t = it.get("title_original")
            if not t:
                t = it.get("title", "")
            if t:
                add_sent_hash(history, _title_hash(t))
        for it in bear_fresh[:3]:
            t = it.get("title_original")
            if not t:
                t = it.get("title", "")
            if t:
                add_sent_hash(history, _title_hash(t))
        save_json(SENT_HISTORY_FILE, history)
        log.info(
            "history updated: %d entries",
            len(history.get("entries", [])),
        )
        log.info("report sent")
    else:
        log.warning("send failed, no history update")


if __name__ == "__main__":
    main()