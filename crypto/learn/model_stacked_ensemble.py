# ============================================================
# ARGUS - STACKED ENSEMBLE MODEL v1.0.3
# Path: crypto/learn/model_stacked_ensemble.py
# ФИКС: убрана опечатка np.ep
# ============================================================

import logging, numpy as np, pandas as pd, pickle
import xgboost as xgb
import lightgbm as lgb
import catboost as cb
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import KFold
from sklearn.impute import SimpleImputer
from pathlib import Path

log = logging.getLogger("model_stacked_ensemble")

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

class StackedRegressor:
    def __init__(self, models, meta, imputer):
        self.models = models
        self.meta = meta
        self.imputer = imputer
    def predict(self, X):
        X = self.imputer.transform(X)
        preds = np.column_stack([m.predict(X) for m in self.models])
        return self.meta.predict(preds)

def _backtest(data, preds, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    rm = cfg.get("risk_management", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))

    if len(preds) == 0: return {"trades":0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1,"score":-99999.0}
    if side == "long": th = np.percentile(preds, pct_top); cond = lambda p: p >= th
    else: th = np.percentile(preds, 100 - pct_top); cond = lambda p: p <= th

    trades = []; in_pos = False
    entry=0.0; sl=0.0; tp1=0.0; tp2=0.0; size_left=0; tp1_hit=False; ei=0; max_p=0.0; min_p=0.0
    for i in range(len(data)):
        row = data.iloc[i]; prob = preds[i]
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
            in_pos = True; entry = row['close']; ei = i; max_p = entry; min_p = entry
            sl_pct = max(rm.get("min_sl_pct",0.5), min(rm.get("max_sl_pct",3.0), c_atr * rm.get("atr_mult_sl",1.5) / entry * 100))
            tp1_pct = max(rm.get("min_tp1_pct",0.8), min(rm.get("max_tp1_pct",2.0), c_atr * rm.get("atr_mult_tp1",1.0) / entry * 100))
            tp2_pct = max(rm.get("min_tp2_pct",2.0), min(rm.get("max_tp2_pct",8.0), c_atr * rm.get("atr_mult_tp2",2.5) / entry * 100))
            if side == "long": sl = entry*(1-sl_pct/100); tp1 = entry*(1+tp1_pct/100); tp2 = entry*(1+tp2_pct/100)
            else: sl = entry*(1+sl_pct/100); tp1 = entry*(1-tp1_pct/100); tp2 = entry*(1-tp2_pct/100)
            tp1_hit = False; size_left = 100

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
        if split_idx <= 200: return {"params": {"model_type":"stacked_ensemble_reg","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":None}, "metrics": {"trades":0,"score":-99999.0}}

        train_d, test_d = data.iloc[:split_idx], data.iloc[split_idx:].copy()
        imp = SimpleImputer(strategy='median')
        X_tr = imp.fit_transform(train_d[cols].values); y_tr = train_d['target'].values
        X_te = imp.transform(test_d[cols].values)

        oof = np.zeros((len(X_tr), 4))
        kf = KFold(n_splits=5, shuffle=False)
        for fold, (ti, vi) in enumerate(kf.split(X_tr)):
            Xf, yf = X_tr[ti], y_tr[ti]; Xv = X_tr[vi]
            oof[vi, 0] = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1).fit(Xf, yf).predict(Xv)
            oof[vi, 1] = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0).fit(Xf, yf).predict(Xv)
            oof[vi, 2] = lgb.LGBMRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1).fit(Xf, yf).predict(Xv)
            oof[vi, 3] = cb.CatBoostRegressor(iterations=100, depth=4, learning_rate=0.05, verbose=0, random_seed=42, nan_mode='Min').fit(Xf, yf).predict(Xv)

        meta = Ridge(alpha=1.0).fit(oof, y_tr)

        m_rf = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1).fit(X_tr, y_tr)
        m_xgb = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0).fit(X_tr, y_tr)
        m_lgb = lgb.LGBMRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1).fit(X_tr, y_tr)
        m_cb = cb.CatBoostRegressor(iterations=100, depth=4, learning_rate=0.05, verbose=0, random_seed=42, nan_mode='Min').fit(X_tr, y_tr)

        stacked = StackedRegressor([m_rf, m_xgb, m_lgb, m_cb], meta, imp)
        preds = stacked.predict(test_d[cols].values)
        metrics = _backtest(test_d.reset_index(drop=True), preds, side, horizon, cfg)

        X_all = imp.fit_transform(data[cols].values); y_all = data['target'].values
        f_rf = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1).fit(X_all, y_all)
        f_xgb = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0).fit(X_all, y_all)
        f_lgb = lgb.LGBMRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1).fit(X_all, y_all)
        f_cb = cb.CatBoostRegressor(iterations=100, depth=4, learning_rate=0.05, verbose=0, random_seed=42, nan_mode='Min').fit(X_all, y_all)
        
        oof_all = np.zeros((len(X_all), 4))
        for ti, vi in KFold(n_splits=5, shuffle=False).split(X_all):
            oof_all[vi, 0] = RandomForestRegressor(n_estimators=100, max_depth=6, random_state=42, n_jobs=-1).fit(X_all[ti], y_all[ti]).predict(X_all[vi])
            oof_all[vi, 1] = xgb.XGBRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbosity=0).fit(X_all[ti], y_all[ti]).predict(X_all[vi])
            oof_all[vi, 2] = lgb.LGBMRegressor(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, verbose=-1).fit(X_all[ti], y_all[ti]).predict(X_all[vi])
            oof_all[vi, 3] = cb.CatBoostRegressor(iterations=100, depth=4, learning_rate=0.05, verbose=0, random_seed=42, nan_mode='Min').fit(X_all[ti], y_all[ti]).predict(X_all[vi])
        
        f_meta = Ridge(alpha=1.0).fit(oof_all, y_all)
        final = StackedRegressor([f_rf, f_xgb, f_lgb, f_cb], f_meta, imp)

        fname = f"model_stacked_ensemble_{mode}_{side}.pkl"
        with open(models_dir / fname, "wb") as f: pickle.dump(final, f)

        return {"params": {"model_type":"stacked_ensemble_reg","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":fname}, "metrics": metrics}
    except Exception as e:
        log.error(f"StackedEnsemble training failed: {e}"); return None