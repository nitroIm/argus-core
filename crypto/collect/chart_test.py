# ============================================================
# ARGUS - CHART TEST (FIXED SPACING)
# Path: crypto/collect/chart_test.py
# ============================================================

import os
import sys
import io
import logging
import pandas as pd
import psycopg
import requests
import mplfinance as mpf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("chart_test")

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
CHAT_ID = (os.getenv("TELEGRAM_CHAT_ID") or "").strip()

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-chart")

def send_telegram_photo(photo_bytes):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("No TG token or chat_id")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', photo_bytes)}
    data = {'chat_id': CHAT_ID}
    try:
        r = requests.post(url, files=files, data=data, timeout=20)
        return r.status_code == 200
    except Exception as e:
        log.error(f"TG Send error: {e}")
        return False

def main():
    log.info("STARTING CHART GENERATOR")
    
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' ORDER BY open_time ASC")
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"DB Error: {e}")
        sys.exit(1)
        
    if len(rows) < 100:
        log.error("Not enough data in DB")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df.set_index('open_time', inplace=True)
    df = df.astype(float)
    
    # Склеиваем в 1D
    df_1d = df.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 
        'close': 'last', 'volume': 'sum'
    }).dropna()

    # Считаем MACD
    exp12 = df_1d['close'].ewm(span=12, adjust=False).mean()
    exp26 = df_1d['close'].ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line

    macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

    last_close = df_1d['close'].iloc[-1]
    last_open = df_1d['open'].iloc[-1]
    last_color = '#26a69a' if last_close >= last_open else '#ef5350'

    rc = {
        'axes.labelsize': 12, 
        'ytick.labelsize': 12, 
        'xtick.labelsize': 10, 
        'axes.titlesize': 16, 
        'figure.titlesize': 16
    }

    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(
        marketcolors=mc, 
        base_mpf_style='nightclouds', 
        facecolor='#131722', 
        figcolor='#131722', 
        gridcolor='#2a2e39', 
        gridstyle='--',
        rc=rc,
        y_on_right=True # Встроенный перенос осей
    )

    apds = [
        mpf.make_addplot(macd_hist, type='bar', color=macd_colors, panel=2),
        mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
        mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1)
    ]

    title = "BTC/USDT | 1D"
    
    fig, axes = mpf.plot(
        df_1d, type='candle', style=s, 
        title=title, ylabel='', ylabel_lower='',
        volume=True, 
        addplot=apds,
        panel_ratios=(6, 1.5, 2), 
        figsize=(10, 10), 
        hlines=dict(hlines=[last_close], colors=[last_color], linestyle='--', linewidths=[1]),
        returnfig=True
    )
    
    # ДОБАВЛЯЕМ ОТСТУП МЕЖДУ ПАНЕЛЯМИ (чтобы цифры не слипались)
    fig.subplots_adjust(hspace=0.25, right=0.85)

    fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=80, ha='center', va='center', color='white', rotation=30)
    fig.text(0.5, 0.02, 'ARGUS — автономная система анализа', alpha=0.6, fontsize=10, ha='center', va='bottom', color='gray')

    # Плашка с ценой (сдвинута ближе, чтобы не обрезалась)
    ax_main = axes[0]
    ax_main.text(1.005, last_close, f' {last_close:,.2f} ', 
                 transform=ax_main.get_yaxis_transform(), 
                 va='center', ha='left', 
                 color='black', 
                 bbox=dict(boxstyle="round,pad=0.3", fc=last_color, ec=last_color, lw=1),
                 fontsize=10, fontweight='bold')

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    buf.seek(0)
    
    log.info("Sending to Telegram...")
    if send_telegram_photo(buf):
        log.info("Photo sent successfully!")
    else:
        log.error("Failed to send photo.")
        sys.exit(1)

if __name__ == "__main__":
    main()