# ============================================================
# ARGUS - SIGNAL BOT (DYNAMIC WEIGHTS & RSI ZONES v7.2)
# Path: crypto/signals/signal_bot.py
# ============================================================

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
import psycopg
import pandas as pd

VERSION = "7.2.0"
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

# --- КОНСТАНТЫ ТРЕЙЛИНГА ---
TRAILING_ACTIVATION_PCT = 1.0
TRAILING_ATR_MULTIPLIER = 2.0

# --- КОНСТАНТЫ ОБУЧЕНИЯ ---
DEFAULT_WEIGHTS = {
    "macd": 1.0, 
    "ema": 1.0, 
    "volume": 1.0,
    "rsi_low": 1.0,   # RSI < 30
    "rsi_mid": 1.0,   # 30 <= RSI <= 70
    "rsi_high": 1.0   # RSI > 70
}
DEFAULT_THRESHOLD = 3.0
LEARN_STEP = 0.05
MIN_WEIGHT = 0.1
MAX_WEIGHT = 2.0
MIN_THRESHOLD = 2.0
MAX_THRESHOLD = 4.5

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

def calc_atr(df, period=14):
    if len(df) < period + 1: return None
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean()
    return atr.iloc[-1]

def ensure_weights(bot_info):
    """Защита: если в старом bot_info.json нет новых ключей, добавляем их с дефолтными значениями."""
    weights = bot_info.get("weights", {})
    changed = False
    for k, default_val in DEFAULT_WEIGHTS.items():
        if k not in weights:
            weights[k] = default_val
            changed = True
    
    bot_info["weights"] = weights
    
    if "threshold" not in bot_info:
        bot_info["threshold"] = DEFAULT_THRESHOLD
        changed = True
        
    if "started_at" not in bot_info:
        bot_info["started_at"] = datetime.now(timezone.utc).isoformat()
        changed = True

    if changed:
        save_json(BOT_INFO, bot_info)
        log.info("🧠 bot_info.json updated with new default weights.")

    return bot_info

def update_weights(bot_info, active_signals, pnl_pct):
    """Динамическое обучение: корректировка весов всех индикаторов, включая зоны RSI."""
    bot_info = ensure_weights(bot_info)
    weights = bot_info["weights"]
    direction = 1 if pnl_pct > 0 else -1
    
    if pnl_pct == 0: return bot_info

    for k in weights.keys():
        if k in active_signals:
            weights[k] += (LEARN_STEP * direction)
        else:
            weights[k] -= (LEARN_STEP * direction)
            
        weights[k] = max(MIN_WEIGHT, min(MAX_WEIGHT, round(weights[k], 2)))
    
    bot_info["weights"] = weights
    
    if pnl_pct > 0:
        bot_info["threshold"] = max(MIN_THRESHOLD, round(bot_info.get("threshold", DEFAULT_THRESHOLD) - 0.05, 2))
    else:
        bot_info["threshold"] = min(MAX_THRESHOLD, round(bot_info.get("threshold", DEFAULT_THRESHOLD) + 0.05, 2))

    log.info(f"🧠 BRAIN UPDATED | Profit: {pnl_pct:+.2f}% | New Threshold: {bot_info['threshold']:.2f}")
    log.info(f"🧠 NEW WEIGHTS: {weights}")
    return bot_info

def main():
    log.info(f"STARTING DYNAMIC BOT v{VERSION}")

    bot_info = load_json(BOT_INFO, {})
    bot_info = ensure_weights(bot_info)  # Принудительная инициализация

    current_deal = load_json(OPENED_DEALS, None)
    models = load_json(MODEL_FILE)

    # ==========================================
    # ЛОГИКА 1: ЕСЛИ МЫ В СДЕЛКЕ (СЛЕДИМ И ТРЕЙЛИМ)
    # ==========================================
    if current_deal and "entry" in current_deal:
        deal_type = current_deal.get("type", "LONG")
        log.info(f"In {deal_type} trade. Entry: {current_deal['entry']:.2f} | TP: {current_deal['tp']:.2f} | SL: {current_deal['sl']:.2f}")
        
        try:
            conn = get_conn()
            with conn.cursor() as cur:
                cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 200", (SYMBOL,))
                rows = list(reversed(cur.fetchall()))
            conn.close()
        except Exception as e: return log.error(f"DB error: {e}")

        if len(rows) < 100:
            return log.error("Not enough data for trailing check.")

        high, low, close = float(rows[-1][2]), float(rows[-1][3]), float(rows[-1][4])
        
        entry_price = float(current_deal["entry"])
        current_sl = float(current_deal["sl"])
        
        # --- ТРЕЙЛИНГ ---
        trailing_active = False
        if deal_type == "LONG" and close >= entry_price * (1 + TRAILING_ACTIVATION_PCT / 100):
            trailing_active = True
        elif deal_type == "SHORT" and close <= entry_price * (1 - TRAILING_ACTIVATION_PCT / 100):
            trailing_active = True

        if trailing_active:
            rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
            df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
            df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
            df = df.set_index('open_time')
            df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            
            atr = calc_atr(df_1h, 14)
            
            if atr:
                if deal_type == "LONG":
                    new_sl = close - (atr * TRAILING_ATR_MULTIPLIER)
                    if new_sl > current_sl and new_sl > entry_price:
                        current_deal["sl"] = new_sl
                        save_json(OPENED_DEALS, current_deal)
                        log.info(f"🔼 Trailing SL updated: {current_sl:.2f} -> {new_sl:.2f}")
                        current_sl = new_sl
                else:
                    new_sl = close + (atr * TRAILING_ATR_MULTIPLIER)
                    if new_sl < current_sl and new_sl < entry_price:
                        current_deal["sl"] = new_sl
                        save_json(OPENED_DEALS, current_deal)
                        log.info(f"🔽 Trailing SL updated: {current_sl:.2f} -> {new_sl:.2f}")
                        current_sl = new_sl

        # --- ПРОВЕРКА ВЫХОДА ---
        exit_reason, exit_price = None, close
        
        time_exit_h = 24
        if deal_type == "LONG" and models.get("long"):
            time_exit_h = models["long"]["params"].get("time_exit_h", 24)
        elif deal_type == "SHORT" and models.get("short"):
            time_exit_h = models["short"]["params"].get("time_exit_h", 24)

        open_time = datetime.fromisoformat(current_deal["open_time"])
        time_held_h = (datetime.now(timezone.utc) - open_time).total_seconds() / 3600.0
        
        if deal_type == "LONG":
            if low <= current_sl:
                exit_reason = "TRAILING_STOP" if current_sl > entry_price else "STOP_LOSS"
                exit_price = current_sl
            elif high >= current_deal["tp"]:
                exit_reason, exit_price = "TAKE_PROFIT", current_deal["tp"]
        else:
            if high >= current_sl:
                exit_reason = "TRAILING_STOP" if current_sl < entry_price else "STOP_LOSS"
                exit_price = current_sl
            elif low <= current_deal["tp"]:
                exit_reason, exit_price = "TAKE_PROFIT", current_deal["tp"]

        if not exit_reason and time_held_h >= time_exit_h:
            exit_reason, exit_price = "TIME_EXIT", close

        if exit_reason:
            if deal_type == "LONG":
                pnl_pct = (exit_price - entry_price) / entry_price * 100
            else:
                pnl_pct = (entry_price - exit_price) / entry_price * 100
            
            pnl_usd = 100.0 * (pnl_pct / 100) 
            
            log.info(f"🔴 {deal_type} CLOSED: {exit_reason} | PnL: {pnl_pct:.2f}%")

            # --- ОБУЧЕНИЕ ---
            active_sigs = current_deal.get("active_signals", [])
            bot_info = update_weights(bot_info, active_sigs, pnl_pct)
            save_json(BOT_INFO, bot_info)

            # --- ЗАПИСЬ ИСТОРИИ ---
            closed = load_json(CLOSED_DEALS, [])
            closed.append({
                "type": deal_type, "entry": entry_price, "exit": exit_price,
                "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2),
                "reason": exit_reason, "close_time": datetime.now(timezone.utc).isoformat()
            })
            save_json(CLOSED_DEALS, closed)
            save_json(OPENED_DEALS, None)

            save_json(INFO_DEAL, {
                "type": "CLOSE", "deal_type": deal_type, "reason": exit_reason,
                "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2), "sent": False
            })
        return

    # ==========================================
    # ЛОГИКА 2: ПОИСК СИГНАЛА (DYNAMIC SCORE)
    # ==========================================
    if not models.get("long") or not models.get("short"):
        return log.error("Models not found or outdated.")

    params_long = models["long"]["params"]
    params_short = models["short"]["params"]
    weights = bot_info["weights"]
    threshold = bot_info["threshold"]

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 400", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e: return log.error(f"DB error: {e}")

    rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
    df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df = df.set_index('open_time')

    df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()

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

    # --- ОЦЕНКА LONG ---
    score_long = 0.0
    active_long = []
    
    if macd_hist is not None and macd_hist > 0:
        score_long += weights["macd"]; active_long.append("macd")
    if ema is not None and price > ema:
        score_long += weights["ema"]; active_long.append("ema")
    if cur_vol > vol_sma:
        score_long += weights["volume"]; active_long.append("volume")
    
    # --- ЛОГИКА RSI ЗОН ДЛЯ LONG ---
    if rsi is not None:
        if rsi < 30:
            score_long += weights["rsi_low"]; active_long.append("rsi_low")
        elif rsi < 70:
            score_long += weights["rsi_mid"]; active_long.append("rsi_mid")

    # --- ОЦЕНКА SHORT ---
    score_short = 0.0
    active_short = []

    if macd_hist is not None and macd_hist < 0:
        score_short += weights["macd"]; active_short.append("macd")
    if ema is not None and price < ema:
        score_short += weights["ema"]; active_short.append("ema")
    if cur_vol > vol_sma:
        score_short += weights["volume"]; active_short.append("volume")
        
    # --- ЛОГИКА RSI ЗОН ДЛЯ SHORT ---
    if rsi is not None:
        if rsi > 70:
            score_short += weights["rsi_high"]; active_short.append("rsi_high")
        elif rsi > 30:
            score_short += weights["rsi_mid"]; active_short.append("rsi_mid")

    log.info(f"1h Check | Price: {price:.1f} | RSI: {rsi:.1f} | LONG Score: {score_long:.2f} | SHORT Score: {score_short:.2f} | Threshold: {threshold:.2f}")

    if score_long >= threshold and score_long > score_short:
        log.info(f"🚀 LONG SIGNAL FOUND! Score: {score_long:.2f}")
        tp = price * (1 + params_long["tp_pct"] / 100)
        sl = price * (1 - params_long["sl_pct"] / 100)
        
        deal = {"type": "LONG", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_long}
        save_json(OPENED_DEALS, deal)
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "LONG", "entry": price, "tp": tp, "sl": sl, "sent": False})
    
    elif score_short >= threshold and score_short > score_long:
        log.info(f"🩸 SHORT SIGNAL FOUND! Score: {score_short:.2f}")
        tp = price * (1 - params_short["tp_pct"] / 100)
        sl = price * (1 + params_short["sl_pct"] / 100)
        
        deal = {"type": "SHORT", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_short}
        save_json(OPENED_DEALS, deal)
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "SHORT", "entry": price, "tp": tp, "sl": sl, "sent": False})
        
    else:
        log.info("No signal. Waiting.")

if __name__ == "__main__":
    main()