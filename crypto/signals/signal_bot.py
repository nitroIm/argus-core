# ============================================================
# ARGUS - SIGNAL BOT v9.3.1 (CONFIG INTEGRATION + FIX TIME)
# Path: crypto/signals/signal_bot.py
# ============================================================

import os
import json
import logging
import base64
import pickle
from pathlib import Path
from datetime import datetime, timezone, timedelta
import psycopg
import pandas as pd
import numpy as np
import xgboost as xgb

VERSION = "9.3.1"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.signal_bot")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
MODEL_FILE = CRYPTO_ROOT / "data" / "models" / "best_params.json"
SIGNALS_DIR = CRYPTO_ROOT / "data" / "signals"
CONFIG_FILE = CRYPTO_ROOT / "data" / "intel" / "bot_config.json"

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
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def append_event(event):
    queue = load_json(INFO_DEAL, [])
    if not isinstance(queue, list): queue = []
    queue.append(event)
    save_json(INFO_DEAL, queue)

def ensure_weights(bot_info):
    for mode in ["intraday", "swing"]:
        if mode not in bot_info: 
            bot_info[mode] = {"score_history": [], "score_archive": []}
        if "started_at" not in bot_info[mode]:
            bot_info[mode]["started_at"] = datetime.now(timezone.utc).isoformat()
        if "balance" not in bot_info[mode]:
            bot_info[mode]["balance"] = 100.0
        if "start_balance" not in bot_info[mode]:
            bot_info[mode]["start_balance"] = 100.0
        if "last_check_ts" not in bot_info[mode]:
            bot_info[mode]["last_check_ts"] = datetime.now(timezone.utc).isoformat()

    save_json(BOT_INFO, bot_info)
    return bot_info

def get_features_for_ml(df_1h, feature_cols):
    df = df_1h.copy()
    df['rsi_norm'] = df['rsi'] / 100.0
    df['rsi_7_norm'] = df['rsi_7'] / 100.0
    df['rsi_21_norm'] = df['rsi_21'] / 100.0
    df['macd_norm'] = df['macd_hist'] / df['close']
    df['ema50_diff'] = (df['close'] - df['ema_50']) / df['ema_50']
    df['ema20_diff'] = (df['close'] - df['ema_20']) / df['ema_20']
    df['ema200_diff'] = (df['close'] - df['ema_200']) / df['ema_200']
    df['vol_ratio'] = df['volume'] / df['vol_sma']
    df['atr7_pct'] = df['atr_7'] / df['close']
    df['atr14_pct'] = df['atr_14'] / df['close']
    df['atr50_pct'] = df['atr_50'] / df['close']
    df['atr100_pct'] = df['atr_100'] / df['close']
    df['avg_trade_norm'] = df['avg_trade_size'] / df['volume'].rolling(20).mean()
    df['pc1'] = df['price_change_1h']
    df['pc4'] = df['price_change_4h']
    df['pc24'] = df['price_change_24h']
    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']:
            df[f'{f}_lag{lag}'] = df[f].shift(lag)
    df = df.dropna()
    return df[feature_cols].iloc[[-1]].values

def archive_and_clean_history(bot_info, mode):
    now = datetime.now(timezone.utc)
    history = bot_info[mode].get("score_history", [])
    archive = bot_info[mode].get("score_archive", [])
    cutoff = now - timedelta(days=7)
    
    fresh, old = [], []
    for entry in history:
        ts = datetime.fromisoformat(entry["ts"]) if isinstance(entry, dict) and "ts" in entry else now - timedelta(days=8)
        if ts.tzinfo is None: ts = ts.replace(tzinfo=timezone.utc)
        (fresh if ts >= cutoff else old).append(entry)
            
    if old:
        by_date = {}
        for e in old:
            d = e["ts"][:10]
            by_date.setdefault(d, []).append(e["score"])
        for d, scores in by_date.items():
            rec = next((a for a in archive if a["date"] == d), None)
            if not rec:
                rec = {"date": d, "avg_score": 0, "max_score": 0, "min_score": 0}
                archive.append(rec)
            rec["avg_score"] = round(float(np.mean(scores)), 2)
            rec["max_score"] = round(float(np.max(scores)), 2)
            rec["min_score"] = round(float(np.min(scores)), 2)
        archive.sort(key=lambda x: x["date"], reverse=True)
        archive = archive[:365]
        
    bot_info[mode]["score_history"] = fresh
    bot_info[mode]["score_archive"] = archive
    return bot_info

def calculate_indicators(df_1h):
    close = df_1h['close']
    df_1h['rsi'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(14).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(14).mean())
    df_1h['rsi_7'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(7).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(7).mean())
    df_1h['rsi_21'] = 100 - 100 / (1 + close.diff().where(close.diff() > 0, 0).rolling(21).mean() / (-close.diff().where(close.diff() < 0, 0)).rolling(21).mean())
    df_1h['ema_20'] = close.ewm(span=20).mean()
    df_1h['ema_50'] = close.ewm(span=50).mean()
    df_1h['ema_200'] = close.ewm(span=200).mean()
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    df_1h['macd_hist'] = macd - macd.ewm(span=9).mean()
    df_1h['vol_sma'] = df_1h['volume'].rolling(20).mean()
    df_1h['atr_7'] = (df_1h['high'] - df_1h['low']).rolling(7).mean()
    df_1h['atr_14'] = (df_1h['high'] - df_1h['low']).rolling(14).mean()
    df_1h['atr_50'] = (df_1h['high'] - df_1h['low']).rolling(50).mean()
    df_1h['atr_100'] = (df_1h['high'] - df_1h['low']).rolling(100).mean()
    df_1h['buy_ratio'] = df_1h['taker_buy_volume'] / df_1h['volume'].replace(0, 1)
    df_1h['avg_trade_size'] = df_1h['volume'] / df_1h['count'].replace(0, 1)
    df_1h['price_change_1h'] = close.pct_change(1)
    df_1h['price_change_4h'] = close.pct_change(4)
    df_1h['price_change_24h'] = close.pct_change(24)
    return df_1h.dropna()

def process_mode(mode, deals, bot_info, models, df_1h, config):
    cfg = config.get(mode, {})
    fee_pct = config.get("global", {}).get("fee_pct", 0.001)
    now_utc = datetime.now(timezone.utc)
    
    # 1. Проверка открытой сделки
    if mode in deals and "entry" in deals[mode]:
        deal = deals[mode]
        deal_type = deal.get("type", "LONG")
        
        entry = float(deal['entry'])
        sl = float(deal['sl'])
        tp = float(deal['tp'])
        
        last_check_str = bot_info[mode].get("last_check_ts", deal["open_time"])
        last_check = datetime.fromisoformat(last_check_str)
        df_missed = df_1h[df_1h['open_time'] > last_check]

        atr = float(df_1h.iloc[-1]['atr_14'])
        act_pct = cfg.get("trailing_activation_pct", 0.5)
        atr_mult = cfg.get("trailing_atr_multiplier", 1.5)

        exit_reason, exit_price = None, float(df_1h.iloc[-1]['close'])
        old_sl = sl
        
        # Ретроактивная проверка по всем пропущенным свечам
        for _, row in df_missed.iterrows():
            if deal_type == "LONG":
                if row['high'] >= entry * (1 + act_pct/100):
                    new_sl = row['high'] - (atr * atr_mult)
                    if new_sl > sl: sl = round(new_sl, 2)
                if row['low'] <= sl: 
                    exit_reason, exit_price = ("TRAILING_STOP" if sl > entry else "STOP_LOSS"), sl
                    break
                if row['high'] >= tp: 
                    exit_reason, exit_price = "TAKE_PROFIT", tp
                    break
            else: # SHORT
                if row['low'] <= entry * (1 - act_pct/100):
                    new_sl = row['low'] + (atr * atr_mult)
                    if new_sl < sl: sl = round(new_sl, 2)
                if row['high'] >= sl: 
                    exit_reason, exit_price = ("TRAILING_STOP" if sl < entry else "STOP_LOSS"), sl
                    break
                if row['low'] <= tp: 
                    exit_reason, exit_price = "TAKE_PROFIT", tp
                    break
        
        if sl != old_sl:
            deal['sl'] = sl
            sl_pct = (sl-entry)/entry*100 if deal_type=="LONG" else (entry-sl)/entry*100
            append_event({"type": "UPDATE", "mode": mode, "deal_type": deal_type, "new_sl": sl, "sl_pct": round(sl_pct, 2)})

        # Принудительное закрытие Intraday (например, в 19:00)
        close_h = cfg.get("close_hour_utc")
        if not exit_reason and mode == "intraday" and close_h is not None and now_utc.hour >= close_h:
            exit_reason, exit_price = "TIME_EXIT_DAY", float(df_1h.iloc[-1]['close'])

        # Выход по времени для Swing
        if not exit_reason and mode == "swing":
            time_held = (now_utc - datetime.fromisoformat(deal["open_time"])).total_seconds() / 3600.0
            time_exit_h = models[mode][deal_type.lower()]["params"].get("time_exit_h", 24)
            if time_held >= time_exit_h:
                exit_reason, exit_price = "TIME_EXIT", float(df_1h.iloc[-1]['close'])

        if exit_reason:
            pnl_pct = (exit_price - entry) / entry * 100 if deal_type == "LONG" else (entry - exit_price) / entry * 100
            pnl_pct -= (fee_pct * 2 * 100)
            pnl_usd = bot_info[mode]["balance"] * (pnl_pct / 100)
            bot_info[mode]["balance"] = round(bot_info[mode]["balance"] + pnl_usd, 2)
            
            append_event({
                "type": "CLOSE", 
                "mode": mode, 
                "deal_type": deal_type, 
                "reason": exit_reason, 
                "pnl_pct": round(pnl_pct, 2), 
                "pnl_usd": round(pnl_usd, 2),
                "new_balance": bot_info[mode]["balance"]
            })
            
            closed = load_json(CLOSED_DEALS, [])
            closed.append({"type": deal_type, "entry": entry, "exit": exit_price, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2), "reason": exit_reason, "mode": mode, "close_time": now_utc.isoformat(), "balance_after": bot_info[mode]["balance"]})
            save_json(CLOSED_DEALS, closed)
            
            del deals[mode]
            bot_info[mode]["last_close_ts"] = now_utc.isoformat()
            bot_info[mode]["last_check_ts"] = now_utc.isoformat()
            save_json(BOT_INFO, bot_info)
            save_json(OPENED_DEALS, deals)
        else:
            bot_info[mode]["last_check_ts"] = now_utc.isoformat()
            save_json(BOT_INFO, bot_info)
        return

    # 2. Поиск сигнала (если сделки нет)
    source_long = models[mode]["long"].get("source", "model_base")
    source_short = models[mode]["short"].get("source", "model_base")
    
    price = float(df_1h.iloc[-1]['close'])
    rsi = float(df_1h.iloc[-1]['rsi'])
    macd_hist = float(df_1h.iloc[-1]['macd_hist'])
    ema = float(df_1h.iloc[-1]['ema_50'])
    vol_sma = float(df_1h.iloc[-1]['vol_sma'])
    cur_vol = float(df_1h.iloc[-1]['volume'])

    w = cfg.get("weights", {})

    score_long, active_long = 0.0, []
    if source_long == "model_base":
        if macd_hist > 0: score_long += w.get("macd", 1.0); active_long.append("macd")
        if price > ema: score_long += w.get("ema", 1.0); active_long.append("ema")
        if cur_vol > vol_sma: score_long += w.get("volume", 1.0); active_long.append("volume")
        if rsi < 30: score_long += w.get("rsi_low", 1.0); active_long.append("rsi_low")
        elif rsi < 70: score_long += w.get("rsi_mid", 1.0); active_long.append("rsi_mid")
    else:
        try:
            m_b64 = models[mode]["long"]["params"]["model_b64"]
            feat = models[mode]["long"]["params"]["features"]
            raw = base64.b64decode(m_b64)
            if "xgb" in source_long:
                bst = xgb.Booster(); bst.load_model(bytearray(raw))
                score_long = bst.predict(xgb.DMatrix(get_features_for_ml(df_1h, feat)))[0] * 10
            else:
                mdl = pickle.loads(raw)
                score_long = mdl.predict_proba(get_features_for_ml(df_1h, feat))[0][1] * 10
        except: pass

    score_short, active_short = 0.0, []
    if source_short == "model_base":
        if macd_hist < 0: score_short += w.get("macd", 1.0); active_short.append("macd")
        if price < ema: score_short += w.get("ema", 1.0); active_short.append("ema")
        if cur_vol > vol_sma: score_short += w.get("volume", 1.0); active_short.append("volume")
        if rsi > 70: score_short += w.get("rsi_high", 1.0); active_short.append("rsi_high")
        elif rsi > 30: score_short += w.get("rsi_mid", 1.0); active_short.append("rsi_mid")
    else:
        try:
            m_b64 = models[mode]["short"]["params"]["model_b64"]
            feat = models[mode]["short"]["params"]["features"]
            raw = base64.b64decode(m_b64)
            if "xgb" in source_short:
                bst = xgb.Booster(); bst.load_model(bytearray(raw))
                score_short = (1.0 - bst.predict(xgb.DMatrix(get_features_for_ml(df_1h, feat)))[0]) * 10
            else:
                mdl = pickle.loads(raw)
                score_short = mdl.predict_proba(get_features_for_ml(df_1h, feat))[0][0] * 10
        except: pass

    thresh = cfg.get("threshold", 2.0)
    
    # --- ИСПРАВЛЕННАЯ ЛОГИКА ВРЕМЕНИ ---
    can_open = True
    no_entry_str = cfg.get("no_entry_time_utc") # Ожидаем формат "18:30"
    if mode == "intraday" and no_entry_str:
        try:
            ne_h, ne_m = map(int, no_entry_str.split(":"))
            if now_utc.hour > ne_h or (now_utc.hour == ne_h and now_utc.minute >= ne_m):
                can_open = False
        except Exception as e:
            log.error(f"Ошибка парсинга no_entry_time_utc: {e}")

    last_close = bot_info[mode].get("last_close_ts")
    if last_close:
        h_since = (now_utc - datetime.fromisoformat(last_close)).total_seconds() / 3600.0
        if h_since < 1.0: can_open = False

    log.info(f"[{mode.upper()}] Price: {price:.1f} | LONG: {score_long:.2f} | SHORT: {score_short:.2f} | Thresh: {thresh:.2f}")

    if can_open and score_long >= thresh and score_long > score_short:
        tp = price * (1 + cfg.get("tp_pct", 2.0) / 100)
        sl = price * (1 - cfg.get("sl_pct", 1.0) / 100)
        deals[mode] = {"type": "LONG", "entry": price, "tp": tp, "sl": sl, "open_time": now_utc.isoformat(), "active_signals": active_long}
        append_event({"type": "ENTRY", "mode": mode, "deal_type": "LONG", "entry": price, "tp": tp, "sl": sl})
        log.info(f"🚀 [{mode.upper()}] LONG SIGNAL!")
    elif can_open and score_short >= thresh and score_short > score_long:
        tp = price * (1 - cfg.get("tp_pct", 2.0) / 100)
        sl = price * (1 + cfg.get("sl_pct", 1.0) / 100)
        deals[mode] = {"type": "SHORT", "entry": price, "tp": tp, "sl": sl, "open_time": now_utc.isoformat(), "active_signals": active_short}
        append_event({"type": "ENTRY", "mode": mode, "deal_type": "SHORT", "entry": price, "tp": tp, "sl": sl})
        log.info(f"🩸 [{mode.upper()}] SHORT SIGNAL!")

    bot_info[mode]["last_check_ts"] = now_utc.isoformat()
    save_json(BOT_INFO, bot_info)

def main():
    log.info(f"STARTING DUAL BOT v{VERSION}")
    
    config = load_json(CONFIG_FILE, {})
    if not config:
        log.error("bot_config.json не найден. Ждем Тюнера.")
        return

    bot_info = ensure_weights(load_json(BOT_INFO, {}))
    models = load_json(MODEL_FILE)
    if not models.get("swing") or not models.get("intraday"): return log.error("Models not found.")
    
    deals = load_json(OPENED_DEALS, {})
    if "intraday" not in deals: deals["intraday"] = {}
    if "swing" not in deals: deals["swing"] = {}

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 500", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e: return log.error(f"DB error: {e}")

    df = pd.DataFrame([(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7])) for r in rows], columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_volume', 'count'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df_1h = df.set_index('open_time').resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'taker_buy_volume': 'sum', 'count': 'sum'}).dropna().reset_index()
    df_1h = calculate_indicators(df_1h.set_index('open_time')).reset_index()

    process_mode("intraday", deals, bot_info, models, df_1h, config)
    process_mode("swing", deals, bot_info, models, df_1h, config)
    
    save_json(OPENED_DEALS, deals)
    save_json(BOT_INFO, bot_info)

if __name__ == "__main__":
    main()