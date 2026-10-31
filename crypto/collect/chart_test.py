# ============================================================
# ARGUS - CHART TEST (DARK THEME)
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

def send_telegram_photo(photo_bytes, caption):
    if not BOT_TOKEN or not CHAT_ID:
        log.error("No TG token or chat_id")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {'photo': ('chart.png', photo_bytes)}
    data = {'chat_id': CHAT_ID, 'caption': caption, 'parse_mode': 'HTML'}
    try:
        r = requests.post(url, files=files, data=data, timeout=20)
        return r.status_code == 200
    except Exception as e:
        log.error(f"TG Send error: {e}")
        return False

def main():
    log.info("STARTING CHART GENERATOR")
    
    # 1. Загружаем данные (берем максимум для 1D)
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' ORDER BY open_time ASC")
            rows = cur.fetchall()
        conn.close()
    except Exception as e:
        log.error(f"DB Error: {e}")
        sys.exit(1)
        
    if len(rows) < 1000:
        log.error("Not enough 15m data to build 1D chart")
        sys.exit(1)

    # 2. Подготовка датафрейма
    df = pd.DataFrame(rows, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df = df.set_index('open_time')
    df = df.astype(float)
    
    # Склеиваем в 1D (Дневки)
    df_1d = df.resample('1D').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 
        'close': 'last', 'volume': 'sum'
    }).dropna()
    
    if len(df_1d) < 100:
        log.error("Not enough 1D candles to calculate EMA(99)")
        sys.exit(1)
        
    log.info(f"Got {len(df_1d)} daily candles.")

    # 3. Считаем индикаторы
    df_1d['ema7'] = df_1d['close'].ewm(span=7, adjust=False).mean()
    df_1d['ema25'] = df_1d['close'].ewm(span=25, adjust=False).mean()
    df_1d['ema99'] = df_1d['close'].ewm(span=99, adjust=False).mean()
    
    # MACD
    exp12 = df_1d['close'].ewm(span=12, adjust=False).mean()
    exp26 = df_1d['close'].ewm(span=26, adjust=False).mean()
    macd_line = exp12 - exp26
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    macd_hist = macd_line - signal_line
    
    # RSI
    delta = df_1d['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df_1d['rsi'] = 100 - (100 / (1 + rs))

    # 4. Настройка стиля (Тёмный)
    mc = mpf.make_marketcolors(
        up='#26a69a', down='#ef5350', edge='inherit', wick='inherit', volume='in'
    )
    s = mpf.make_mpf_style(
        marketcolors=mc, 
        base_mpf_style='nightclouds', 
        facecolor='#131722', 
        figcolor='#131722', 
        gridcolor='#2a2e39',
        gridstyle='--'
    )

    # 5. Дополнительные графики
    apds = [
        mpf.make_addplot(df_1d['ema7'], color='#FFD700', panel=0, width=1),
        mpf.make_addplot(df_1d['ema25'], color='#FF69B4', panel=0, width=1),
        mpf.make_addplot(df_1d['ema99'], color='#9370DB', panel=0, width=1),
        
        mpf.make_addplot(macd_hist, type='bar', color='dimgray', panel=2),
        mpf.make_addplot(macd_line, color='#FFD700', panel=2, width=1),
        mpf.make_addplot(signal_line, color='#9370DB', panel=2, width=1),
        
        mpf.make_addplot(df_1d['rsi'], color='#FFD700', panel=3, width=1)
    ]

    # 6. Сохраняем в память (без создания лишних файлов на диске)
    buf = io.BytesIO()
    title = f"BTC/USDT | 1D | Binance\nPrice: ${df_1d['close'].iloc[-1]:,.2f}"
    
    log.info("Generating chart...")
    mpf.plot(
        df_1d, type='candle', style=s, 
        title=title, ylabel='', ylabel_lower='',
        volume=True, 
        addplot=apds,
        panel_ratios=(6, 1.5, 2, 2),
        figsize=(12, 14),
        savefig=dict(fname=buf, dpi=150, bbox_inches='tight')
    )
    
    buf.seek(0)
    
    # 7. Отправляем
    log.info("Sending to Telegram...")
    caption = f"📊 <b>Тест графика (1D)</b>\n🕯️ Свечи: {len(df_1d)} дн.\n<b>Индикаторы:</b> EMA(7,25,99), MACD, RSI"
    if send_telegram_photo(buf, caption):
        log.info("Photo sent successfully!")
    else:
        log.error("Failed to send photo.")
        sys.exit(1)

if __name__ == "__main__":
    main()