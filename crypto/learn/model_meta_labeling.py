# ============================================================
# ARGUS - META-LABELING MODEL v1.0.0
# Path: crypto/learn/model_meta_labeling.py
#
# ИДЕЯ (Lopez de Prado):
#   Первичная модель (технические сигналы) даёт "сигнал идти".
#   Мета-модель (бинарный классификатор) предсказывает:
#   "если пойдём по сигналу - будет прибыль?"
#   Мы учим мета-модель на исторических "что если бы вошли".
#   Score = (prob_win - 0.5) * 20, диапазон -10..+10.
# ============================================================

import logging, numpy as np, pandas as pd, pickle, xgboost as xgb
from pathlib import Path
from sklearn.impute import SimpleImputer

log = logging.getLogger("model_meta_labeling")

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
    data['atr14_pct'] = data['atr_14'] / data['close']
    data['avg_trade_norm'] = data['avg_trade_size'] / data['volume'].rolling(20).mean()
    data['pc1'] = data['price_change_1h']; data['pc4'] = data['price_change_4h']; data['pc24'] = data['price_change_24h']
    base = ['rsi_norm','macd_norm','ema50_diff','ema20_diff','ema200_diff','vol_ratio','atr14_pct','avg_trade_norm','pc1','pc4','pc24','buy_ratio']
    for lag in [1,3,6,12,24]:
        for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']:
            data[f'{f}_lag{lag}'] = data[f].shift(lag)
    data['target'] = ((data['close'].shift(-horizon) - data['close']) / data['close'] * 100).clip(-20, 20)
    cols = base + [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']]
    data = data.dropna(subset=['target'])
    return data, cols

def _primary_signal(row, side):
    """Технический сигнал: LONG/SHORT. Возвращает True если есть вход."""
    macd_ok = (row['macd_hist'] > 0) if side == "long" else (row['macd_hist'] < 0)
    ema_ok = (row['close'] > row['ema_50']) if side == "long" else (row['close'] < row['ema_50'])
    return bool(macd_ok and ema_ok)

class MetaLabelModel:
    """Обёртка: .predict(X) возвращает ndarray of (prob-0.5)*20."""
    def __init__(self, clf, imputer):
        self.clf = clf
        self.imputer = imputer
    def predict(self, X):
        X = self.imputer.transform(X)
        return (self.clf.predict_proba(X)[:, 1] - 0.5) * 20.0

def _backtest(data, feature_cols, preds, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    tp_max = float(bt.get("tp_max_pct", 10.0)); sl_max = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5)); pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))
    if len(preds) == 0:
        return {"trades":0,"score":-99999.0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1}
    if side == "long":
        th = np.percentile(preds, pct_top); cond = lambda p: p >= th
    else:
        th = np.percentile(preds, 100 - pct_top); cond = lambda p: p <= th
    trades=[]; in_pos=False; ep=0.0; ei=0; dtp=0.0; dsl=0.0
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
            if not _primary_signal(row, side): continue
            in_pos = True; ep = row['close']; ei = i; atr = row['atr_14']
            tp_pct = min(abs(prob), tp_max)/100; sl_pct = min((atr*atr_mult)/ep, sl_max/100)
            if side == "long": dtp = ep*(1+tp_pct); dsl = ep*(1-sl_pct)
            else: dtp = ep*(1-tp_pct); dsl = ep*(1+sl_pct)
    if not trades:
        return {"trades":0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1,"score":-99999.0}
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
        if len(data) < 1000:
            log.info(f"  [{mode}/{side}] Not enough rows"); return None
        bt = cfg.get("backtest", {})
        split_pct = float(bt.get("test_split_pct", 0.2))
        split_idx = int(len(data) * (1 - split_pct)) - horizon
        if split_idx <= 200:
            return {"params": {"model_type":"meta_labeling","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":None}, "metrics":{"trades":0,"score":-99999.0}}
        train_d, test_d = data.iloc[:split_idx], data.iloc[split_idx:].copy()

        # Метка: был ли target > 0 при наличии сигнала
        train_d = train_d.copy()
        train_d['primary'] = train_d.apply(lambda r: _primary_signal(r, side), axis=1)
        # Обучаем на ВСЕХ строках, но метка бинарная
        # (если сигнала не было - всё равно учим предсказывать направление target)
        # Лучше: только на строках с primary=True, иначе мало данных
        mask = train_d['primary']
        if mask.sum() < 200:
            log.info(f"  [{mode}/{side}] Too few primary signals: {mask.sum()}"); return None
        X_tr_full = train_d.loc[mask, cols].values
        y_tr_full = (train_d.loc[mask, 'target'] > 0).astype(int).values

        imp = SimpleImputer(strategy='median')
        X_tr = imp.fit_transform(X_tr_full)
        # class_weight чтобы компенсировать дисбаланс
        pos_ratio = y_tr_full.mean()
        scale_pos = (1 - pos_ratio) / max(pos_ratio, 1e-6)
        clf = xgb.XGBClassifier(
            n_estimators=100, max_depth=4, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, random_state=42,
            scale_pos_weight=scale_pos, verbosity=0, eval_metric='logloss'
        )
        clf.fit(X_tr, y_tr_full)

        wrapper = MetaLabelModel(clf, imp)
        preds_te = wrapper.predict(test_d[cols].values)
        metrics = _backtest(test_d.reset_index(drop=True), cols, preds_te, side, horizon, cfg)

        # финальный на всех primary-строках
        data_full = data.copy()
        data_full['primary'] = data_full.apply(lambda r: _primary_signal(r, side), axis=1)
        m_all = data_full['primary']
        if m_all.sum() < 200:
            return {"params": {"model_type":"meta_labeling","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":None}, "metrics": metrics}
        X_all = imp.fit_transform(data_full.loc[m_all, cols].values)
        y_all = (data_full.loc[m_all, 'target'] > 0).astype(int).values
        pr = y_all.mean()
        spw = (1 - pr) / max(pr, 1e-6)
        f_clf = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, subsample=0.8, colsample_bytree=0.8, random_state=42, scale_pos_weight=spw, verbosity=0, eval_metric='logloss').fit(X_all, y_all)
        final = MetaLabelModel(f_clf, imp)

        fname = f"model_meta_labeling_{mode}_{side}.pkl"
        with open(models_dir / fname, "wb") as f: pickle.dump(final, f)

        return {
            "params": {"model_type":"meta_labeling","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":fname},
            "metrics": metrics,
        }
    except Exception as e:
        log.error(f"Meta-labeling training failed: {e}")
        return None