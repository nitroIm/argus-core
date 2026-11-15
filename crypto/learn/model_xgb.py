# ============================================================
# ARGUS - XGBOOST MODEL v7.5.4
# Path: crypto/learn/model_xgb.py
#
# ИЗМЕНЕНИЯ v7.5.4:
#   - Адаптивный порог входа (тот же, что в signal_bot).
#   - Бэктест полностью повторяет live-логику.
# ============================================================

import logging, numpy as np, pandas as pd, xgboost as xgb
from pathlib import Path

log = logging.getLogger("model_xgb")

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
    data['pc1'] = data['price_change_1h']; data['pc4'] = data['price_change_4h']; data['pc24'] = data['price_change_24h']
    base = ['rsi_norm','macd_norm','ema50_diff','ema20_diff','ema200_diff','vol_ratio','atr14_pct','atr50_pct','avg_trade_norm','pc1','pc4','pc24','buy_ratio']
    for lag in [1,3,6,12,24]:
        for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']:
            data[f'{f}_lag{lag}'] = data[f].shift(lag)
    macro = []
    if 'nasdaq' in data.columns:
        data['nasdaq_pct_1h'] = data['nasdaq'].pct_change(1); data['nasdaq_pct_24h'] = data['nasdaq'].pct_change(24); macro += ['nasdaq_pct_1h','nasdaq_pct_24h']
    if 'dxy' in data.columns:
        data['dxy_pct_1h'] = data['dxy'].pct_change(1); data['dxy_pct_24h'] = data['dxy'].pct_change(24); macro += ['dxy_pct_1h','dxy_pct_24h']
    if 'fear_greed' in data.columns:
        data['fear_greed_norm'] = data['fear_greed']/100.0; macro.append('fear_greed_norm')
    if 'btc_dominance' in data.columns:
        data['btc_dom_pct'] = data['btc_dominance'].pct_change(24); macro.append('btc_dom_pct')
    if 'stablecoin_supply' in data.columns:
        data['stable_flow_1h'] = data['stablecoin_supply'].pct_change(1); data['stable_flow_24h'] = data['stablecoin_supply'].pct_change(24); macro += ['stable_flow_1h','stable_flow_24h']
    data['target'] = ((data['close'].shift(-horizon) - data['close']) / data['close'] * 100).clip(-20, 20)
    cols = base + macro + [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']]
    data = data.dropna(subset=['target'])
    return data, cols

def _adaptive_thresh(atr_pct_series, base_thresh, adap):
    if not adap.get("enabled", False) or len(atr_pct_series) < 50: return base_thresh
    lookback = int(adap.get("atr_lookback", 200))
    series = atr_pct_series.tail(lookback)
    curr = series.iloc[-1]
    if pd.isna(curr): return base_thresh
    pct_low = series.quantile(0.25); pct_high = series.quantile(0.75)
    if curr <= pct_low: factor = float(adap.get("low_vol_multiplier", 0.75))
    elif curr >= pct_high: factor = float(adap.get("high_vol_multiplier", 1.3))
    else: factor = 1.0
    adjusted = base_thresh * factor
    return max(float(adap.get("min_threshold", 3.0)), min(float(adap.get("max_threshold", 6.5)), adjusted))

def walk_forward_validate(data, feature_cols, side, horizon, cfg):
    rm = cfg.get("risk_management", {})
    adap = cfg.get("adaptive_threshold", {})
    bt = cfg.get("backtest", {})
    mode_cfg = cfg.get("intraday" if horizon <= 24 else "swing", {})
    base_thresh = float(mode_cfg.get("threshold", 4.6))
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    pct_top = float(bt.get("percentile_top", 80.0))
    split_pct = float(bt.get("test_split_pct", 0.2))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))

    split_idx = int(len(data) * (1 - split_pct)) - horizon
    if split_idx <= 100: return {"trades":0,"score":-99999.0}, None
    train, test = data.iloc[:split_idx], data.iloc[split_idx:].copy()
    X_tr, y_tr = train[feature_cols].values, train['target'].values
    X_te = test[feature_cols].values
    model = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0)
    model.fit(X_tr, y_tr)
    preds = model.predict(X_te)
    if len(preds) == 0: return {"trades":0,"score":-99999.0}, None
    if side == "long": th_pct = np.percentile(preds, pct_top); cond = lambda p: p >= th_pct
    else: th_pct = np.percentile(preds, 100 - pct_top); cond = lambda p: p <= th_pct

    atr_hist = test['atr14_pct'].values if 'atr14_pct' in test.columns else np.array([])
    trades = []; in_pos = False
    entry=0.0; sl=0.0; tp1=0.0; tp2=0.0; size_left=0; tp1_hit=False; ei=0; max_p=0.0; min_p=0.0
    for i in range(len(test)):
        row = test.iloc[i]; prob = preds[i]
        c_high, c_low = row['high'], row['low']
        c_atr = row['atr_14'] if pd.notna(row['atr_14']) else 0.0
        if in_pos:
            if side == "long": max_p = max(max_p, c_high); min_p = min(min_p, c_low)
            else: min_p = min(min_p, c_low); max_p = max(max_p, c_high)
            if (side == "long" and c_low <= sl) or (side == "short" and c_high >= sl):
                pnl = ((sl-entry)/entry if side=="long" else (entry-sl)/entry) - fee*2
                trades.append(notional * (size_left/100) * pnl)
                in_pos = False
            elif (side == "long" and c_high >= tp2) or (side == "short" and c_low <= tp2):
                pnl = ((tp2-entry)/entry if side=="long" else (entry-tp2)/entry) - fee*2
                trades.append(notional * (size_left/100) * pnl)
                in_pos = False
            elif not tp1_hit and ((side == "long" and c_high >= tp1) or (side == "short" and c_low <= tp1)):
                tp1_hit = True; close_pct = rm.get("partial_exit_pct", 50)
                pnl = ((tp1-entry)/entry if side=="long" else (entry-tp1)/entry) - fee*2
                trades.append(notional * (close_pct/100) * pnl)
                size_left -= close_pct
                sl = entry * (1 + fee*2) if side == "long" else entry * (1 - fee*2)
            if in_pos and tp1_hit and c_atr > 0:
                if side == "long":
                    new_sl = max_p - (c_atr * rm.get("trail_atr_mult", 2.0))
                    if new_sl > sl: sl = new_sl
                else:
                    new_sl = min_p + (c_atr * rm.get("trail_atr_mult", 2.0))
                    if new_sl < sl: sl = new_sl
            if in_pos and (i - ei >= horizon * 2):
                ex = row['close']; pnl = ((ex-entry)/entry if side=="long" else (entry-ex)/entry) - fee*2
                trades.append(notional * (size_left/100) * pnl)
                in_pos = False
        else:
            if abs(prob) < filt or not cond(prob): continue
            # адаптивный порог: сравниваем предсказание с динамическим порогом (score = pred*10)
            score = prob * 10 if side == "long" else -prob * 10
            if atr_hist.size > 0:
                a_series = pd.Series(atr_hist[max(0, i-int(adap.get("atr_lookback", 200))+1):i+1])
                dyn_thresh = _adaptive_thresh(a_series, base_thresh, adap)
            else:
                dyn_thresh = base_thresh
            if score < dyn_thresh: continue
            in_pos = True; entry = row['close']; ei = i; max_p = entry; min_p = entry
            sl_pct = max(rm.get("min_sl_pct",0.5), min(rm.get("max_sl_pct",3.0), c_atr * rm.get("atr_mult_sl",1.5) / entry * 100))
            tp1_pct = max(rm.get("min_tp1_pct",0.8), min(rm.get("max_tp1_pct",2.0), c_atr * rm.get("atr_mult_tp1",1.0) / entry * 100))
            tp2_pct = max(rm.get("min_tp2_pct",2.0), min(rm.get("max_tp2_pct",8.0), c_atr * rm.get("atr_mult_tp2",2.5) / entry * 100))
            if side == "long": sl = entry*(1-sl_pct/100); tp1 = entry*(1+tp1_pct/100); tp2 = entry*(1+tp2_pct/100)
            else: sl = entry*(1+sl_pct/100); tp1 = entry*(1-tp1_pct/100); tp2 = entry*(1-tp2_pct/100)
            tp1_hit = False; size_left = 100

    if not trades: return {"trades":0,"score":-99999.0}, None
    tr = np.array(trades); pnl = tr.sum(); wr = float((tr>0).mean())
    cum = np.cumsum(tr) + init_bal; peak = np.maximum.accumulate(cum)
    dd = float(((cum-peak)/peak*100).min())
    if dd == 0: dd = -0.1
    metrics = {"trades": int(len(tr)), "pnl": round(float(pnl),2), "winrate": round(wr,4), "max_dd": round(dd,2), "score": round(float(pnl/abs(dd)),2)}
    final = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0)
    final.fit(data[feature_cols].values, data['target'].values)
    return metrics, final

def train(df, side="long", mode="swing", models_dir=None, cfg=None):
    try:
        if cfg is None: cfg = {}
        if models_dir is None: models_dir = Path("crypto/data/models")
        models_dir = Path(models_dir)
        horizon = 12 if mode == "intraday" else 48
        data, cols = prepare_features(df, horizon, side)
        if len(data) < 1000: return None
        metrics, model = walk_forward_validate(data, cols, side, horizon, cfg)
        if model is None: return None
        fname = f"model_xgb_{mode}_{side}.xgb"
        model.get_booster().save_model(str(models_dir / fname))
        return {"params": {"model_type":"xgboost_reg","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":fname}, "metrics": metrics}
    except Exception as e:
        log.error(f"XGBoost training failed: {e}"); return None