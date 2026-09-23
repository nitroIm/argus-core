# ============================================================
# ARGUS - NEWS REPORT v2 [PRODUCTION]
# ------------------------------------------------------------
# Читает crypto/data/news_sentiment.json.
# Переводит ТОЛЬКО топ-3 позитив + топ-3 негатив.
# Отправляет сводку в Telegram.
# ============================================================

import os
import sys
import json
import logging
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.news_report")

# Translate из той же папки
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


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error("load %s: %s", path.name, e)
        return {}


def _get_originals(items, n=3):
    out = []
    for it in items[:n]:
        t = it.get("title_original")
        if not t:
            t = it.get("title", "")
        if t:
            out.append(t)
    return out


def translate_top(data):
    """Переводит title_original топ-3 позитив и топ-3 негатив."""
    if not TRANSLATE_AVAILABLE:
        return [], []

    top_bull = data.get("top_bullish", [])
    top_bear = data.get("top_bearish", [])

    bull_orig = _get_originals(top_bull, 3)
    bear_orig = _get_originals(top_bear, 3)

    bull_ru = []
    bear_ru = []

    try:
        if bull_orig:
            log.info(
                "translating %d bullish",
                len(bull_orig),
            )
            bull_ru = translate_batch(bull_orig)
        if bear_orig:
            log.info(
                "translating %d bearish",
                len(bear_orig),
            )
            bear_ru = translate_batch(bear_orig)
    except Exception as e:
        log.exception("translate_top: %s", e)

    return bull_ru, bear_ru


def build_report(data: dict) -> str:
    mood = data.get("mood", "Неизвестно")
    avg = data.get("avg_sentiment", 0.0)
    total = data.get("total_news", 0)
    bull = data.get("bullish_count", 0)
    bear = data.get("bearish_count", 0)
    neu = data.get("neutral_count", 0)
    fake = data.get("fake_count", 0)
    cross = data.get("cross_confirmed", 0)

    bull_ru, bear_ru = translate_top(data)

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

    if bull_ru:
        lines.append("🟢 <b>Позитив:</b>")
        for t in bull_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")

    if bear_ru:
        lines.append("🔴 <b>Негатив:</b>")
        for t in bear_ru[:3]:
            lines.append("  • " + esc(t[:150]))
        lines.append("")

    lines.append(
        "<i>Настроение — один из факторов "
        "прогноза ARGUS.</i>"
    )

    return "\n".join(lines)


def main() -> None:
    log.info("news report v2")
    log.info("reading: %s", SENTIMENT_FILE)

    if not SENTIMENT_FILE.exists():
        log.error("no sentiment file")
        log.error("run crypto/collect/news.py first")
        sys.exit(0)

    data = load_json(SENTIMENT_FILE)
    if not data:
        log.error("sentiment file empty/broken")
        sys.exit(1)

    text = build_report(data)
    log.info("report: %d chars", len(text))

    ok = send_message(text)
    if ok:
        log.info("report sent")
    else:
        log.warning("send failed")


if __name__ == "__main__":
    main()