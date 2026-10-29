# ============================================================
# ARGUS - SIGNAL BOT (MONOLITH v6.0 LONG & SHORT)
# ============================================================

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
import psycopg
import pandas as pd

VERSION = "6.0.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_bot")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
MODEL_FILE = CRYPTO_ROOT / "data" / "models" / "best_params.json"
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

OPENED_DEALS = SIGNALS_DIR / "opened_deals.json"
CLOSED_DEALS = SIGNALS_DIR / "closed_deals.json"
INFO_DEAL = SIGNALS_DIR / "info_deal.json"
BOT_INFO = SIGNALS_DIR / "bot_info.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
SYMBOL = "BTCUSDT"

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30)

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def calc_ema(s, p): return s.ewm(span=p, adjust=False).mean().iloc[-1] if len(s) >= p else None

def calc_rsi(s, p=14):
    if len(s) < p + 1: return None
    d = s.diff(); g = (d.where(d > 0, 0)).rolling(window=p).mean(); l = (-d.where(d < 0, 0)).rolling(window=p).mean()
    return (100 - (100 / (1 + g/l))).iloc[-1]

def calc_macd_hist(s):
    if len(s) < 35: return None
    ema_fast = s.ewm(span=12).mean()
    ema_slow = s.ewm(span=26).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=9).mean()
    return (macd_line - signal_line).iloc[-1]

def main():
    log.info(f"STARTING MONOLITH BOT v{VERSION}")

    if not BOT_INFO.exists():
        save_json(BOT_INFO, {"started_at": datetime.now(timezone.utc).isoformat()})
        log.info("Bot start time recorded.")

    current_deal = load_json(OPENED_DEALS, None)
    models = load_json(MODEL_FILE)

    # ==========================================
    # ЛОГИКА 1: ЕСЛИ МЫ В СДЕЛКЕ (СЛЕДИМ)
    # ==========================================
    if current_deal and "entry" in current_deal:
        # Защита для твоей текущей сделки: если нет ключа "type", считаем её LONG
        deal_type = current_deal.get("type", "LONG")
        log.info(f"In {deal_type} trade. Entry: {current_deal['entry']:.2f}")
        
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT high, low, close FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 1", (SYMBOL,))
                row = cur.fetchone()
            conn.close()
        except Exception as e: return log.error(f"DB error: {e}")

        high, low, close = float(row[0]), float(row[1]), float(row[2])
        
        exit_reason, exit_price = None, close
        
        # Проверка времени удержания
        time_exit_h = 24 # дефолт
        if deal_type == "LONG" and models.get("long"):
            time_exit_h = models["long"]["params"].get("time_exit_h", 24)
        elif deal_type == "SHORT" and models.get("short"):
            time_exit_h = models["short"]["params"].get("time_exit_h", 24)

        open_time = datetime.fromisoformat(current_deal["open_time"])
        time_held_h = (datetime.now(timezone.utc) - open_time).total_seconds() / 3600.0
        
        if deal_type == "LONG":
            if low <= current_deal["sl"]:
                exit_reason, exit_price = "STOP_LOSS", current_deal["sl"]
            elif high >= current_deal["tp"]:
                exit_reason, exit_price = "TAKE_PROFIT", current_deal["tp"]
        else: # SHORT
            if high >= current_deal["sl"]:
                exit_reason, exit_price = "STOP_LOSS", current_deal["sl"]
            elif low <= current_deal["tp"]:
                exit_reason, exit_price = "TAKE_PROFIT", current_deal["tp"]

        if not exit_reason and time_held_h >= time_exit_h:
            exit_reason, exit_price = "TIME_EXIT", close

        if exit_reason:
            if deal_type == "LONG":
                pnl_pct = (exit_price - current_deal["entry"]) / current_deal["entry"] * 100
            else:
                pnl_pct = (current_deal["entry"] - exit_price) / current_deal["entry"] * 100
            
            pnl_usd = 100.0 * (pnl_pct / 100) 
            
            log.info(f"🔴 {deal_type} CLOSED: {exit_reason} | PnL: {pnl_pct:.2f}%")

            closed = load_json(CLOSED_DEALS, [])
            closed.append({
                "type": deal_type, "entry": current_deal["entry"], "exit": exit_price,
                "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
                "reason": exit_reason, "close_time": datetime.now(timezone.utc).isoformat()
            })
            save_json(CLOSED_DEALS, closed)
            save_json(OPENED_DEALS, None)

            save_json(INFO_DEAL, {
                "type": "CLOSE", "deal_type": deal_type, "reason": exit_reason,
                "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
                "sent": False
            })
        return

    # ==========================================
    # ЛОГИКА 2: ЕСЛИ СДЕЛКИ НЕТ (ИЩЕМ)
    # ==========================================
    if not models.get("long") or not models.get("short"):
        return log.error("Models not found or outdated.")

    params_long = models["long"]["params"]
    params_short = models["short"]["params"]

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 400", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e: return log.error(f"DB error: {e}")

    # ФИКС ДЛЯ PANDAS: перевод Decimal в float
    rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df = df.set_index('open_time')

    df_1h = df.resample('1h').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 
        'close': 'last', 'volume': 'sum'
    }).dropna()

    if len(df_1h) < 55:
        return log.error("Not enough data for 1h indicators.")

    last_closed_1h = df_1h.iloc[-2]
    close_series = df_1h['close'].iloc[:-1] 

    price = float(last_closed_1h['close'])
    rsi = calc_rsi(close_series)
    macd_hist = calc_macd_hist(close_series)
    ema = calc_ema(close_series, 50)
    
    vol_sma = df_1h['volume'].iloc[:-1].rolling(20).mean().iloc[-1]
    cur_vol = float(last_closed_1h['volume'])

    log.info(f"1h Signal Check | Price: {price} | RSI: {rsi:.1f} | MACD Hist: {macd_hist:.2f}")

    signal_long = False
    if macd_hist > 0 and (params_long["rsi_min"] < rsi < params_long["rsi_max"]): signal_long = True
    if params_long["use_trend_filter"] and not (price > ema): signal_long = False
    if params_long["use_volume_filter"] and not (cur_vol > vol_sma): signal_long = False

    signal_short = False
    if macd_hist < 0 and (params_short["rsi_min"] < rsi < params_short["rsi_max"]): signal_short = True
    if params_short["use_trend_filter"] and not (price < ema): signal_short = False
    if params_short["use_volume_filter"] and not (cur_vol > vol_sma): signal_short = False

    if signal_long:
        log.info("🚀 LONG SIGNAL FOUND! Opening deal.")
        tp = price * (1 + params_long["tp_pct"] / 100)
        sl = price * (1 - params_long["sl_pct"] / 100)
        save_json(OPENED_DEALS, {"type": "LONG", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat()})
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "LONG", "entry": price, "tp": tp, "sl": sl, "sent": False})
    
    elif signal_short:
        log.info("🩸 SHORT SIGNAL FOUND! Opening deal.")
        tp = price * (1 - params_short["tp_pct"] / 100)
        sl = price * (1 + params_short["sl_pct"] / 100)
        save_json(OPENED_DEALS, {"type": "SHORT", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat()})
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "SHORT", "entry": price, "tp": tp, "sl": sl, "sent": False})
        
    else:
        log.info("No signal. Waiting.")

if __name__ == "__main__":
    main()