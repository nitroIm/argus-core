# ============================================================
# ARGUS - CHART TEST (1H TIMEFRAME, MOCK TRADE)
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
    files = {'photo': ('chart.png', photo_bytes)}
    data = {'chat_id': CHAT_ID}
    try:
        r = requests.post(url, files=files, data=data, timeout=20)
        return r.status_code == 200
    except Exception as e:
        log.error(f"TG Send error: {e}")
        return False

def main():
    log.info("STARTING 1H CHART GENERATOR FROM NOV 10")

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            # Берем 15-минутные данные с 10 ноября
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

    if len(rows) < 100:
        log.error("Not enough data in DB since Nov 10")
        sys.exit(1)

    df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df.set_index('open_time', inplace=True)
    df = df.astype(float)

    # --- СКЛЕИВАЕМ В 1 ЧАС (1H) ---
    df_1h = df.resample('1h').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna()
    
    df = df_1h # Дальше работаем с часовыми свечами

    # --- МОКАЕМ ТЕСТОВУЮ СДЕЛКУ (на часовиках) ---
    mid_idx = len(df) // 2
    entry_idx = mid_idx - 10 # Вход чуть раньше середины
    exit_idx = len(df) - 5   # Выход недавно
    
    entry_price = df['close'].iloc[entry_idx]
    exit_price = df['close'].iloc[exit_idx]
    
    # Фейковые уровни (Лонг)
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

    # ДИНАМИЧЕСКИЙ РАСЧЕТ
    max_price = max(df['high'].max(), tp_price)
    min_price = min(df['low'].min(), sl_price)
    padding = (max_price - min_price) * 0.15
    ymin, ymax = min_price - padding, max_price + padding

    # Шрифты
    rc = {
        'axes.labelsize': 14, 'ytick.labelsize': 16, 'xtick.labelsize': 14,
        'axes.titlesize': 20, 'figure.titlesize': 20,
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

    # Стрелка покупки
    scatter_entry = pd.Series(np.nan, index=df.index)
    scatter_entry.iloc[entry_idx] = entry_price
    apds.append(mpf.make_addplot(scatter_entry, type='scatter', markersize=300, marker='^', color='#26a69a', panel=0))

    # Стрелка продажи
    scatter_exit = pd.Series(np.nan, index=df.index)
    scatter_exit.iloc[exit_idx] = exit_price
    apds.append(mpf.make_addplot(scatter_exit, type='scatter', markersize=300, marker='v', color='#ef5350', panel=0))

    # Рисуем график
    fig, axes = mpf.plot(
        df, type='candle', style=s,
        ylabel='', ylabel_lower='',
        volume=True,
        addplot=apds,
        panel_ratios=(6, 1.5, 2),
        figsize=(14, 12),
        ylim=(ymin, ymax),
        hlines=dict(hlines=[entry_price, sl_price, tp_price], colors=['blue', '#ef5350', '#26a69a'], linestyle='--', linewidths=[1.5, 1.5, 1.5]),
        returnfig=True,
        xrotation=0,
        tight_layout=False
    )

    fig.subplots_adjust(hspace=1.0, right=0.88, top=0.92, bottom=0.08, left=0.08)

    if len(axes) > 3:
        vol_ax = axes[3]
        vol_ymin, vol_ymax = vol_ax.get_ylim()
        vol_ax.set_ylim(vol_ymin, vol_ymax * 1.3)

    axes[0].set_title("BTC/USDT | 1H (с 10.11.2026)", fontsize=20, fontweight='bold', pad=15)
    fig.text(0.5, 0.5, 'ARGUS', alpha=0.1, fontsize=100, ha='center', va='center', color='white', rotation=30)

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
    fig.savefig(buf, format='png', dpi=150, bbox_inches='tight', pad_inches=0.2, facecolor=fig.get_facecolor())
    buf.seek(0)
    plt.close(fig)

    log.info("Sending test 1H chart to Telegram...")
    if send_telegram_photo(buf):
        log.info("Test chart sent successfully!")
    else:
        log.error("Failed to send photo.")
        sys.exit(1)

if __name__ == "__main__":
    main()