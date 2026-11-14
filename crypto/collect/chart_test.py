# ============================================================
# ARGUS - CHART TEST (FAT CANDLES, TIGHT LAYOUT)
# Path: crypto/collect/chart_test.py
# ============================================================

import os
import sys
import io
import logging
import pandas as pd
import numpy as np
import psycopg
import requests
import mplfinance as mpf
import matplotlib.pyplot as plt
from datetime import datetime, timezone, timedelta

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
    files = {'photo': ('chart.jpg', photo_bytes, 'image/jpeg')}
    data = {'chat_id': CHAT_ID}
    try:
        r = requests.post(url, files=files, data=data, timeout=30)
        if r.status_code != 200:
            log.error(f"TG API Error: {r.text}")
            return False
        return True
    except Exception as e:
        log.error(f"TG Send error: {e}")
        return False

def main():
    log.info("STARTING CHART")

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT open_time, open, high, low, close, volume 
                FROM candles 
                WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' 
                  AND open_time >= '2026-11-10' 
                ORDER BY open_time ASC
            """)
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"DB Error: {e}")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df = df.set_index('open_time')
    df = df.astype(float)

    # Склеиваем в 1 час
    df_1h = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min',
        'close': 'last', 'volume': 'sum'
    }).dropna()
    
    # ОСТАВЛЯЕМ ТОЛЬКО 40 СВЕЧЕЙ
    df = df_1h.tail(40).copy() 
    
    # Тестовая сделка
    entry_idx = 10 
    exit_idx = len(df) - 5   
    entry_price = df['close'].iloc[entry_idx]
    exit_price = df['close'].iloc[exit_idx]
    
    sl_price = entry_price * 0.99
    tp_price = entry_price * 1.02

    # MACD
    exp12 = df['close'].ewm(span=12, adjust=False).mean()
    exp26 = df['close'].ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    macd_colors = ['#26a69a' if val >= 0 else '#ef5350' for val in macd_hist]

    last_close = df['close'].iloc[-1]
    last_open = df['open'].iloc[-1]
    last_color = '#26a69a' if last_close >= last_open else '#ef5350'

    max_price = max(df['high'].max(), tp_price)
    min_price = min(df['low'].min(), sl_price)
    padding = (max_price - min_price) * 0.1
    ymin, ymax = min_price - padding, max_price + padding

    rc = {
        'axes.labelsize': 12, 'ytick.labelsize': 14, 'xtick.labelsize': 12,
        'axes.titlesize': 18, 'figure.titlesize': 18,
    }

    mc = mpf.make_marketcolors(up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in')
    s = mpf.make_mpf_style(
        marketcolors=mc, base_mpf_style='nightclouds',
        facecolor='#131722', figcolor='#131722', gridcolor='#2a2e39',
        gridstyle='--', rc=rc, y_on_right=True
    )

    apds = [
        mpf.make_addplot(macd_hist, type='bar', color=macd_colors, panel=2),
        mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
        mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1)
    ]

    scatter_entry = pd.Series(np.nan, index=df.index)
    scatter_entry.iloc[entry_idx] = entry_price
    apds.append(mpf.make_addplot(scatter_entry, type='scatter', markersize=400, marker='^', color='#26a69a', edgecolors='white', linewidths=1.5, panel=0))

    scatter_exit = pd.Series(np.nan, index=df.index)
    scatter_exit.iloc[exit_idx] = exit_price
    apds.append(mpf.make_addplot(scatter_exit, type='scatter', markersize=400, marker='v', color='#ef5350', edgecolors='white', linewidths=1.5, panel=0))

    # ШИРОКИЙ ГРАФИК 16:9 (чтобы свечи были толстыми)
    fig, axes = mpf.plot(
        df, type='candle', style=s,
        ylabel='', ylabel_lower='',
        volume=True,
        addplot=apds,
        panel_ratios=(8, 2, 3), # Главный график занимает больше всего места
        figsize=(16, 9),
        ylim=(ymin, ymax),
        hlines=dict(hlines=[entry_price, sl_price, tp_price], colors=['blue', '#ef5350', '#26a69a'], linestyle='--', linewidths=[1.5, 1.5, 1.5]),
        returnfig=True,
        xrotation=0,
        tight_layout=True # Включаем, чтобы убрать дыры
    )

    # Убираем огромные зазоры (hspace), делаем минимальные отступы
    fig.subplots_adjust(hspace=0.25, right=0.92, top=0.92, bottom=0.08, left=0.06)

    axes[0].set_title("BTC/USDT | 1H (Последние 40 свечей)", fontsize=18, fontweight='bold', pad=10)
    fig.text(0.5, 0.5, 'ARGUS', alpha=0.05, fontsize=120, ha='center', va='center', color='white', rotation=30)

    ax_main = axes[0]
    ax_main.text(df.index[0], entry_price, ' ВХОД', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='blue', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'))
    ax_main.text(df.index[0], sl_price, ' СТОП', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#ef5350', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'))
    ax_main.text(df.index[0], tp_price, ' ТЕЙК', color='white', fontsize=12, fontweight='bold', va='center', ha='left', bbox=dict(facecolor='#26a69a', alpha=0.8, edgecolor='none', boxstyle='round,pad=0.2'))

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
    fig.savefig(buf, format='jpeg', dpi=150, facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)

    if send_telegram_photo(buf.getvalue()):
        log.info("Test chart sent successfully!")
    else:
        log.error("Failed to send photo.")
        sys.exit(1)

if __name__ == "__main__":
    main()