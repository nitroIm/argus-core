# ============================================================
# ARGUS - RANDOM FOREST MODEL v3.0 (Pack to Base64)
# Path: crypto/learn/model_rf.py
# ============================================================

import logging
import numpy as np
import pandas as pd
import pickle
import base64
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import TimeSeriesSplit

log = logging.getLogger("model_rf")

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001
HORIZON = 24

def prepare_features(df):
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
    data['buy_ratio'] = data['buy_ratio']
    data['avg_trade_norm'] = data['avg_trade_size'] / data['volume'].rolling(20).mean()
    data['pc1'] = data['price_change_1h']
    data['pc4'] = data['price_change_4h']
    data['pc24'] = data['price_change_24h']
    
    base_features = ['rsi_norm', 'macd_norm', 'ema50_diff', 'ema20_diff', 'ema200_diff',
                     'vol_ratio', 'atr14_pct', 'atr50_pct', 'buy_ratio', 'avg_trade_norm',
                     'pc1', 'pc4', 'pc24']
    
    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']:
            data[f'{f}_lag{lag}'] = data[f].shift(lag)
    
    data['target'] = (data['close'].shift(-HORIZON) > data['close']).astype(int)
    
    feature_cols = base_features + \
                   [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']]
    
    data = data.dropna()
    return data, feature_cols

def walk_forward_validate(data, feature_cols, side):
    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)
    X = data[feature_cols].values
    y = data['target'].values
    all_predictions = []
    all_indices = []
    
    for train_idx, test_idx in tscv.split(X):
        if len(np.unique(y[train_idx])) < 2: continue
        model = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_split=5, 
                                       min_samples_leaf=2, random_state=42, n_jobs=-1)
        model.fit(X[train_idx], y[train_idx])
        all_predictions.extend(model.predict(X[test_idx]))
        all_indices.extend(test_idx)
    
    if not all_predictions: return None, None
    
    trades = []
    for i, idx in enumerate(all_indices):
        row = data.iloc[idx]
        pred = all_predictions[i]
        if side == "long" and pred == 1:
            future = data.iloc[idx+1:idx+HORIZON+1]['close']
            if len(future) < HORIZON: continue
            pnl_pct = (future.iloc[-1] - row['close']) / row['close'] * 100 - (FEE_RATE * 200)
            trades.append(POSITION_NOTIONAL * (pnl_pct / 100))
        elif side == "short" and pred == 0:
            future = data.iloc[idx+1:idx+HORIZON+1]['close']
            if len(future) < HORIZON: continue
            pnl_pct = (row['close'] - future.iloc[-1]) / row['close'] * 100 - (FEE_RATE * 200)
            trades.append(POSITION_NOTIONAL * (pnl_pct / 100))
    
    if not trades: return None, None
    trades = np.array(trades)
    total_pnl = trades.sum()
    winrate = (trades > 0).mean()
    cum = np.cumsum(trades) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100
    max_dd = dd.min() if len(dd) else 0
    if max_dd == 0: max_dd = -0.1
    score = total_pnl / abs(max_dd)
    
    metrics = {"trades": len(trades), "pnl": round(float(total_pnl), 2), "winrate": round(float(winrate), 4),
               "max_dd": round(float(max_dd), 2), "score": round(float(score), 2)}
    
    # Обучаем финальную модель на всех данных
    final_model = RandomForestClassifier(n_estimators=200, max_depth=8, min_samples_split=5, 
                                         min_samples_leaf=2, random_state=42, n_jobs=-1)
    final_model.fit(X, y)
    
    return metrics, final_model

def train(df, side="long"):
    try:
        data, feature_cols = prepare_features(df)
        if len(data) < 2000:
            return None
        
        metrics, model = walk_forward_validate(data, feature_cols, side)
        if not metrics or model is None:
            return None
        
        # Упаковка модели в Base64 строку
        raw_model = pickle.dumps(model)
        model_b64 = base64.b64encode(raw_model).decode('utf-8')
        
        return {
            "params": {
                "model_type": "random_forest",
                "features": feature_cols,
                "model_b64": model_b64
            },
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"Random Forest training failed: {e}")
        return None