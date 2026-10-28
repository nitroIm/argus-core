# ============================================================
# ARGUS - EVENING NEWS REPORT v2.2 (PATH UPDATE)
# ------------------------------------------------------------
# v2.2: Путь к данным изменен на crypto/data/news/
# v2.1: Исправлено: хватает данных для EMA(50) и ATR.
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
# ИЗМЕНЕНИЕ ЗДЕСЬ: добавлена папка "news"
DATA_DIR = CRYPTO_ROOT / "data" / "news"
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
SENT_HISTORY_FILE = DATA_DIR / "news_sent_history_evening.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.evening_news")

sys.path.insert(0, str(SCRIPT_DIR))
try:
    from translate import translate_batch
    TRANSLATE_AVAILABLE = True
except Exception:
    TRANSLATE_AVAILABLE = False
    def translate_batch(t): return t

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TG_CHANNEL_ID = (os.getenv("TG_CHANNEL_ID") or "").strip()
VK_TOKEN = (os.getenv("VK_TOKEN") or "").strip()
VK_GROUP_ID = (os.getenv("VK_GROUP_ID") or "").strip()
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

TEST_MODE = (os.getenv("NEWS_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"))

MAX_MESSAGE_LEN = 3800
MAX_VK_LEN = 15000
HISTORY_DAYS = 7
SYMBOL = "BTCUSDT"
TIMEFRAME = "15m"
TZ = ZoneInfo("Europe/Kaliningrad")

def now_local():
    return datetime.now(timezone.utc).astimezone(TZ)

def esc(s):
    if not s: return ""
    s = str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def split_text(text, max_len):
    if len(text) <= max_len: return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current: parts.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current: parts.append(current)
    return parts

def send_telegram(text, chat_id):
    if not BOT_TOKEN or not chat_id: return False
    ok_all = True
    for part in split_text(text, MAX_MESSAGE_LEN):
        try:
            url = "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage"
            r = requests.post(url, json={"chat_id": chat_id, "text": part, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=20)
            if r.status_code != 200: ok_all = False
        except Exception: ok_all = False
    return ok_all

def send_vk(text):
    if not VK_TOKEN or not VK_GROUP_ID: return False
    try:
        gid = VK_GROUP_ID.strip()
        owner_id = gid if gid.startswith("-") else "-" + gid
        url = "https://api.vk.com/method/wall.post"
        params = {"owner_id": owner_id, "from_group": 1, "message": text[:MAX_VK_LEN], "access_token": VK_TOKEN, "v": "5.199"}
        r = requests.post(url, data=params, timeout=30)
        return "error" not in r.json()
    except Exception: return False

def to_plain(text):
    text = re.sub(r"<a href=\"([^\"]+)\">([^<]+)</a>", r"\2 (\1)", text)
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _title_hash(text):
    return hashlib.md5((text or "").strip().lower().encode("utf-8")).hexdigest()

def filter_fresh(items, seen):
    out = []
    for it in items:
        t = it.get("title_original") or it.get("title", "")
        if _title_hash(t) not in seen: out.append(it)
    return out

def translate_top(items, n=3):
    if not items or not TRANSLATE_AVAILABLE: return []
    originals = [it.get("title_original") or it.get("title", "") for it in items[:n]]
    try: return translate_batch(originals)
    except Exception: return originals

# ============================================================
# ДИНАМИЧЕСКИЕ ВИЗУАЛЬНЫЕ ШКАЛЫ
# ============================================================
def get_progress_bar(pct, length=12):
    filled = int(round(pct / 100 * length))
    filled = max(0, min(filled, length))
    return "▕" + "█" * filled + "░" * (length - filled) + "▏"

def get_range_bar(price, low, high, length=12):
    if high == low: return "▕░░░░░░░░░░░░▏"
    pct = (price - low) / (high - low) * 100
    filled = int(round(pct / 100 * length))
    filled = max(0, min(filled, length))
    return "▕" + "█" * filled + "░" * (length - filled) + "▏"

# ============================================================
# СБОР ДАННЫХ ЗА ДЕНЬ
# ============================================================
def fetch_btc_day_stats():
    if not PSYCOPG_OK or not DB_URL: return None
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume FROM candles WHERE symbol=%s AND timeframe=%s AND market_type='spot' ORDER BY open_time DESC LIMIT 250", (SYMBOL, TIMEFRAME))
                rows = list(reversed(cur.fetchall()))
        conn.close()
        
        now_kly = now_local()
        today_start_kly = now_kly.replace(hour=8, minute=0, second=0, microsecond=0)
        today_start_utc = today_start_kly.astimezone(timezone.utc)

        day_rows = []
        for r in rows:
            dt = r[0]
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            if dt >= today_start_utc: day_rows.append(r)

        if not day_rows: return None

        open_p = float(day_rows[0][1])
        price = float(rows[-1][4])
        change_pct = (price - open_p) / open_p * 100 if open_p else 0
        high = max(float(r[2]) for r in day_rows)
        low = min(float(r[3]) for r in day_rows)
        
        vol_total = sum(float(r[5]) for r in day_rows)
        vol_buy = sum(float(r[6]) for r in day_rows)
        vol_sell = vol_total - vol_buy
        buy_pct = (vol_buy / vol_total * 100) if vol_total else 50
        sell_pct = 100 - buy_pct

        candles_1h = []
        for i in range(0, len(rows) - 3, 4):
            chunk = rows[i:i+4]
            if len(chunk) < 4: continue
            h = max(float(c[2]) for c in chunk)
            l = min(float(c[3]) for c in chunk)
            c = float(chunk[-1][4])
            candles_1h.append((h, l, c))
            
        closes_1h = [c[2] for c in candles_1h]
        highs_1h = [c[0] for c in candles_1h]
        lows_1h = [c[1] for c in candles_1h]

        rsi_1h = None
        if len(closes_1h) > 14:
            gains, losses = 0, 0
            for i in range(1, 15):
                d = closes_1h[-i] - closes_1h[-i-1]
                if d >= 0: gains += d
                else: losses -= d
            if losses == 0: rsi_1h = 100
            else: rsi_1h = 100 - (100 / (1 + (gains/14)/(losses/14)))

        ema_50 = sum(closes_1h[-50:]) / 50 if len(closes_1h) >= 50 else None

        atr_1h = None
        if len(candles_1h) > 14:
            trs = []
            for i in range(len(candles_1h)-14, len(candles_1h)):
                if i == 0: continue
                tr = max(highs_1h[i] - lows_1h[i], abs(highs_1h[i] - closes_1h[i-1]), abs(lows_1h[i] - closes_1h[i-1]))
                trs.append(tr)
            if trs: atr_1h = sum(trs) / len(trs)
            
        atr_pct = (atr_1h / price * 100) if atr_1h and price else 0

        return {
            "open": open_p, "price": price, "change_pct": change_pct,
            "high": high, "low": low,
            "vol_buy": vol_buy, "vol_sell": vol_sell,
            "buy_pct": buy_pct, "sell_pct": sell_pct,
            "rsi": rsi_1h, "ema_50": ema_50, "atr": atr_1h, "atr_pct": atr_pct
        }
    except Exception as e:
        log.error("fetch_btc: %s", e)
        return None

def fmt_price(p):
    return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")

def fmt_pct(v):
    return format(v, "+.2f") + "%"

def fmt_btc(v):
    return format(v, ",.1f") + " BTC"

# ============================================================
# ОТЧЕТ
# ============================================================
def build_report(data, top_items, top_ru, btc):
    now = now_local()
    lines = []

    if btc:
        if btc["change_pct"] > 0.2:
            header = "🐂 <b>Быки удержали инициативу: Итоги дня BTC</b>"
        elif btc["change_pct"] < -0.2:
            header = "🐻 <b>Медведи продавили рынок: Итоги дня BTC</b>"
        else:
            header = "😐 <b>Боковик: Итоги дня BTC</b>"
    else:
        header = "🌙 <b>ARGUS — Вечерний дайджест</b>"

    lines.append(header)
    lines.append(now.strftime("%d.%m %H:%M"))
    lines.append("")

    if btc:
        lines.append("💰 <b>Цена за день:</b>")
        lines.append(f"  Утром: {fmt_price(btc['open'])} → Вечером: {fmt_price(btc['price'])}")
        lines.append(f"  Изменение: <b>{fmt_pct(btc['change_pct'])}</b>")
        
        bar_price = get_range_bar(btc["price"], btc["low"], btc["high"])
        lines.append(f"  Мин `{fmt_price(btc['low'])}` {bar_price} Макс `{fmt_price(btc['high'])}`")
        lines.append("")

        lines.append("⚔️ <b>Битва объемов:</b>")
        
        if btc["buy_pct"] > 53: verdict = "Покупатели доминировали."
        elif btc["sell_pct"] > 53: verdict = "Продавцы доминировали."
        else: verdict = "Равенство сил."

        bar_buy = get_progress_bar(btc["buy_pct"])
        bar_sell = get_progress_bar(btc["sell_pct"])

        lines.append(f"  🟢 Покупки: {fmt_btc(btc['vol_buy'])} ({btc['buy_pct']:.1f}%)")
        lines.append(f"  {bar_buy}")
        lines.append(f"  🔴 Продажи: {fmt_btc(btc['vol_sell'])} ({btc['sell_pct']:.1f}%)")
        lines.append(f"  {bar_sell}")
        lines.append(f"  <i>Вердикт: {verdict}</i>")
        lines.append("")

        lines.append("🩺 <b>Техническая картина (1ч):</b>")
        if btc["ema_50"]:
            trend = "выше EMA(50) — бычий настрой" if btc["price"] > btc["ema_50"] else "ниже EMA(50) — медвежий настрой"
            lines.append(f"  • Тренд: Цена {trend}")
        if btc["rsi"]:
            st = "нейтрально" if 30 < btc["rsi"] < 70 else ("перекупленность" if btc["rsi"] >= 70 else "перепроданность")
            lines.append(f"  • Сила (RSI): {btc['rsi']:.1f} — {st}")
        if btc["atr"]:
            lines.append(f"  • Волатильность (ATR): {fmt_price(btc['atr'])} ({btc['atr_pct']:.2f}%)")
        lines.append("")

    if top_items:
        lines.append("📰 <b>Главное за день:</b>")
        for i, it in enumerate(top_items[:3]):
            title = it.get("title_original") or it.get("title", "")
            if i < len(top_ru): title = top_ru[i]
            url = it.get("url") or it.get("link") or ""
            t_esc = esc(title[:180])
            
            if url: lines.append(f"  • <a href=\"{esc(url)}\">{t_esc}</a>")
            else: lines.append(f"  • {t_esc}")
        lines.append("")

    if btc:
        lines.append("🎯 <b>На завтра:</b>")
        lines.append(f"  • Сопротивление: {fmt_price(btc['high'])}")
        lines.append(f"  • Поддержка: {fmt_price(btc['low'])}")
        lines.append("")

    lines.append("────────────────────")
    lines.append("🔥 <b>Хотите торговать на Bybit?</b>")
    lines.append("Бонусы до $1720 для новых пользователей:")
    lines.append("<a href=\"https://www.bybit.com/invite?ref=JV3355&medium=referral&utm_campaign=evergreen\">👉 Забрать бонус на Bybit</a>")
    lines.append("")
    lines.append("<i>Хорошего вечера! До встречи завтра утром.</i>")
    
    return "\n".join(lines)

def main():
    log.info("evening news report start")
    if not SENTIMENT_FILE.exists(): return

    data = load_json(SENTIMENT_FILE)
    history = load_json(SENT_HISTORY_FILE, {"entries": []})
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    history["entries"] = [e for e in history.get("entries", []) if datetime.fromisoformat(e["sent_at"]) >= cutoff]
    seen = {e["hash"] for e in history["entries"]}

    all_top = data.get("top_bullish", []) + data.get("top_bearish", [])
    all_top.sort(key=lambda x: x.get("effective_weight", 0), reverse=True)
    fresh_items = filter_fresh(all_top, seen)[:3]
    top_ru = translate_top(fresh_items, 3)
    
    btc = fetch_btc_day_stats()

    text_tg = build_report(data, fresh_items, top_ru, btc)
    text_vk = to_plain(text_tg)

    log.info("sending evening report to TG Channel and VK...")
    
    if TG_CHANNEL_ID: send_telegram(text_tg, TG_CHANNEL_ID)
    if VK_GROUP_ID: send_vk(text_vk)

    for it in fresh_items:
        history["entries"].append({"hash": _title_hash(it.get("title_original", "")), "sent_at": datetime.now(timezone.utc).isoformat()})
    save_json(SENT_HISTORY_FILE, history)
    log.info("evening report sent")

if __name__ == "__main__":
    main()