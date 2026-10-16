# ============================================================
# ARGUS - NEWS REPORT v8
# ------------------------------------------------------------
# v8: + BTC блок (цена, 24ч, RSI, ATR).
#     + ссылки и источники новостей.
#     + VK группа (только утром 09:00).
#     + TG канал (только утром 09:00).
#     + TG личка (утро 09:00 + вечер 20:00).
# v7: should_send_now — only by Kaliningrad hour.
# ============================================================

import os
import sys
import re
import json
import hashlib
import logging
import requests
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import psycopg
    PSYCOPG_OK = True
except ImportError:
    PSYCOPG_OK = False

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
TG_CHANNEL_ID = (
    os.getenv("TG_CHANNEL_ID")
    or ""
).strip()
VK_TOKEN = (os.getenv("VK_TOKEN") or "").strip()
VK_GROUP_ID = (os.getenv("VK_GROUP_ID") or "").strip()
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

MAX_MESSAGE_LEN = 3800
MAX_VK_LEN = 15000
HISTORY_DAYS = 7
STALE_HOURS = 6

SYMBOL = "BTCUSDT"
TIMEFRAME = "15m"

TZ = ZoneInfo("Europe/Kaliningrad")

# Часы по КЛГ
SEND_HOURS_PERSONAL = {9, 20}  # TG личка
SEND_HOURS_GROUP = {9}         # TG канал + VK группа


def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)


def should_send_personal():
    return now_local().hour in SEND_HOURS_PERSONAL


def should_send_group():
    return now_local().hour in SEND_HOURS_GROUP


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


def send_telegram(text, chat_id):
    if not BOT_TOKEN or not chat_id:
        return False
    ok_all = True
    parts = split_text(text, MAX_MESSAGE_LEN)
    for part in parts:
        try:
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": part,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code != 200:
                log.error("tg %s: %s", chat_id, r.status_code)
                ok_all = False
        except Exception as e:
            log.exception("send: %s", e)
            ok_all = False
    return ok_all


def send_vk(text):
    if not VK_TOKEN or not VK_GROUP_ID:
        log.warning("VK not configured")
        return False
    try:
        gid = VK_GROUP_ID.strip()
        if gid.startswith("-"):
            owner_id = gid
        else:
            owner_id = "-" + gid
        url = "https://api.vk.com/method/wall.post"
        params = {
            "owner_id": owner_id,
            "from_group": 1,
            "message": text[:MAX_VK_LEN],
            "access_token": VK_TOKEN,
            "v": "5.199",
        }
        r = requests.post(url, data=params, timeout=30)
        data = r.json()
        if "error" in data:
            log.error("VK err: %s", data["error"])
            return False
        log.info("VK posted: %s", data.get("response"))
        return True
    except Exception as e:
        log.error("VK: %s", e)
        return False


def to_plain(text):
    text = re.sub(
        r"<a href=\"([^\"]+)\">([^<]+)</a>",
        r"\2 (\1)", text,
    )
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    return text


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
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    data = load_json(SENT_HISTORY_FILE, {"entries": []})
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
        log.info("translating %d items", len(originals))
        return translate_batch(originals)
    except Exception as e:
        log.exception("translate_top: %s", e)
        return originals


# ============================================================
# BTC
# ============================================================
def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    ag = gains / period
    al = losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g = d if d > 0 else 0
        l = -d if d < 0 else 0
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    if al == 0:
        return 100.0
    rs = ag / al
    return 100 - 100 / (1 + rs)


def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    if len(trs) < period:
        return None
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
    return a


def fetch_btc_snapshot():
    if not PSYCOPG_OK or not DB_URL:
        return None
    try:
        conn = psycopg.connect(
            DB_URL, connect_timeout=30,
            application_name="argus-news",
        )
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT open_time, close, high, low "
                    "FROM candles WHERE symbol=%s AND timeframe=%s "
                    "AND market_type='spot' "
                    "ORDER BY open_time DESC LIMIT 100",
                    (SYMBOL, TIMEFRAME),
                )
                rows = list(reversed(cur.fetchall()))
        conn.close()
        if len(rows) < 20:
            return None
        closes = [float(r[1]) for r in rows]
        highs = [float(r[2]) for r in rows]
        lows = [float(r[3]) for r in rows]
        price = closes[-1]
        change_pct = 0
        if len(closes) >= 96:
            prev = closes[-96]
            if prev:
                change_pct = (price - prev) / prev * 100
        rsi = _rsi(closes, 14)
        atr = _atr(highs, lows, closes, 14)
        atr_pct = (atr / price * 100) if atr and price else 0
        return {
            "price": price,
            "change_pct": change_pct,
            "rsi": rsi,
            "atr": atr,
            "atr_pct": atr_pct,
        }
    except Exception as e:
        log.error("fetch_btc: %s", e)
        return None


def fmt_price(p):
    if p is None:
        return "?"
    if p >= 1000:
        return "$" + format(int(p), ",")
    if p >= 1:
        return "$" + format(p, ".2f")
    return "$" + format(p, ".4f")


def fmt_pct(v):
    if v is None:
        return "?"
    return format(v, "+.2f") + "%"


# ============================================================
# REPORT
# ============================================================
def build_report(data, bull_items, bear_items,
                 bull_ru, bear_ru, age_h, btc):
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
    lines.append("📰 <b>ARGUS — Новости</b>")
    lines.append(now.strftime("%d.%m %H:%M") + " КЛГ")
    lines.append("")

    # ---- BTC ----
    if btc:
        line = "💰 <b>BTC</b> " + fmt_price(btc["price"])
        line += " | " + fmt_pct(btc["change_pct"]) + " 24ч"
        lines.append(line)
        parts = []
        if btc["rsi"] is not None:
            r = btc["rsi"]
            st = ("перекуп" if r >= 70
                  else "перепрод" if r <= 30 else "нейтр")
            parts.append("RSI " + format(r, ".1f") + " " + st)
        if btc["atr"]:
            parts.append(
                "ATR " + fmt_price(btc["atr"])
                + " (" + format(btc["atr_pct"], ".2f") + "%)"
            )
        if parts:
            lines.append("  " + " | ".join(parts))
        lines.append("")

    # ---- freshness ----
    if age_h is not None:
        icon = "✅" if age_h <= STALE_HOURS else "⚠️"
        line = icon + " Данные: " + fmt_age(age_h) + " назад"
        if age_h > STALE_HOURS:
            line += " (устарели)"
        lines.append(line)
    lines.append("")

    # ---- mood ----
    lines.append("🎭 " + esc(mood))
    lines.append("📊 Сентимент: <b>" + format(avg, "+.3f") + "</b>")
    lines.append("")

    lines.append("📈 Всего: " + str(total))
    lines.append("🟢 Бычьих: " + str(bull))
    lines.append("🔴 Медвежьих: " + str(bear))
    lines.append("🟡 Нейтральных: " + str(neu))
    if fake:
        lines.append("⚠️ Подозрительных: " + str(fake))
    if cross:
        lines.append("🔍 Подтв. источниками: " + str(cross))
    lines.append("")

    # ---- позитив ----
    if bull_ru or bull_items:
        lines.append("🟢 <b>Позитив:</b>")
        for i, it in enumerate(bull_items[:3]):
            title = it.get("title_original") or it.get("title", "")
            if i < len(bull_ru):
                title = bull_ru[i]
            url = it.get("url") or it.get("link") or ""
            src = it.get("source") or ""
            t_esc = esc(title[:180])
            if url:
                lines.append(
                    "  • <a href=\"" + esc(url) + "\">"
                    + t_esc + "</a>"
                )
            else:
                lines.append("  • " + t_esc)
            if src:
                lines.append("    <i>" + esc(src) + "</i>")
        lines.append("")
    else:
        lines.append("🟢 Позитив: новых нет")
        lines.append("")

    # ---- негатив ----
    if bear_ru or bear_items:
        lines.append("🔴 <b>Негатив:</b>")
        for i, it in enumerate(bear_items[:3]):
            title = it.get("title_original") or it.get("title", "")
            if i < len(bear_ru):
                title = bear_ru[i]
            url = it.get("url") or it.get("link") or ""
            src = it.get("source") or ""
            t_esc = esc(title[:180])
            if url:
                lines.append(
                    "  • <a href=\"" + esc(url) + "\">"
                    + t_esc + "</a>"
                )
            else:
                lines.append("  • " + t_esc)
            if src:
                lines.append("    <i>" + esc(src) + "</i>")
        lines.append("")
    else:
        lines.append("🔴 Негатив: новых нет")
        lines.append("")

    lines.append(
        "<i>Настроение — один из факторов "
        "прогноза ARGUS.</i>"
    )
    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================
def main():
    log.info("news report v8")
    local = now_local()
    log.info(
        "local time: %s КЛГ (hour=%d)",
        local.strftime("%H:%M"), local.hour,
    )

    send_p = should_send_personal()
    send_g = should_send_group()

    if not send_p and not send_g:
        log.info("skip — hour not in list")
        return

    if not SENTIMENT_FILE.exists():
        log.error("no sentiment file")
        sys.exit(0)

    data = load_json(SENTIMENT_FILE)
    if not data:
        log.error("sentiment empty/broken")
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

    log.info("bull: %d total, %d fresh",
             len(top_bull), len(bull_fresh))
    log.info("bear: %d total, %d fresh",
             len(top_bear), len(bear_fresh))

    bull_ru = translate_top(bull_fresh, 3)
    bear_ru = translate_top(bear_fresh, 3)

    btc = fetch_btc_snapshot()

    text_tg = build_report(
        data, bull_fresh, bear_fresh,
        bull_ru, bear_ru, age_h, btc,
    )
    text_vk = to_plain(text_tg)

    log.info("report: %d chars", len(text_tg))

    sent_any = False

    # TG личка — утром и вечером
    if send_p:
        log.info("→ TG personal")
        if send_telegram(text_tg, CHAT_ID):
            sent_any = True

    # TG канал + VK — только утром
    if send_g:
        if TG_CHANNEL_ID:
            log.info("→ TG channel")
            send_telegram(text_tg, TG_CHANNEL_ID)
        log.info("→ VK group")
        if send_vk(text_vk):
            sent_any = True

    if sent_any:
        for it in bull_fresh[:3]:
            t = it.get("title_original") or it.get("title", "")
            if t:
                add_sent_hash(history, _title_hash(t))
        for it in bear_fresh[:3]:
            t = it.get("title_original") or it.get("title", "")
            if t:
                add_sent_hash(history, _title_hash(t))
        save_json(SENT_HISTORY_FILE, history)
        log.info("history updated: %d entries",
                 len(history.get("entries", [])))
        log.info("report sent")
    else:
        log.warning("send failed, no history update")


if __name__ == "__main__":
    main()