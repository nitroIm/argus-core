# ============================================================
# ARGUS - XGBOOST MODEL v5.1 (TP/SL TARGET)
# Path: crypto/learn/model_xgb.py
# ============================================================

import logging
import numpy as np
import pandas as pd
import xgboost as xgb
import base64
from sklearn.model_selection import TimeSeriesSplit

log = logging.getLogger("model_xgb")

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001
TP_PCT = 2.0
SL_PCT = 1.0

def prepare_features(df, horizon, side):
    data = df.copy()
    
    data['rsi_norm'] = data['rsi'] / 100.0
    data['rsi_7_norm'] = data['rsi_7'] / 100.0
    data['rsi_21_norm'] = data['rsi_21'] / 100.0
    data['macd_norm'] = data['macd_hist'] / data['close']
    data['ema50_diff'] = (data['close'] - data['ema_50']) / data['ema_50']
    data['ema20_diff'] = (data['close'] - data['ema_20']) / data['ema_20']
    data['ema200_diff'] = (data['close'] - data['ema_200']) / data['ema_200']
    data['vol_ratio'] = data['volume'] / data['vol_sma']
    data['atr7_pct'] = data['atr_7'] / data['close']
    data['atr14_pct'] = data['atr_14'] / data['close']
    data['atr50_pct'] = data['atr_50'] / data['close']
    data['atr100_pct'] = data['atr_100'] / data['close']
    data['avg_trade_norm'] = data['avg_trade_size'] / data['volume'].rolling(20).mean()
    data['pc1'] = data['price_change_1h']
    data['pc4'] = data['price_change_4h']
    data['pc24'] = data['price_change_24h']
    
    base_features = ['rsi_norm', 'macd_norm', 'ema50_diff', 'ema20_diff', 'ema200_diff',
                     'vol_ratio', 'atr14_pct', 'atr50_pct', 'avg_trade_norm',
                     'pc1', 'pc4', 'pc24', 'buy_ratio']
    
    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']:
            data[f'{f}_lag{lag}'] = data[f].shift(lag)
    
    # УМНАЯ ЦЕЛЬ: Кто первый? Тейк 2% или Стоп 1%?
    target = np.zeros(len(data))
    for i in range(len(data) - horizon):
        entry_price = data.iloc[i]['close']
        if side == "long":
            tp_price = entry_price * (1 + TP_PCT / 100)
            sl_price = entry_price * (1 - SL_PCT / 100)
            # Смотрим вперед на horizon свечей
            future_data = data.iloc[i+1 : i+1+horizon]
            hit_tp = (future_data['high'] >= tp_price).values
            hit_sl = (future_data['low'] <= sl_price).values
        else:
            tp_price = entry_price * (1 - TP_PCT / 100)
            sl_price = entry_price * (1 + SL_PCT / 100)
            future_data = data.iloc[i+1 : i+1+horizon]
            hit_tp = (future_data['low'] <= tp_price).values
            hit_sl = (future_data['high'] >= sl_price).values

        # Ищем, что случилось раньше
        tp_idx = np.argmax(hit_tp) if np.any(hit_tp) else 999
        sl_idx = np.argmax(hit_sl) if np.any(hit_sl) else 999

        if tp_idx < sl_idx:
            target[i] = 1 # Успех
        elif sl_idx < tp_idx:
            target[i] = 0 # Провал
        else:
            target[i] = np.nan # Ничего не произошло (тайм-аут)

    data['target'] = target
    
    feature_cols = base_features + \
                   [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']]
    
    data = data.dropna()
    return data, feature_cols

def walk_forward_validate(data, feature_cols, side, horizon):
    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)
    X = data[feature_cols].values
    y = data['target'].values
    predictions = np.full(len(data), np.nan)
    
    for train_idx, test_idx in tscv.split(X):
        if len(np.unique(y[train_idx])) < 2: continue
        model = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, eval_metric='logloss', random_state=42)
        model.fit(X[train_idx], y[train_idx])
        predictions[test_idx] = model.predict_proba(X[test_idx])[:, 1]
    
    trades, in_pos, entry_price, entry_idx = [], False, 0, 0
    
    for i in range(len(data)):
        prob = predictions[i]
        if pd.isna(prob): continue
        
        if in_pos:
            if i - entry_idx >= horizon:
                exit_price = data.iloc[i]['close']
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if side == "long" else ((entry_price - exit_price) / entry_price * 100)
                trades.append(POSITION_NOTIONAL * ((pnl_pct - (FEE_RATE * 200)) / 100))
                in_pos = False
        else:
            # ЖЕСТКИЙ ПОРОГ УВЕРЕННОСТИ (65%)
            if side == "long" and prob > 0.65:
                in_pos = True
                entry_price = data.iloc[i]['close']
                entry_idx = i
            elif side == "short" and prob < 0.35:
                in_pos = True
                entry_price = data.iloc[i]['close']
                entry_idx = i
                
    if not trades: return None, None
    trades = np.array(trades)
    total_pnl = trades.sum()
    winrate = (trades > 0).mean()
    cum = np.cumsum(trades) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    max_dd = ((cum - peak) / peak * 100).min()
    if max_dd == 0: max_dd = -0.1
    score = total_pnl / abs(max_dd)
    
    metrics = {"trades": len(trades), "pnl": round(float(total_pnl), 2), "winrate": round(float(winrate), 4), "max_dd": round(float(max_dd), 2), "score": round(float(score), 2)}
    
    final_model = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, eval_metric='logloss', random_state=42)
    final_model.fit(X, y)
    return metrics, final_model

def train(df, side="long", mode="swing"):
    try:
        horizon = 12 if mode == "intraday" else 48 # Даем сделке больше времени
        data, feature_cols = prepare_features(df, horizon, side)
        if len(data) < 2000: return None
        metrics, model = walk_forward_validate(data, feature_cols, side, horizon)
        if not metrics or model is None: return None
        
        model_b64 = base64.b64encode(model.get_booster().save_raw()).decode('utf-8')
        return {
            "params": {"model_type": "xgboost", "horizon": horizon, "features": feature_cols, "model_b64": model_b64},
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"XGBoost training failed: {e}")
        return None