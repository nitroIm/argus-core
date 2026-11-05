# ============================================================
# ARGUS - RANDOM FOREST MODEL (Walk-Forward)
# Path: crypto/learn/model_rf.py
# ============================================================

import logging
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import TimeSeriesSplit

log = logging.getLogger("model_rf")

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001
HORIZON = 24

def prepare_features(df):
    """Аналогичная подготовка признаков, что и в XGBoost."""
    data = df.copy()
    
    data['rsi'] = data['rsi'] / 100.0
    data['macd_norm'] = data['macd_hist'] / data['close']
    data['ema_diff'] = (data['close'] - data['ema_50']) / data['ema_50']
    data['vol_ratio'] = data['volume'] / data['vol_sma']
    data['atr_pct'] = data['atr'] / data['close']
    
    for lag in [1, 2, 3, 6, 12]:
        data[f'rsi_lag{lag}'] = data['rsi'].shift(lag)
        data[f'ema_diff_lag{lag}'] = data['ema_diff'].shift(lag)
        data[f'vol_ratio_lag{lag}'] = data['vol_ratio'].shift(lag)
    
    data['target'] = (data['close'].shift(-HORIZON) > data['close']).astype(int)
    
    feature_cols = ['rsi', 'macd_norm', 'ema_diff', 'vol_ratio', 'atr_pct'] + \
                   [f'rsi_lag{l}' for l in [1,2,3,6,12]] + \
                   [f'ema_diff_lag{l}' for l in [1,2,3,6,12]] + \
                   [f'vol_ratio_lag{l}' for l in [1,2,3,6,12]]
    
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
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y[train_idx], y[test_idx]
        
        if len(np.unique(y_train)) < 2:
            continue
        
        model = RandomForestClassifier(
            n_estimators=100,
            max_depth=6,
            min_samples_split=5,
            min_samples_leaf=2,
            random_state=42,
            n_jobs=-1
        )
        model.fit(X_train, y_train)
        
        preds = model.predict(X_test)
        all_predictions.extend(preds)
        all_indices.extend(test_idx)
    
    if not all_predictions:
        return None, None
    
    actual = y[all_indices]
    acc = accuracy_score(actual, all_predictions)
    
    trades = []
    for i, idx in enumerate(all_indices):
        row = data.iloc[idx]
        pred = all_predictions[i]
        
        if side == "long" and pred == 1:
            entry = row['close']
            future_prices = data.iloc[idx+1:idx+HORIZON+1]['close']
            if len(future_prices) < HORIZON:
                continue
            exit_price = future_prices.iloc[-1]
            pnl_pct = (exit_price - entry) / entry * 100 - (FEE_RATE * 200)
            trades.append(POSITION_NOTIONAL * (pnl_pct / 100))
        elif side == "short" and pred == 0:
            entry = row['close']
            future_prices = data.iloc[idx+1:idx+HORIZON+1]['close']
            if len(future_prices) < HORIZON:
                continue
            exit_price = future_prices.iloc[-1]
            pnl_pct = (entry - exit_price) / entry * 100 - (FEE_RATE * 200)
            trades.append(POSITION_NOTIONAL * (pnl_pct / 100))
    
    if not trades:
        return None, None
    
    trades = np.array(trades)
    total_pnl = trades.sum()
    winrate = (trades > 0).mean()
    
    cum = np.cumsum(trades) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100
    max_dd = dd.min() if len(dd) else 0
    if max_dd == 0: max_dd = -0.1
    
    score = total_pnl / abs(max_dd)
    
    metrics = {
        "trades": len(trades),
        "pnl": round(total_pnl, 2),
        "winrate": round(winrate, 4),
        "max_dd": round(max_dd, 2),
        "score": round(score, 2),
        "accuracy": round(acc, 4)
    }
    
    return metrics, model

def train(df, side="long"):
    try:
        data, feature_cols = prepare_features(df)
        if len(data) < 500:
            log.warning(f"Not enough data for RF ({len(data)} rows)")
            return None
        
        metrics, model = walk_forward_validate(data, feature_cols, side)
        if not metrics:
            return None
        
        return {
            "params": {
                "model_type": "random_forest",
                "horizon": HORIZON,
                "features": feature_cols,
                "n_features": len(feature_cols)
            },
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"Random Forest training failed: {e}")
        return None