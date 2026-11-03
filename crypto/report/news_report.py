# ============================================================
# ARGUS - NEWS REPORT v12.7 (VK JPEG FIX)
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
from PIL import Image  # <--- Добавлен импорт для конвертации
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
        
        # Явно указываем JPEG и MIME-тип
        files = {'photo': ('chart.jpg', photo_bytes.getvalue(), 'image/jpeg')}
        r = requests.post(upload_url, files=files, timeout=30).json()
        
        # Проверяем, что ВК реально принял фото
        if not r.get("photo"):
            log.error(f"VK upload returned empty photo: {r}")
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
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 5000")
            rows = cur.fetchall()
        conn.close()
        
        if len(rows) < 100: return None
        rows.reverse()
        
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

        # Сохраняем в память как PNG
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
        buf.seek(0)
        
        # КОНВЕРТИРУЕМ В JPEG (убираем прозрачность, которую не любит ВК)
        img = Image.open(buf).convert('RGB')
        out_buf = io.BytesIO()
        img.save(out_buf, format='JPEG', quality=95)
        out_buf.seek(0)
        return out_buf
    except Exception as e:
        log.error(f"Chart Gen Error: {e}")
        return None

# ... (ОСТАЛЬНЫЕ ФУНКЦИИ _rsi, _atr, fetch_btc_snapshot, fetch_live_cmc, fetch_prev_global_metrics, fetch_network_sentiment, fetch_stablecoins, formatting, build_report, main остаются БЕЗ ИЗМЕНЕНИЙ, они идентичны предыдущей версии 12.6) ...