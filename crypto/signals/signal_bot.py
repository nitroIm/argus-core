# ============================================================
# ARGUS - SIGNAL BOT (UNIVERSAL v8.0)
# Path: crypto/signals/signal_bot.py
# ============================================================

import os
import json
import logging
import base64
import pickle
from pathlib import Path
from datetime import datetime, timezone
import psycopg
import pandas as pd
import numpy as np
import xgboost as xgb

VERSION = "8.0.0"
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

DEFAULT_WEIGHTS = {"macd": 1.0, "ema": 1.0, "volume": 1.0, "rsi_low": 1.0, "rsi_mid": 1.0, "rsi_high": 1.0}
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

def calculate_ema(s, p): return s.ewm(span=p, adjust=False).mean().iloc[-1] if len(s) >= p else None
def calculate_rsi(s, p=14):
    if len(s) < p + 1: return None
    d = s.diff(); g = (d.where(d > 0, 0)).rolling(window=p).mean(); l = (-d.where(d < 0, 0)).rolling(window=p).mean()
    return (100 - (100 / (1 + g/l))).iloc[-1]

def calculate_macd_hist(s):
    if len(s) < 35: return None
    ema_fast = s.ewm(span=12).mean(); ema_slow = s.ewm(span=26).mean()
    macd_line = ema_fast - ema_slow; signal_line = macd_line.ewm(span=9).mean()
    return (macd_line - signal_line).iloc[-1]

def calc_atr_series(df, period):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def ensure_weights(bot_info):
    weights = bot_info.get("weights", {})
    for k, default_val in DEFAULT_WEIGHTS.items():
        if k not in weights: weights[k] = default_val
    bot_info["weights"] = weights
    if "threshold" not in bot_info: bot_info["threshold"] = DEFAULT_THRESHOLD
    if "started_at" not in bot_info: bot_info["started_at"] = datetime.now(timezone.utc).isoformat()
    save_json(BOT_INFO, bot_info)
    return bot_info

def update_weights(bot_info, active_signals, pnl_pct):
    bot_info = ensure_weights(bot_info)
    weights = bot_info["weights"]
    direction = 1 if pnl_pct > 0 else -1
    if pnl_pct == 0: return bot_info
    for k in weights.keys():
        if k in active_signals: weights[k] += (LEARN_STEP * direction)
        else: weights[k] -= (LEARN_STEP * direction)
        weights[k] = max(MIN_WEIGHT, min(MAX_WEIGHT, round(weights[k], 2)))
    bot_info["weights"] = weights
    if pnl_pct > 0:
        bot_info["threshold"] = max(MIN_THRESHOLD, round(bot_info.get("threshold", DEFAULT_THRESHOLD) - 0.05, 2))
    else:
        bot_info["threshold"] = min(MAX_THRESHOLD, round(bot_info.get("threshold", DEFAULT_THRESHOLD) + 0.05, 2))
    log.info(f"🧠 BRAIN UPDATED | Profit: {pnl_pct:+.2f}% | Threshold: {bot_info['threshold']:.2f}")
    log.info(f"🧠 WEIGHTS: {weights}")
    return bot_info

def get_features_for_ml(df_1h, feature_cols):
    """Подготовка фичей для ML-модели (должна совпадать с тем, что в модели)."""
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

def main():
    log.info(f"STARTING UNIVERSAL BOT v{VERSION}")

    bot_info = load_json(BOT_INFO, {})
    bot_info = ensure_weights(bot_info)

    current_deal = load_json(OPENED_DEALS, None)
    models = load_json(MODEL_FILE)

    if not models.get("long") or not models.get("short"):
        return log.error("Models not found.")

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
        
        rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows]
        df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume'])
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df = df.set_index('open_time')
        df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        
        atr = calc_atr_series(df_1h, 14).iloc[-1] if len(df_1h) > 14 else None
        
        trailing_active = False
        if deal_type == "LONG" and close >= entry_price * (1 + TRAILING_ACTIVATION_PCT / 100): trailing_active = True
        elif deal_type == "SHORT" and close <= entry_price * (1 - TRAILING_ACTIVATION_PCT / 100): trailing_active = True

        if trailing_active and atr:
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

        exit_reason, exit_price = None, close
        time_exit_h = 24
        if deal_type == "LONG" and models.get("long"): time_exit_h = models["long"]["params"].get("time_exit_h", 24)
        elif deal_type == "SHORT" and models.get("short"): time_exit_h = models["short"]["params"].get("time_exit_h", 24)

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
            if deal_type == "LONG": pnl_pct = (exit_price - entry_price) / entry_price * 100
            else: pnl_pct = (entry_price - exit_price) / entry_price * 100
            pnl_usd = 100.0 * (pnl_pct / 100) 
            
            log.info(f"🔴 {deal_type} CLOSED: {exit_reason} | PnL: {pnl_pct:.2f}%")

            # Обучение только для базовой модели (ML не обучается на ходу)
            if models["long"].get("source") == "model_base" or models["short"].get("source") == "model_base":
                active_sigs = current_deal.get("active_signals", [])
                bot_info = update_weights(bot_info, active_sigs, pnl_pct)
                save_json(BOT_INFO, bot_info)

            closed = load_json(CLOSED_DEALS, [])
            closed.append({"type": deal_type, "entry": entry_price, "exit": exit_price, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2), "reason": exit_reason, "close_time": datetime.now(timezone.utc).isoformat()})
            save_json(CLOSED_DEALS, closed)
            save_json(OPENED_DEALS, None)
            save_json(INFO_DEAL, {"type": "CLOSE", "deal_type": deal_type, "reason": exit_reason, "pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2), "sent": False})
        return

    # ==========================================
    # ЛОГИКА 2: ПОИСК СИГНАЛА (DYNAMIC/ML)
    # ==========================================
    source_long = models["long"].get("source", "model_base")
    source_short = models["short"].get("source", "model_base")

    try:
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol=%s AND timeframe='15m' AND market_type='spot' ORDER BY open_time DESC LIMIT 500", (SYMBOL,))
            rows = list(reversed(cur.fetchall()))
        conn.close()
    except Exception as e: return log.error(f"DB error: {e}")

    rows_float = [(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5]), float(r[6]), int(r[7])) for r in rows]
    df = pd.DataFrame(rows_float, columns=['open_time', 'open', 'high', 'low', 'close', 'volume', 'taker_buy_volume', 'count'])
    df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
    df = df.set_index('open_time')
    df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'taker_buy_volume': 'sum', 'count': 'sum'}).dropna()

    if len(df_1h) < 55: return log.error("Not enough data.")

    # Считаем индикаторы для 1h
    df_1h['rsi'] = df_1h['close'].apply(lambda x: x) # Заглушка, ниже ручной расчет
    close_series = df_1h['close']
    df_1h['rsi'] = 100 - 100 / (1 + close_series.diff().where(close_series.diff() > 0, 0).rolling(14).mean() / (-close_series.diff().where(close_series.diff() < 0, 0)).rolling(14).mean())
    df_1h['rsi_7'] = 100 - 100 / (1 + close_series.diff().where(close_series.diff() > 0, 0).rolling(7).mean() / (-close_series.diff().where(close_series.diff() < 0, 0)).rolling(7).mean())
    df_1h['rsi_21'] = 100 - 100 / (1 + close_series.diff().where(close_series.diff() > 0, 0).rolling(21).mean() / (-close_series.diff().where(close_series.diff() < 0, 0)).rolling(21).mean())
    df_1h['ema_20'] = close_series.ewm(span=20).mean()
    df_1h['ema_50'] = close_series.ewm(span=50).mean()
    df_1h['ema_200'] = close_series.ewm(span=200).mean()
    df_1h['macd_hist'] = close_series.ewm(span=12).mean() - close_series.ewm(span=26).mean()
    df_1h['macd_hist'] = df_1h['macd_hist'] - df_1h['macd_hist'].ewm(span=9).mean()
    df_1h['vol_sma'] = df_1h['volume'].rolling(20).mean()
    df_1h['atr_7'] = calc_atr_series(df_1h, 7)
    df_1h['atr_14'] = calc_atr_series(df_1h, 14)
    df_1h['atr_50'] = calc_atr_series(df_1h, 50)
    df_1h['atr_100'] = calc_atr_series(df_1h, 100)
    df_1h['buy_ratio'] = df_1h['taker_buy_volume'] / df_1h['volume'].replace(0, 1)
    df_1h['avg_trade_size'] = df_1h['volume'] / df_1h['count'].replace(0, 1)
    df_1h['price_change_1h'] = close_series.pct_change(1)
    df_1h['price_change_4h'] = close_series.pct_change(4)
    df_1h['price_change_24h'] = close_series.pct_change(24)
    df_1h = df_1h.dropna()

    last_closed_1h = df_1h.iloc[-1] # Берем последнюю закрытую свечу
    price = float(last_closed_1h['close'])
    rsi = float(last_closed_1h['rsi'])
    macd_hist = float(last_closed_1h['macd_hist'])
    ema = float(last_closed_1h['ema_50'])
    vol_sma = float(last_closed_1h['vol_sma'])
    cur_vol = float(last_closed_1h['volume'])

    # --- ОЦЕНКА LONG ---
    score_long = 0.0
    active_long = []
    prob_long = 0.0

    if source_long == "model_base":
        if macd_hist > 0: score_long += bot_info["weights"]["macd"]; active_long.append("macd")
        if price > ema: score_long += bot_info["weights"]["ema"]; active_long.append("ema")
        if cur_vol > vol_sma: score_long += bot_info["weights"]["volume"]; active_long.append("volume")
        if rsi < 30: score_long += bot_info["weights"]["rsi_low"]; active_long.append("rsi_low")
        elif rsi < 70: score_long += bot_info["weights"]["rsi_mid"]; active_long.append("rsi_mid")
        signal_long = score_long >= bot_info["threshold"]
    else:
        # ML логика
        try:
            if source_long == "model_xgb":
                model_b64 = models["long"]["params"]["model_b64"]
                feature_cols = models["long"]["params"]["features"]
                raw_model = base64.b64decode(model_b64)
                booster = xgb.Booster()
                booster.load_model(bytearray(raw_model))
                # xgb Booster требует DMatrix для предсказания, но проще через sklearn API, если сохраняли его
                # Для простоты перезагрузим как sklearn (если упаковали через get_booster().save_raw(), то используем Booster)
                X_ml = get_features_for_ml(df_1h, feature_cols)
                dmat = xgb.DMatrix(X_ml)
                prob_long = booster.predict(dmat)[0]
            elif source_long == "model_rf":
                model_b64 = models["long"]["params"]["model_b64"]
                feature_cols = models["long"]["params"]["features"]
                raw_model = base64.b64decode(model_b64)
                model = pickle.loads(raw_model)
                X_ml = get_features_for_ml(df_1h, feature_cols)
                prob_long = model.predict_proba(X_ml)[0][1] # вероятность класса 1 (лонг)
        except Exception as e:
            log.error(f"ML Prediction failed: {e}")
            prob_long = 0.0
            
        signal_long = prob_long > 0.65 # Порог уверенности для лонга

    # --- ОЦЕНКА SHORT ---
    score_short = 0.0
    active_short = []
    prob_short = 0.0

    if source_short == "model_base":
        if macd_hist < 0: score_short += bot_info["weights"]["macd"]; active_short.append("macd")
        if price < ema: score_short += bot_info["weights"]["ema"]; active_short.append("ema")
        if cur_vol > vol_sma: score_short += bot_info["weights"]["volume"]; active_short.append("volume")
        if rsi > 70: score_short += bot_info["weights"]["rsi_high"]; active_short.append("rsi_high")
        elif rsi > 30: score_short += bot_info["weights"]["rsi_mid"]; active_short.append("rsi_mid")
        signal_short = score_short >= bot_info["threshold"]
    else:
        # ML логика
        try:
            if source_short == "model_xgb":
                model_b64 = models["short"]["params"]["model_b64"]
                feature_cols = models["short"]["params"]["features"]
                raw_model = base64.b64decode(model_b64)
                booster = xgb.Booster()
                booster.load_model(bytearray(raw_model))
                X_ml = get_features_for_ml(df_1h, feature_cols)
                dmat = xgb.DMatrix(X_ml)
                # Для шорта мы хотим, чтобы вероятность лонга была низкой
                prob_short = 1.0 - booster.predict(dmat)[0] 
            elif source_short == "model_rf":
                model_b64 = models["short"]["params"]["model_b64"]
                feature_cols = models["short"]["params"]["features"]
                raw_model = base64.b64decode(model_b64)
                model = pickle.loads(raw_model)
                X_ml = get_features_for_ml(df_1h, feature_cols)
                prob_short = model.predict_proba(X_ml)[0][0] # вероятность класса 0 (шорт)
        except Exception as e:
            log.error(f"ML Prediction failed: {e}")
            prob_short = 0.0
            
        signal_short = prob_short > 0.65

    log.info(f"Price: {price:.1f} | RSI: {rsi:.1f} | LONG src: {source_long} | SHORT src: {source_short}")
    if source_long == "model_base": log.info(f"LONG Score: {score_long:.2f} | Threshold: {bot_info['threshold']:.2f}")
    else: log.info(f"LONG ML Prob: {prob_long:.3f}")
    if source_short == "model_base": log.info(f"SHORT Score: {score_short:.2f}")
    else: log.info(f"SHORT ML Prob: {prob_short:.3f}")

    # Открытие позиции
    if signal_long and not signal_short:
        log.info("🚀 LONG SIGNAL!")
        tp = price * (1 + models["long"]["params"].get("tp_pct", 5.0) / 100)
        sl = price * (1 - models["long"]["params"].get("sl_pct", 2.0) / 100)
        deal = {"type": "LONG", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_long if source_long == "model_base" else []}
        save_json(OPENED_DEALS, deal)
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "LONG", "entry": price, "tp": tp, "sl": sl, "sent": False})
    elif signal_short and not signal_long:
        log.info("🩸 SHORT SIGNAL!")
        tp = price * (1 - models["short"]["params"].get("tp_pct", 5.0) / 100)
        sl = price * (1 + models["short"]["params"].get("sl_pct", 2.0) / 100)
        deal = {"type": "SHORT", "entry": price, "tp": tp, "sl": sl, "open_time": datetime.now(timezone.utc).isoformat(), "active_signals": active_short if source_short == "model_base" else []}
        save_json(OPENED_DEALS, deal)
        save_json(INFO_DEAL, {"type": "ENTRY", "deal_type": "SHORT", "entry": price, "tp": tp, "sl": sl, "sent": False})
    else:
        log.info("No signal. Waiting.")

if __name__ == "__main__":
    main()