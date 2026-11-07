# ============================================================
# ARGUS - CHART TEST (FIXED: tight_layout / overlap / title)
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

    # Скользящее окно — последние 45 дней
    df_1d = df_1d.tail(45)

    if len(df_1d) < 15:
        log.error("Not enough 1D candles after resample")
        sys.exit(1)

    # MACD
    exp12 = df_1d['close'].ewm(span=12, adjust=False).mean()
    exp26 = df_1d['close'].ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

    last_close = df_1d['close'].iloc[-1]
    last_open = df_1d['open'].iloc[-1]
    last_color = '#26a69a' if last_close >= last_open else '#ef5350'

    # Динамический padding
    max_price = df_1d['high'].max()
    min_price = df_1d['low'].min()
    padding = (max_price - min_price) * 0.10
    ymin, ymax = min_price - padding, max_price + padding

    # ==== УВЕЛИЧЕННЫЕ ШРИФТЫ ====
    rc = {
        'axes.labelsize': 14,
        'ytick.labelsize': 16,   # Крупные цифры на осях
        'xtick.labelsize': 14,
        'axes.titlesize': 20,
        'figure.titlesize': 20,
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
        y_on_right=True
    )

    apds = [
        mpf.make_addplot(macd_hist, type='bar', color=macd_colors, panel=2),
        mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
        mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1)
    ]

    # ==== ГЛАВНЫЙ ФИКС: tight_layout=False ====
    fig, axes = mpf.plot(
        df_1d, type='candle', style=s,
        ylabel='', ylabel_lower='',
        volume=True,
        addplot=apds,
        panel_ratios=(6, 1.5, 2),
        figsize=(12, 12),
        ylim=(ymin, ymax),
        hlines=dict(hlines=[last_close], colors=[last_color], linestyle='--', linewidths=[1]),
        returnfig=True,
        xrotation=0,
        tight_layout=False  # <--- ОТКЛЮЧАЕМ АВТОМАТИЧЕСКОЕ СЛИПАНИЕ
    )

    # hspace=0.5 раздвигает панели, чтобы 74к и 15к не наезжали.
    # top=0.92 оставляет место для заголовка.
    fig.subplots_adjust(hspace=0.5, right=0.88, top=0.92, bottom=0.08, left=0.08)

    # Заголовок теперь привязан к графику и стоит ровно по центру
    axes[0].set_title("BTC/USDT | 1D", fontsize=20, fontweight='bold', pad=15)

    # Watermark
    fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=100, ha='center', va='center',
             color='white', rotation=30)

    # Плашка цены
    ax_main = axes[0]
    ax_main.annotate(
        f' {last_close:,.2f} ',
        xy=(1.0, last_close),
        xycoords=('axes fraction', 'data'),
        xytext=(3, 0),
        textcoords='offset points',
        color='black',
        bbox=dict(boxstyle="round,pad=0.5", fc=last_color, ec=last_color, lw=1),
        fontsize=14, fontweight='bold', va='center', ha='left'
    )

    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', pad_inches=0.2)
    buf.seek(0)

    log.info("Sending to Telegram...")
    if send_telegram_photo(buf):
        log.info("Photo sent successfully!")
    else:
        log.error("Failed to send photo.")
        sys.exit(1)

if __name__ == "__main__":
    main()