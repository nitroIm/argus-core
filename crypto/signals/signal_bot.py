# ============================================================
# ARGUS - SIGNAL BOT v9.1.0 (STARTED_AT FIX)
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

VERSION = "9.1.0"
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

TRAILING_ACTIVATION_PCT = 1.0
TRAILING_ATR_MULTIPLIER = 2.0

DEFAULT_THRESHOLD = 2.0
LEARN_STEP = 0.05
MIN_WEIGHT = 0.1
MAX_WEIGHT = 2.0
MIN_ADAPTIVE_THRESHOLD = 1.5
HISTORY_DAYS = 7
PERCENTILE = 90

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
    # Проверяем и создаем структуру для INTRADAY
    if "intraday" not in bot_info: 
        bot_info["intraday"] = {
            "weights": {"macd": 1.0, "ema": 1.0, "volume": 1.0, "rsi_low": 1.0, "rsi_mid": 1.0, "rsi_high": 1.0}, 
            "threshold": DEFAULT_THRESHOLD, 
            "score_history": [], 
            "score_archive": []
        }
    if "started_at" not in bot_info["intraday"]:
        bot_info["intraday"]["started_at"] = datetime.now(timezone.utc).isoformat()

    # Проверяем и создаем структуру для SWING
    if "swing" not in bot_info: 
        bot_info["swing"] = {
            "weights": {"macd": 1.0, "ema": 1.0, "volume": 1.0, "rsi_low": 1.0, "rsi_mid": 1.0, "rsi_high": 1.0}, 
            "threshold": DEFAULT_THRESHOLD, 
            "score_history": [], 
            "score_archive": []
        }
    if "started_at" not in bot_info["swing"]:
        bot_info["swing"]["started_at"] = datetime.now(timezone.utc).isoformat()

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
    cutoff = now - timedelta(days=HISTORY_DAYS)
    
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
                rec = {"date": d, "avg_score": 0, "max_score": 0, "min_score": 0, "trades_count": 0, "winrate": 0}
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

def process_mode(mode, deals, bot_info, models, df_1h):
    # 1. Проверка открытой сделки
    if mode in deals and "entry" in deals[mode]:
        deal = deals[mode]
        deal_type = deal.get("type", "LONG")
        
        close = float(df_1h.iloc[-1]['close'])
        entry = float(deal['entry'])
        sl = float(deal['sl'])
        atr = float(df_1h.iloc[-1]['atr_14'])
        
        # Трейлинг
        trailing_active = (deal_type == "LONG" and close >= entry * 1.01) or (deal_type == "SHORT" and close <= entry * 0.99)
        if trailing_active and atr:
            new_sl = close - (atr * 2.0) if deal_type == "LONG" else close + (atr * 2.0)
            if (deal_type == "LONG" and new_sl > sl and new_sl > entry) or (deal_type == "SHORT" and new_sl < sl and new_sl < entry):
                deal['sl'] = new_sl
                sl_pct = (new_sl-entry)/entry*100 if deal_type=="LONG" else (entry-new_sl)/entry*100
                append_event({"type": "UPDATE", "mode": mode, "deal_type": deal_type, "new_sl": new_sl, "sl_pct": round(sl_pct, 2)})
                log.info(f"🔼 [{mode.upper()}] Trailing SL updated: {sl:.2f} -> {new_sl:.2f}")
                sl = new_sl

        # Выход
        exit_reason, exit_price = None, close
        time_held_h = (datetime.now(timezone.utc) - datetime.fromisoformat(deal["open_time"])).total_seconds() / 3600.0
        time_exit_h = models[mode][deal_type.lower()]["params"].get("time_exit_h", 24)
        
        # Для интрадея: принудительное закрытие при смене дня
        if mode == "intraday" and datetime.now(timezone.utc).date() > datetime.fromisoformat(deal["open_time"]).date():
            exit_reason, exit_price = "TIME_EXIT_DAY", close
        elif deal_type == "LONG":
            if float(df_1h.iloc[-1]['low']) <= sl: exit_reason, exit_price = ("TRAILING_STOP" if sl > entry else "STOP_LOSS"), sl
            elif float(df_1h.iloc[-1]['high']) >= deal["tp"]: exit_reason, exit_price = "TAKE_PROFIT", deal["tp"]
        else:
            if float(df_1h.iloc[-1]['high']) >= sl: exit_reason, exit_price = ("TRAILING_STOP" if sl < entry else "STOP_LOSS"), sl
            elif float(df_1h.iloc[-1]['low']) <= deal["tp"]: exit_reason, exit_price = "TAKE_PROFIT", deal["tp"]
            
        if not exit_reason and time_held_h >= time_exit_h: exit_reason, exit_price = "TIME_EXIT", close

        if exit_reason:
            pnl_pct = (exit_price - entry) / entry * 100 if deal_type == "LONG" else (entry - exit_price) / entry * 100
            pnl_usd = 100.0 * (pnl_pct / 100)
            
            append_event({"type": "CLOSE", "mode": mode, "deal_type": deal_type, "reason": exit_reason, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2)})
            log.info(f"🔴 [{mode.upper()}] {deal_type} CLOSED: {exit_reason} | PnL: {pnl_pct:.2f}%")
            
            # Обучение (только для базовой модели)
            if models[mode][deal_type.lower()].get("source") == "model_base":
                active = deal.get("active_signals", [])
                direction = 1 if pnl_pct > 0 else -1
                for k in bot_info[mode]["weights"].keys():
                    if k in active: bot_info[mode]["weights"][k] += (0.05 * direction)
                    else: bot_info[mode]["weights"][k] -= (0.05 * direction)
                    bot_info[mode]["weights"][k] = max(0.1, min(2.0, round(bot_info[mode]["weights"][k], 2)))
            
            closed = load_json(CLOSED_DEALS, [])
            closed.append({"type": deal_type, "entry": entry, "exit": exit_price, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2), "reason": exit_reason, "mode": mode, "close_time": datetime.now(timezone.utc).isoformat()})
            save_json(CLOSED_DEALS, closed)
            
            del deals[mode] # Удаляем сделку
            bot_info[mode]["last_close_ts"] = datetime.now(timezone.utc).isoformat()
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

    score_long, active_long = 0.0, []
    if source_long == "model_base":
        if macd_hist > 0: score_long += bot_info[mode]["weights"]["macd"]; active_long.append("macd")
        if price > ema: score_long += bot_info[mode]["weights"]["ema"]; active_long.append("ema")
        if cur_vol > vol_sma: score_long += bot_info[mode]["weights"]["volume"]; active_long.append("volume")
        if rsi < 30: score_long += bot_info[mode]["weights"]["rsi_low"]; active_long.append("rsi_low")
        elif rsi < 70: score_long += bot_info[mode]["weights"]["rsi_mid"]; active_long.append("rsi_mid")
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
        if macd_hist < 0: score_short += bot_info[mode]["weights"]["macd"]; active_short.append("macd")
        if price < ema: score_short += bot_info[mode]["weights"]["ema"]; active_short.append("ema")
        if cur_vol > vol_sma: score_short += bot_info[mode]["weights"]["volume"]; active_short.append("volume")
        if rsi > 70: score_short += bot_info[mode]["weights"]["rsi_high"]; active_short.append("rsi_high")
        elif rsi > 30: score_short += bot_info[mode]["weights"]["rsi_mid"]; active_short.append("rsi_mid")
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

    # Адаптивный порог
    max_s = max(score_long, score_short)
    bot_info[mode]["score_history"].append({"ts": datetime.now(timezone.utc).isoformat(), "score": max_s})
    bot_info = archive_and_clean_history(bot_info, mode)
    fresh = [e["score"] for e in bot_info[mode]["score_history"]]
    thresh = float(np.percentile(fresh, 90)) if len(fresh) > 50 else DEFAULT_THRESHOLD
    thresh = max(1.5, min(3.5, thresh))

    last_close = bot_info[mode].get("last_close_ts")
    can_open = True
    if last_close:
        h_since = (datetime.now(timezone.utc) - datetime.fromisoformat(last_close)).total_seconds() / 3600.0
        if h_since < 1.0: can_open = False

    log.info(f"[{mode.upper()}] Price: {price:.1f} | LONG: {score_long:.2f} | SHORT: {score_short:.2f} | Thresh: {thresh:.2f}")

    if can_open and score_long >= thresh and score_long > score_short:
        tp = price * (1 + models[mode]["long"]["params"].get("tp_pct", 2.0) / 100)
        sl = price * (1 - models[mode]["long"]["params"].get("sl_pct", 1.0) / 100)
        deals[mode] = {"type": "LONG", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_long}
        append_event({"type": "ENTRY", "mode": mode, "deal_type": "LONG", "entry": price, "tp": tp, "sl": sl})
        log.info(f"🚀 [{mode.upper()}] LONG SIGNAL!")
    elif can_open and score_short >= thresh and score_short > score_long:
        tp = price * (1 - models[mode]["short"]["params"].get("tp_pct", 2.0) / 100)
        sl = price * (1 + models[mode]["short"]["params"].get("sl_pct", 1.0) / 100)
        deals[mode] = {"type": "SHORT", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_short}
        append_event({"type": "ENTRY", "mode": mode, "deal_type": "SHORT", "entry": price, "tp": tp, "sl": sl})
        log.info(f"🩸 [{mode.upper()}] SHORT SIGNAL!")

def main():
    log.info(f"STARTING DUAL BOT v{VERSION}")
    
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

    process_mode("intraday", deals, bot_info, models, df_1h)
    process_mode("swing", deals, bot_info, models, df_1h)
    
    save_json(OPENED_DEALS, deals)
    save_json(BOT_INFO, bot_info)

if __name__ == "__main__":
    main()