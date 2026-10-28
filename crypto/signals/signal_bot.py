# ============================================================
# ARGUS - SIGNAL BOT (SCOUT v3.2.0)
# ------------------------------------------------------------
# Ищет точку входа. Если находит, записывает сделку в 
# opened_deals.json со статусом "не отправлено".
# ============================================================

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
import psycopg
import pandas as pd

VERSION = "3.2.0"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_bot")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent

# Пути
MODEL_FILE = CRYPTO_ROOT / "data" / "models" / "best_params.json"
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

# ТОТ САМЫЙ ФАЙЛ
OPENED_DEALS_FILE = SIGNALS_DIR / "opened_deals.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
SYMBOL = "BTCUSDT"
TIMEFRAME = "15m"

def get_conn():
    if not DB_URL:
        raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-signal")

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================
# ИНДИКАТОРЫ
# ============================================================
def calculate_ema(series, period):
    if len(series) < period: return None
    return series.ewm(span=period, adjust=False).mean().iloc[-1]

def calculate_rsi(series, period=14):
    if len(series) < period + 1: return None
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1]

def calculate_macd_hist(series, fast=12, slow=26, signal=9):
    if len(series) < slow + signal: return None
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return hist.iloc[-1]

# ============================================================
# ЛОГИКА
# ============================================================
def main():
    log.info(f"STARTING SIGNAL BOT v{VERSION}")

    # 1. Загружаем модель
    model_data = load_json(MODEL_FILE)
    if not model_data or "params" not in model_data:
        log.error("Model file not found. Run trainer first.")
        return
    
    params = model_data["params"]

    # 2. СТРОГАЯ ПРОВЕРКА: Если есть словарь И в нём есть ключ 'entry'
    current_deal = load_json(OPENED_DEALS_FILE, None)
    if current_deal and "entry" in current_deal:
        log.info("Deal is already open. Sleeping.")
        return

    # 3. Идем в базу за свежими данными
    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT open_time, close, volume 
                FROM candles 
                WHERE symbol=%s AND timeframe=%s AND market_type='spot'
                ORDER BY open_time DESC LIMIT 100
            """, (SYMBOL, TIMEFRAME))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e:
        log.error(f"DB error: {e}")
        return

    if len(rows) < 50:
        log.error("Not enough candles in DB.")
        return

    # 4. Считаем индикаторы
    df = pd.DataFrame(rows, columns=['open_time', 'close', 'volume'])
    
    current_close = float(df['close'].iloc[-1])
    rsi = calculate_rsi(df['close'])
    macd_hist = calculate_macd_hist(df['close'])
    ema_50 = calculate_ema(df['close'], 50)
    vol_sma = df['volume'].rolling(window=20).mean().iloc[-1]
    current_vol = float(df['volume'].iloc[-1])

    log.info(f"Price: {current_close} | RSI: {rsi:.1f} | MACD: {macd_hist:.2f}")

    # 5. Проверка условий из модели
    signal = False
    if macd_hist > 0 and (params["rsi_min"] < rsi < params["rsi_max"]):
        signal = True
        
    if params["use_trend_filter"] and not (current_close > ema_50):
        signal = False
            
    if params["use_volume_filter"] and not (current_vol > vol_sma):
        signal = False

    # 6. Запись результата в opened_deals.json
    if signal:
        log.info("🚀 SIGNAL FOUND! Opening deal.")
        
        tp_price = current_close * (1 + params["tp_pct"] / 100)
        sl_price = current_close * (1 - params["sl_pct"] / 100)
        
        new_deal = {
            "entry": current_close,
            "tp": tp_price,
            "sl": sl_price,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "sent_to_tg": False
        }
        
        save_json(OPENED_DEALS_FILE, new_deal)
        log.info(f"Saved signal. Entry: {current_close:.2f}, TP: {tp_price:.2f}, SL: {sl_price:.2f}")
    else:
        log.info("No signal. Waiting.")

if __name__ == "__main__":
    main()