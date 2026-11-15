# ============================================================
# ARGUS - XGBOOST MODEL v7.3 (DYNAMIC ATR & MIN PROFIT)
# Path: crypto/learn/model_xgb.py
# ============================================================

import logging, numpy as np, pandas as pd, xgboost as xgb, base64
from sklearn.model_selection import TimeSeriesSplit

log = logging.getLogger("model_xgb")
INITIAL_BALANCE, POSITION_NOTIONAL, FEE_RATE = 100.0, 20.0, 0.001

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
            
    macro_features = []
    
    if 'nasdaq' in data.columns:
        data['nasdaq_pct_1h'] = data['nasdaq'].pct_change(1)
        data['nasdaq_pct_24h'] = data['nasdaq'].pct_change(24)
        macro_features.extend(['nasdaq_pct_1h', 'nasdaq_pct_24h'])
        
    if 'dxy' in data.columns:
        data['dxy_pct_1h'] = data['dxy'].pct_change(1)
        data['dxy_pct_24h'] = data['dxy'].pct_change(24)
        macro_features.extend(['dxy_pct_1h', 'dxy_pct_24h'])
        
    if 'fear_greed' in data.columns:
        data['fear_greed_norm'] = data['fear_greed'] / 100.0
        macro_features.append('fear_greed_norm')
        
    if 'btc_dominance' in data.columns:
        data['btc_dom_pct'] = data['btc_dominance'].pct_change(24)
        macro_features.append('btc_dom_pct')
        
    if 'stablecoin_supply' in data.columns:
        data['stable_flow_1h'] = data['stablecoin_supply'].pct_change(1)
        data['stable_flow_24h'] = data['stablecoin_supply'].pct_change(24)
        macro_features.extend(['stable_flow_1h', 'stable_flow_24h'])

    future_return = (data['close'].shift(-horizon) - data['close']) / data['close'] * 100
    data['target'] = future_return.clip(-20, 20)
    
    feature_cols = base_features + macro_features + \
                   [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']]
    
    data = data.dropna(subset=['target'])
    return data, feature_cols

def walk_forward_validate(data, feature_cols, side, horizon):
    n_splits = 5
    tscv = TimeSeriesSplit(n_splits=n_splits)
    X = data[feature_cols].values
    y = data['target'].values
    predictions = np.full(len(data), np.nan)
    
    for train_idx, test_idx in tscv.split(X):
        if len(np.unique(y[train_idx])) < 2: continue
        model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42)
        model.fit(X[train_idx], y[train_idx])
        predictions[test_idx] = model.predict(X[test_idx])
    
    valid_preds = predictions[~np.isnan(predictions)]
    if len(valid_preds) == 0: return None, None
    
    # Берем Топ-20% лучших сигналов (отсекаем слабые)
    if side == "long":
        threshold = np.percentile(valid_preds, 80)
        signal_condition = lambda p: p >= threshold
    else:
        threshold = np.percentile(valid_preds, 20)
        signal_condition = lambda p: p <= threshold

    trades, in_pos, entry_price, entry_idx = [], False, 0, 0
    dynamic_tp, dynamic_sl = 0, 0

    for i in range(len(data)):
        prob = predictions[i]
        if pd.isna(prob): continue
        
        if in_pos:
            row = data.iloc[i]
            if side == "long":
                if row['low'] <= dynamic_sl: trades.append(POSITION_NOTIONAL * ((dynamic_sl - entry_price) / entry_price - (FEE_RATE * 2))); in_pos = False
                elif row['high'] >= dynamic_tp: trades.append(POSITION_NOTIONAL * ((dynamic_tp - entry_price) / entry_price - (FEE_RATE * 2))); in_pos = False
            else:
                if row['high'] >= dynamic_sl: trades.append(POSITION_NOTIONAL * ((entry_price - dynamic_sl) / entry_price - (FEE_RATE * 2))); in_pos = False
                elif row['low'] <= dynamic_tp: trades.append(POSITION_NOTIONAL * ((entry_price - dynamic_tp) / entry_price - (FEE_RATE * 2))); in_pos = False
                
            if in_pos and (i - entry_idx >= horizon):
                exit_price = row['close']; pnl_pct = ((exit_price - entry_price) / entry_price) if side == "long" else ((entry_price - exit_price) / entry_price)
                trades.append(POSITION_NOTIONAL * (pnl_pct - (FEE_RATE * 2))); in_pos = False
        else:
            # ФИЛЬТР: Не входим, если предсказание меньше 1.5% (комиссия съест)
            if abs(prob) < 1.5: continue 
            
            if signal_condition(prob):
                in_pos = True
                entry_price = data.iloc[i]['close']
                entry_idx = i
                atr = data.iloc[i]['atr_14']
                
                # ДИНАМИЧЕСКИЙ ТЕЙК И СТОП НА ОСНОВЕ ATR
                tp_pct = min(abs(prob), 10.0) / 100
                sl_pct = min((atr * 1.5) / entry_price, 0.03)
                
                if side == "long":
                    dynamic_tp = entry_price * (1 + tp_pct)
                    dynamic_sl = entry_price * (1 - sl_pct) 
                else:
                    dynamic_tp = entry_price * (1 - tp_pct)
                    dynamic_sl = entry_price * (1 + sl_pct)
                
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
    
    final_model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42)
    final_model.fit(X, y)
    return metrics, final_model

def train(df, side="long", mode="swing"):
    try:
        horizon = 12 if mode == "intraday" else 48
        data, feature_cols = prepare_features(df, horizon, side)
        if len(data) < 1000: return None
        metrics, model = walk_forward_validate(data, feature_cols, side, horizon)
        if not metrics or model is None: return None
        
        model_b64 = base64.b64encode(model.get_booster().save_raw()).decode('utf-8')
        return {
            "params": {"model_type": "xgboost_reg", "horizon": horizon, "features": feature_cols, "model_b64": model_b64},
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"XGBoost training failed: {e}")
        return None