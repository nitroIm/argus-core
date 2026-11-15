# ============================================================
# ARGUS - RANDOM FOREST MODEL v5.7.1
# Path: crypto/learn/model_rf.py
# ============================================================

import logging, numpy as np, pandas as pd, pickle
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from pathlib import Path

log = logging.getLogger("model_rf")

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
    
    valid_cols = [c for c in cols if c in data.columns and not data[c].isna().all()]
    return data, valid_cols

class RFWrapper:
    def __init__(self, model, imputer):
        self.model = model
        self.imputer = imputer
    def predict(self, X):
        X = self.imputer.transform(X)
        return self.model.predict(X)

def _backtest(data, preds, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    tp_max = float(bt.get("tp_max_pct", 10.0)); sl_max = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5)); pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))

    if len(preds) == 0: return {"trades":0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1,"score":-99999.0}
    if side == "long": th = np.percentile(preds, pct_top); cond = lambda p: p >= th
    else: th = np.percentile(preds, 100 - pct_top); cond = lambda p: p <= th

    trades = []; in_pos = False; ep=0.0; ei=0; dtp=0.0; dsl=0.0
    for i in range(len(data)):
        row = data.iloc[i]; prob = preds[i]
        if in_pos:
            if side == "long":
                if row['low'] <= dsl: trades.append(notional*((dsl-ep)/ep - fee*2)); in_pos=False
                elif row['high'] >= dtp: trades.append(notional*((dtp-ep)/ep - fee*2)); in_pos=False
            else:
                if row['high'] >= dsl: trades.append(notional*((ep-dsl)/ep - fee*2)); in_pos=False
                elif row['low'] <= dtp: trades.append(notional*((ep-dtp)/ep - fee*2)); in_pos=False
            if in_pos and (i - ei >= horizon):
                ex = row['close']; pnl = ((ex-ep)/ep if side=="long" else (ep-ex)/ep)
                trades.append(notional*(pnl - fee*2)); in_pos=False
        else:
            if abs(prob) < filt or not cond(prob): continue
            in_pos = True; ep = row['close']; ei = i; atr = row['atr_14']
            tp_pct = min(abs(prob), tp_max)/100; sl_pct = min((atr*atr_mult)/ep, sl_max/100)
            if side == "long": dtp = ep*(1+tp_pct); dsl = ep*(1-sl_pct)
            else: dtp = ep*(1-tp_pct); dsl = ep*(1+sl_pct)

    if not trades: return {"trades":0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1,"score":-99999.0}
    tr = np.array(trades); pnl = tr.sum(); wr = float((tr>0).mean())
    cum = np.cumsum(tr) + init_bal; peak = np.maximum.accumulate(cum)
    dd = float(((cum-peak)/peak*100).min())
    if dd == 0: dd = -0.1
    return {"trades": int(len(tr)), "pnl": round(float(pnl),2), "winrate": round(wr,4), "max_dd": round(dd,2), "score": round(float(pnl/abs(dd)),2)}

def train(df, side="long", mode="swing", models_dir=None, cfg=None):
    try:
        if cfg is None: cfg = {}
        if models_dir is None: models_dir = Path("crypto/data/models")
        models_dir = Path(models_dir)
        horizon = 12 if mode == "intraday" else 48
        data, cols = prepare_features(df, horizon, side)
        if len(data) < 1000: return None

        bt = cfg.get("backtest", {})
        split_pct = float(bt.get("test_split_pct", 0.2))
        split_idx = int(len(data) * (1 - split_pct)) - horizon
        if split_idx <= 200: return {"params": {"model_type":"random_forest_reg","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":None}, "metrics": {"trades":0,"score":-99999.0}}

        train_d, test_d = data.iloc[:split_idx], data.iloc[split_idx:].copy()
        imp = SimpleImputer(strategy='median')
        X_tr = imp.fit_transform(train_d[cols].values); y_tr = train_d['target'].values
        X_te = imp.transform(test_d[cols].values)

        model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)
        model.fit(X_tr, y_tr)
        preds = model.predict(X_te)
        metrics = _backtest(test_d.reset_index(drop=True), preds, side, horizon, cfg)

        X_all = imp.fit_transform(data[cols].values); y_all = data['target'].values
        final_model = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1)
        final_model.fit(X_all, y_all)
        
        wrapper = RFWrapper(final_model, imp)
        fname = f"model_rf_{mode}_{side}.pkl"
        with open(models_dir / fname, "wb") as f: pickle.dump(wrapper, f)

        return {"params": {"model_type":"random_forest_reg","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":fname}, "metrics": metrics}
    except Exception as e:
        log.error(f"Random Forest training failed: {e}"); return None