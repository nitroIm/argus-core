# ============================================================
# ARGUS - BASE MODEL v5.0 (DYNAMIC THRESHOLD)
# Path: crypto/learn/model_base.py
# ============================================================
import logging, numpy as np, pandas as pd
from sklearn.model_selection import TimeSeriesSplit

log = logging.getLogger("model_base")
INITIAL_BALANCE, POSITION_NOTIONAL, FEE_RATE = 100.0, 20.0, 0.001

def prepare_features(df, horizon, side):
    data = df.copy()
    future_close = data['close'].shift(-horizon)
    if side == "long": data['target'] = ((future_close - data['close']) > (data['atr_14'] * 0.5)).astype(int)
    else: data['target'] = ((data['close'] - future_close) > (data['atr_14'] * 0.5)).astype(int)
    return data.dropna()

def walk_forward_validate(data, side, horizon):
    # Базовая модель просто считает сумму индикаторов
    scores = np.zeros(len(data))
    
    macd = data['macd_hist'].values
    close = data['close'].values
    ema50 = data['ema_50'].values
    rsi = data['rsi'].values
    
    for i in range(len(data)):
        s = 0
        if side == "long":
            if macd[i] > 0: s += 1
            if close[i] > ema50[i]: s += 1
            if rsi[i] < 30: s += 1
            elif rsi[i] < 50: s += 0.5
        else:
            if macd[i] < 0: s += 1
            if close[i] < ema50[i]: s += 1
            if rsi[i] > 70: s += 1
            elif rsi[i] > 50: s += 0.5
        scores[i] = s

    # Имитируем вероятности (нормализуем от 0 до 1)
    max_possible_score = 3.0
    predictions = scores / max_possible_score
    
    # Динамический порог для базовой модели (Топ-10% лучших скоров)
    valid_preds = predictions
    if len(valid_preds) == 0: return None, None
    
    threshold = np.percentile(valid_preds, 90) if side == "long" else np.percentile(valid_preds, 10)
    signal_condition = (lambda p: p >= threshold) if side == "long" else (lambda p: p <= threshold)

    trades, in_pos, entry_price, entry_idx = [], False, 0, 0
    for i in range(len(data)):
        prob = predictions[i]
        if in_pos:
            if i - entry_idx >= horizon:
                exit_price = data.iloc[i]['close']
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if side == "long" else ((entry_price - exit_price) / entry_price * 100)
                trades.append(POSITION_NOTIONAL * ((pnl_pct - (FEE_RATE * 200)) / 100)); in_pos = False
        elif signal_condition(prob):
            in_pos, entry_price, entry_idx = True, data.iloc[i]['close'], i
                
    if not trades: return None, None
    trades = np.array(trades); total_pnl = trades.sum(); winrate = (trades > 0).mean(); cum = np.cumsum(trades) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum); max_dd = ((cum - peak) / peak * 100).min()
    if max_dd == 0: max_dd = -0.1
    
    metrics = {"trades": len(trades), "pnl": round(float(total_pnl), 2), "winrate": round(float(winrate), 4), "max_dd": round(float(max_dd), 2), "score": round(float(total_pnl / abs(max_dd)), 2)}
    return metrics, True # Возвращаем True вместо ML-модели

def train(df, side="long", mode="swing"):
    try:
        horizon = 4 if mode == "intraday" else 24
        data = prepare_features(df, horizon, side)
        if len(data) < 2000: return None
        metrics, _ = walk_forward_validate(data, side, horizon)
        if not metrics: return None
        
        return {
            "params": {"model_type": "model_base", "horizon": horizon, "features": [], "model_b64": ""},
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"Base model training failed: {e}"); return None