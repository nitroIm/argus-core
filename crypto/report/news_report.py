# ============================================================
# ARGUS - NEWS REPORT v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: дедупликация — не отправляет одни и те же заголовки.
#     История отправленных хранится 7 дней.
# v2: перевод только топ-3+3
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


# ============================================================
# HELPERS
# ============================================================
def esc(s) -> str:
    if not s:
        return ""
    s = str(s)
    s = s.replace("&", "&amp;")
    s = s.replace("<", "&lt;")
    s = s.replace(">", "&gt;")
    return s


def split_text(text: str, max_len: int) -> list:
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


def send_message(text: str) -> bool:
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
            else:
                log.info(
                    "part %d/%d sent",
                    i + 1, len(parts),
                )
        except Exception as e:
            log.exception("send: %s", e)
            ok_all = False

    return ok_all


def load_json(path: Path, default=None) -> dict:
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


def save_json(path: Path, data) -> None:
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


def _title_hash(text: str) -> str:
    t = (text or "").strip().lower()
    return hashlib.md5(t.encode("utf-8")).hexdigest()


# ============================================================
# HISTORY
# ============================================================
def load_sent_history() -> dict:
    data = load_json(
        SENT_HISTORY_FILE,
        {"entries": []},
    )
    if not isinstance(data, dict):
        data = {"entries": []}
    if "entries" not in data:
        data["entries"] = []
    return data


def clean_history(data: dict) -> dict:
    """Убирает записи старше HISTORY_DAYS."""
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


def get_sent_hashes(data: dict) -> set:
    out = set()
    for e in data.get("entries", []):
        h = e.get("hash")
        if h:
            out.add(h)
    return out


def add_sent_hash(data: dict, h: str) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    data.setdefault("entries", []).append({
        "hash": h,
        "sent_at": now_iso,
    })


# ============================================================
# TOP SELECTION
# ============================================================
def filter_fresh(items: list, seen: set) -> list:
    """Оставляет только те что не отправлялись."""
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


def translate_top(items: list, n: int = 3) -> list:
    """Переводит топ-N новостей."""
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


# ============================================================
# REPORT
# ============================================================
def build_report(
    data: dict,
    bull_items: list,
    bear_items: list,
    bull_ru: list,
    bear_ru: list,
) -> str:
    mood = data.get("mood", "Неизвестно")
    avg = data.get("avg_sentiment", 0.0)
    total = data.get("total_news", 0)
    bull = data.get("bullish_count", 0)
    bear = data.get("bearish_count", 0)
    neu = data.get("neutral_count", 0)
    fake = data.get("fake_count", 0)
    cross = data.get("cross_confirmed", 0)

    lines = []
    lines.append("📰 <b>ARGUS — Настроение рынка</b>")
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

    # Позитив
    if bull_ru:
        lines.append("🟢 <b>Позитив:</b>")
        for t in bull_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")
    elif bull_items:
        # Есть позитив, но всё уже отправлялось
        lines.append("🟢 <b>Позитив:</b> новых нет")
        lines.append("")

    # Негатив
    if bear_ru:
        lines.append("🔴 <b>Негатив:</b>")
        for t in bear_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")
    elif bear_items:
        lines.append("🔴 <b>Негатив:</b> новых нет")
        lines.append("")

    lines.append(
        "<i>Настроение — один из факторов "
        "прогноза ARGUS.</i>"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main() -> None:
    log.info("news report v3")
    log.info("reading: %s", SENTIMENT_FILE)

    if not SENTIMENT_FILE.exists():
        log.error("no sentiment file")
        log.error("run crypto/collect/news.py first")
        sys.exit(0)

    data = load_json(SENTIMENT_FILE)
    if not data:
        log.error("sentiment file empty/broken")
        sys.exit(1)

    # История отправленных
    history = load_sent_history()
    history = clean_history(history)
    seen = get_sent_hashes(history)
    log.info(
        "history: %d entries", len(seen),
    )

    # Фильтруем
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

    # Переводим топ-3 из свежих
    bull_ru = translate_top(bull_fresh, 3)
    bear_ru = translate_top(bear_fresh, 3)

    # Строим сообщение
    text = build_report(
        data,
        bull_fresh, bear_fresh,
        bull_ru, bear_ru,
    )
    log.info("report: %d chars", len(text))

    ok = send_message(text)

    # Записываем в историю — что отправили
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
        log.warning("send failed, history not updated")


if __name__ == "__main__":
    main()