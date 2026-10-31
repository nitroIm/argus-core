# ============================================================
# ARGUS - NEWS REPORT v12.5 (TRUE VK BARS FIX)
# Path: crypto/report/news_report.py
# ============================================================

import os
import sys
import re
import json
import hashlib
import logging
import requests
import io
import pandas as pd
import psycopg
import mplfinance as mpf
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data" / "news"
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
SENT_HISTORY_FILE = DATA_DIR / "news_sent_history.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.news_report")

sys.path.insert(0, str(SCRIPT_DIR))
TRANSLATE_AVAILABLE = False
try:
    from translate import translate_batch
    TRANSLATE_AVAILABLE = True
except Exception as e:
    log.warning("translate unavailable: " + str(e))
    def translate_batch(t): return t

BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
TG_CHANNEL_ID = (os.getenv("TG_CHANNEL_ID") or "").strip()
VK_TOKEN = (os.getenv("VK_TOKEN") or "").strip()
VK_GROUP_ID = (os.getenv("VK_GROUP_ID") or "").strip()
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
CMC_API_KEY = (os.getenv("CMC_API_KEY") or "").strip()

TEST_MODE = (os.getenv("NEWS_TEST_MODE", "").strip().lower() in ("1", "true", "yes", "on"))
MAX_MESSAGE_LEN = 3800
MAX_VK_LEN = 15000
HISTORY_DAYS = 7
SYMBOL = "BTCUSDT"
TIMEFRAME = "15m"
TZ = ZoneInfo("Europe/Kaliningrad")

def now_local(): return datetime.now(timezone.utc).astimezone(TZ)
def should_send_group(): return True if TEST_MODE else now_local().hour == 9

def esc(s):
    if not s: return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def split_text(text, max_len):
    if len(text) <= max_len: return [text]
    parts, current = [], ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current: parts.append(current)
            current = line
        else: current = current + "\n" + line if current else line
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

def upload_photo_to_vk(photo_bytes):
    if not VK_TOKEN or not VK_GROUP_ID: 
        log.error("VK: No token or group ID")
        return None
    try:
        gid = VK_GROUP_ID.replace("-", "")
        url = f"https://api.vk.com/method/photos.getWallUploadServer?group_id={gid}&access_token={VK_TOKEN}&v=5.199"
        r = requests.get(url, timeout=20).json()
        
        if "error" in r: 
            log.error(f"VK getWallUploadServer Error: {r['error']}")
            return None
            
        upload_url = r["response"]["upload_url"]
        files = {'photo': ('chart.png', photo_bytes)}
        r = requests.post(upload_url, files=files, timeout=30).json()
        
        if "error" in r:
            log.error(f"VK Upload Error: {r}")
            return None
            
        url = "https://api.vk.com/method/photos.saveWallPhoto"
        params = {"group_id": gid, "photo": r["photo"], "server": r["server"], "hash": r["hash"], "access_token": VK_TOKEN, "v": "5.199"}
        res = requests.post(url, data=params, timeout=20).json()
        
        if "error" in res: 
            log.error(f"VK saveWallPhoto Error: {res['error']}")
            return None
            
        saved = res["response"][0]
        return f"photo{saved['owner_id']}_{saved['id']}"
    except Exception as e:
        log.error(f"VK Photo Upload Error: {e}")
        return None

def send_vk(text, photo_attachment=None):
    if not VK_TOKEN or not VK_GROUP_ID: return False
    try:
        gid = VK_GROUP_ID.strip()
        owner_id = gid if gid.startswith("-") else "-" + gid
        url = "https://api.vk.com/method/wall.post"
        params = {
            "owner_id": owner_id, "from_group": 1, 
            "message": text[:MAX_VK_LEN], 
            "access_token": VK_TOKEN, "v": "5.199"
        }
        if photo_attachment: params["attachments"] = photo_attachment
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
    return hashlib.md5((text or "").strip().lower().encode("utf-8"), usedforsecurity=False).hexdigest()

def load_sent_history():
    data = load_json(SENT_HISTORY_FILE, {"entries": []})
    if not isinstance(data, dict): data = {"entries": []}
    if "entries" not in data: data["entries"] = []
    return data

def clean_history(data):
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    entries = []
    for e in data.get("entries", []):
        ts_str = e.get("sent_at")
        if not ts_str: continue
        try:
            ts = datetime.fromisoformat(ts_str)
            if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff: entries.append(e)
        except Exception: continue
    data["entries"] = entries
    return data

def get_sent_hashes(data):
    return {e.get("hash") for e in data.get("entries", []) if e.get("hash")}

def add_sent_hash(data, h):
    data.setdefault("entries", []).append({"hash": h, "sent_at": datetime.now(timezone.utc).isoformat()})

def filter_fresh(items, seen):
    out = []
    for it in items:
        t = it.get("title_original") or it.get("title", "")
        if _title_hash(t) not in seen: out.append(it)
    return out

def translate_top(items, n=5):
    if not items or not TRANSLATE_AVAILABLE: return []
    originals = [it.get("title_original") or it.get("title", "") for it in items[:n] if (it.get("title_original") or it.get("title", ""))]
    try: return translate_batch(originals)
    except Exception: return originals

# ============================================================
# CHART & DB FETCHERS
# ============================================================
def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-news")

def generate_chart_image():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' ORDER BY open_time ASC")
            rows = cur.fetchall()
        conn.close()
        
        if len(rows) < 100: return None
        
        df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df.set_index('open_time', inplace=True)
        df = df.astype(float)
        
        df_1d = df.resample('1D').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna().tail(45)
        
        if len(df_1d) < 15: return None

        exp12 = df_1d['close'].ewm(span=12, adjust=False).mean()
        exp26 = df_1d['close'].ewm(span=26, adjust=False).mean()
        macd_line = exp12 - exp26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist = macd_line - signal_line
        macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

        last_close = df_1d['close'].iloc[-1]
        last_open = df_1d['open'].iloc[-1]
        last_color = '#26a69a' if last_close >= last_open else '#ef5350'
        
        max_price = df_1d['high'].max()
        min_price = df_1d['low'].min()
        padding = (max_price - min_price) * 0.10
        ymin, ymax = min_price - padding, max_price + padding

        rc = {'axes.labelsize': 12, 'ytick.labelsize': 12, 'xtick.labelsize': 10, 'axes.titlesize': 16, 'figure.titlesize': 16}
        mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in')
        s = mpf.make_mpf_style(marketcolors=mc, base_mpf_style='nightclouds', facecolor='#131722', figcolor='#131722', gridcolor='#2a2e39', gridstyle='--', rc=rc, y_on_right=True)

        apds = [
            mpf.make_addplot(macd_hist, type='bar', color=macd_colors, panel=2),
            mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
            mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1)
        ]

        fig, axes = mpf.plot(df_1d, type='candle', style=s, title="BTC/USDT | 1D", ylabel='', ylabel_lower='', volume=True, addplot=apds, panel_ratios=(6, 1.5, 2), figsize=(10, 10), ylim=(ymin, ymax), hlines=dict(hlines=[last_close], colors=[last_color], linestyle='--', linewidths=[1]), returnfig=True)
        fig.subplots_adjust(hspace=0.25, right=0.85)
        
        fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=80, ha='center', va='center', color='white', rotation=30)
        ax_main = axes[0]
        ax_main.text(1.005, last_close, f' {last_close:,.2f} ', transform=ax_main.get_yaxis_transform(), va='center', ha='left', color='black', bbox=dict(boxstyle="round,pad=0.3", fc=last_color, ec=last_color, lw=1), fontsize=10, fontweight='bold')

        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        return buf
    except Exception as e:
        log.error(f"Chart Gen Error: {e}")
        return None

def _rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d >= 0: gains += d
        else: losses -= d
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = (d if d > 0 else 0), (-d if d < 0 else 0)
        ag = (ag * (period - 1) + g) / period
        al = (al * (period - 1) + l) / period
    if al == 0: return 100.0
    return 100 - 100 / (1 + (ag / al))

def _atr(highs, lows, closes, period=14):
    if len(highs) < period + 1: return None
    trs = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(1, len(highs))]
    if len(trs) < period: return None
    a = sum(trs[:period]) / period
    for i in range(period, len(trs)): a = (a * (period - 1) + trs[i]) / period
    return a

def fetch_btc_snapshot():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume FROM candles WHERE symbol=%s AND timeframe=%s AND market_type='spot' ORDER BY open_time DESC LIMIT 400", (SYMBOL, TIMEFRAME))
            rows = list(reversed(cur.fetchall()))
        conn.close()
        if len(rows) < 60: return None
        last_24h = rows[-96:] if len(rows) >= 96 else rows
        high_24h, low_24h = max(float(r[2]) for r in last_24h), min(float(r[3]) for r in last_24h)
        price = float(rows[-1][4])
        change_pct = (price - float(rows[-96][4])) / float(rows[-96][4]) * 100 if len(rows) >= 96 and float(rows[-96][4]) else 0
        
        vol_btc = sum(float(r[5]) for r in last_24h)
        taker_buy = sum(float(r[6]) for r in last_24h)
        buy_pct = (taker_buy / vol_btc * 100) if vol_btc else 50
        sell_pct = 100 - buy_pct

        rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
        df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df = df.set_index('open_time')
        df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        
        if len(df_1h) < 15: return None
        closes_1h, highs_1h, lows_1h = df_1h['close'].tolist(), df_1h['high'].tolist(), df_1h['low'].tolist()
        rsi_1h = _rsi(closes_1h, 14)
        atr_1h = _atr(highs_1h, lows_1h, closes_1h, 14)
        return {"price": price, "change_pct": change_pct, "high_24h": high_24h, "low_24h": low_24h, "rsi": rsi_1h, "atr": atr_1h, "vol_btc": vol_btc, "buy_pct": buy_pct, "sell_pct": sell_pct, "taker_buy": taker_buy}
    except Exception as e:
        log.error("fetch_btc: %s", e)
        return None

def fetch_live_cmc():
    if not CMC_API_KEY: return None
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            quote_usd = data.get("quote", {}).get("USD", {})
            return {"btc_dominance": float(data.get("btc_dominance", 0)), "total_market_cap": float(quote_usd.get("total_market_cap", 0)), "stablecoin_market_cap": float(quote_usd.get("stablecoin_market_cap", 0))}
    except Exception: pass
    return None

def fetch_prev_global_metrics(hours_ago=24):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(f"SELECT btc_dominance, total_market_cap, stablecoin_market_cap FROM global_metrics WHERE last_updated <= NOW() - INTERVAL '{hours_ago} hours' ORDER BY last_updated DESC LIMIT 1")
            prev_row = cur.fetchone()
        conn.close()
        if not prev_row: return None
        return {"btc_dominance": float(prev_row[0]), "total_market_cap": float(prev_row[1]), "stablecoin_market_cap": float(prev_row[2])}
    except Exception: return None

def fetch_network_sentiment(hours_ago=24):
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT value, classification FROM fear_greed ORDER BY timestamp DESC LIMIT 1")
            fng_cur = cur.fetchone()
            cur.execute(f"SELECT value FROM fear_greed WHERE timestamp <= NOW() - INTERVAL '{hours_ago} hours' ORDER BY timestamp DESC LIMIT 1")
            fng_prev = cur.fetchone()
            cur.execute("SELECT chart_name, value FROM btc_network WHERE chart_name IN ('hash-rate', 'difficulty') ORDER BY chart_time DESC LIMIT 2")
            net_cur = dict(cur.fetchall())
            cur.execute(f"SELECT chart_name, value FROM btc_network WHERE chart_name IN ('hash-rate', 'difficulty') AND chart_time <= NOW() - INTERVAL '{hours_ago} hours' ORDER BY chart_time DESC LIMIT 2")
            net_prev = dict(cur.fetchall())
        conn.close()
        return {"fng": (fng_cur, fng_prev), "network": (net_cur, net_prev)}
    except Exception: return None

def fetch_stablecoins():
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, price FROM stablecoins WHERE symbol IN ('USDT', 'USDC', 'DAI') ORDER BY timestamp DESC LIMIT 3")
            res = dict(cur.fetchall())
        conn.close()
        return res
    except Exception: return {}

# ============================================================
# FORMATTING & BARS (NEW BLOCK CHARACTERS)
# ============================================================
def fmt_price(p): return "$" + format(int(p), ",") if p >= 1000 else "$" + format(p, ".2f")
def fmt_usd(v):
    if v is None: return "?"
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1e12: return sign + "$" + format(a / 1e12, ".2f") + "T"
    if a >= 1e9: return sign + "$" + format(a / 1e9, ".2f") + "B"
    if a >= 1e6: return sign + "$" + format(a / 1e6, ".2f") + "M"
    if a >= 1e3: return sign + "$" + format(a / 1e3, ".1f") + "K"
    return sign + "$" + format(a, ".2f")
def fmt_btc(v): return format(v, ",.1f") + " BTC" if v >= 1000 else format(v, ".2f") + " BTC"
def fmt_pct(v): return format(v, "+.2f") + "%" if v is not None else "?"

def calc_delta(cur, prev, is_pct=False, is_money=False):
    if prev is None or cur is None: return "(нет данных)"
    if prev == 0: return "(⏸️ 0)"
    diff = cur - prev
    sign = "🔼" if diff > 0 else "🔽" if diff < 0 else "⏸️"
    if diff == 0: return f"({sign} 0)"
    if is_pct: return f"({sign} {diff:+.2f}%)"
    if is_money: return f"({sign} {fmt_usd(diff)})"
    return f"({sign} {diff:+g})"

def build_report(data, top_items, top_ru, btc, live_cmc, prev_g_metrics, n_sentiment, stables):
    now = now_local()
    mood = data.get("mood", "Неизвестно")
    avg = data.get("avg_sentiment", 0.0)
    total = data.get("total_news", 0)
    
    lines = ["📰 <b>ARGUS — Новости</b>", f"☕ Доброе утро! {now.strftime('%d.%m.%Y, %H:%M')}", ""]

    if btc:
        lines.append(f"💰 <b>BTC</b> {fmt_price(btc['price'])} | {fmt_pct(btc['change_pct'])} 24ч")
        lines.append(f"  Макс: {fmt_price(btc['high_24h'])} | Мин: {fmt_price(btc['low_24h'])}")
        parts = []
        if btc["rsi"]: parts.append(f"RSI(1ч): {btc['rsi']:.1f}")
        if btc["atr"]: parts.append(f"ATR(1ч): {fmt_price(btc['atr'])}")
        if parts: lines.append("  " + " | ".join(parts))
        lines.append("")
        
        bar_len = 20
        # ИСПОЛЬЗУЕМ УЗКИЕ БЛОКИ, А НЕ ЭМОДЗИ
        b_buy = int(round(btc["buy_pct"] / 100 * bar_len))
        bar_buy = "█" * b_buy + "░" * (bar_len - b_buy)
        b_sell = int(round(btc["sell_pct"] / 100 * bar_len))
        bar_sell = "█" * b_sell + "░" * (bar_len - b_sell)
        
        lines.append("⚔️ <b>Битва объемов:</b>")
        lines.append(f"  🟢 Покупки: {fmt_btc(btc['taker_buy'])} ({btc['buy_pct']:.1f}%)")
        lines.append(f"  {bar_buy}")
        lines.append(f"  🔴 Продажи: {fmt_btc(btc['vol_btc'] - btc['taker_buy'])} ({btc['sell_pct']:.1f}%)")
        lines.append(f"  {bar_sell}")
        lines.append("")

    lines.append("🌍 <b>Глобальный рынок</b>")
    if live_cmc:
        mcap, dom, stbl_mcap = live_cmc["total_market_cap"], live_cmc["btc_dominance"], live_cmc["stablecoin_market_cap"]
        d_mcap = calc_delta(mcap, prev_g_metrics["total_market_cap"], is_money=True) if prev_g_metrics else "(нет данных)"
        d_dom = calc_delta(dom, prev_g_metrics["btc_dominance"], is_pct=True) if prev_g_metrics else "(нет данных)"
        usdt = stables.get("USDT")
        usdt_s = f"${usdt:.3f} ✅" if usdt and 0.99 <= float(usdt) <= 1.01 else f"${usdt:.3f} ⚠️" if usdt else "?"
        lines.append(f"  Капитализация: {fmt_usd(mcap)} {d_mcap}")
        lines.append(f"  Доминация BTC: {dom:.2f}% {d_dom}")
        lines.append(f"  Стейблкоины: {fmt_usd(stbl_mcap)} (USDT {usdt_s})")
    else:
        lines.append("  ⚠️ CMC не отвечает")
    lines.append("")

    lines.append("🌐 <b>Сеть и Сентимент</b>")
    if n_sentiment:
        fng_cur, _ = n_sentiment["fng"]
        net_cur, _ = n_sentiment["network"]
        if fng_cur: lines.append(f"  Страх/Жадность: {fng_cur[0]} ({fng_cur[1]})")
        else: lines.append("  Страх/Жадность: (нет данных)")
        if net_cur.get("hash-rate"):
            hr = float(net_cur["hash-rate"]) / 1e6
            lines.append(f"  Хешрейт: {hr:.0f} EH/s")
        else: lines.append("  Сеть BTC: (нет данных)")
    else:
        lines.append("  ❌ Данные недоступны")
    lines.append("")

    lines.append(f"🎭 {esc(mood)}")
    lines.append(f"📊 Сентимент: <b>{format(avg, '+.3f')}</b> | Всего: {total}")
    lines.append("")

    if top_items:
        lines.append("🔥 <b>Главные новости:</b>")
        for i, it in enumerate(top_items):
            title = top_ru[i] if i < len(top_ru) else (it.get("title_original") or it.get("title", ""))
            url = it.get("url") or it.get("link") or ""
            t_esc = esc(title[:180])
            lines.append(f"  • <a href=\"{esc(url)}\">{t_esc}</a>" if url else f"  • {t_esc}")
        lines.append("")
    else:
        lines.append("Новых важных новостей нет.\n")

    lines.append("────────────────────")
    lines.append("🔥 <b>Хотите торговать на Bybit?</b>")
    lines.append("  Регистрируйтесь по ссылке и получайте бонусы до $1720:")
    lines.append("  <a href=\"https://www.bybit.com/invite?ref=JV3355&medium=referral&utm_campaign=evergreen\">👉 Забрать бонус на Bybit</a>\n")
    lines.append("<i>Настроение — один из факторов прогноза ARGUS.</i>")
    
    return "\n".join(lines)

def main():
    log.info("news report v12.5")
    if not should_send_group(): return log.info("skip")

    if not SENTIMENT_FILE.exists(): return log.error("no sentiment file")

    data = load_json(SENTIMENT_FILE)
    history = clean_history(load_sent_history())
    seen = get_sent_hashes(history)
    
    all_top = data.get("top_bullish", []) + data.get("top_bearish", [])
    all_top.sort(key=lambda x: x.get("effective_weight", 0), reverse=True)
    top_5 = filter_fresh(all_top, seen)[:5]
    top_ru = translate_top(top_5, 5)
    
    btc = fetch_btc_snapshot()
    live_cmc = fetch_live_cmc()
    prev_g_metrics = fetch_prev_global_metrics(hours_ago=24)
    n_sentiment = fetch_network_sentiment(hours_ago=24)
    stables = fetch_stablecoins()

    text_tg = build_report(data, top_5, top_ru, btc, live_cmc, prev_g_metrics, n_sentiment, stables)
    text_vk = to_plain(text_tg)

    sent_any = False
    if TG_CHANNEL_ID: send_telegram(text_tg, TG_CHANNEL_ID)
    
    photo_buf = generate_chart_image()
    photo_att = upload_photo_to_vk(photo_buf) if photo_buf else None
    
    if send_vk(text_vk, photo_att): sent_any = True

    if sent_any:
        for it in top_5: add_sent_hash(history, _title_hash(it.get("title_original") or it.get("title", "")))
        save_json(SENT_HISTORY_FILE, history)
        log.info("report sent")

if __name__ == "__main__":
    main()