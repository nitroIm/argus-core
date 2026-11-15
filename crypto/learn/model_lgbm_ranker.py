# ============================================================
# ARGUS - LIGHTGBM RANKER v1.0.0
# Path: crypto/learn/model_lgbm_ranker.py
#
# ИДЕЯ: вместо регрессии "сколько процентов будет" - ранжирование.
# LightGBM lambdarank учится: какие часы дадут лучший вход.
# Каждое окно (block_size=48ч) - группа. Внутри группы - ранги по target.
# На выходе score_rank -> конвертируем в "псевдо-доходность" для бота.
# ============================================================

import logging, numpy as np, pandas as pd, lightgbm as lgb
from pathlib import Path

log = logging.getLogger("model_lgbm_ranker")

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
    data['target'] = ((data['close'].shift(-horizon) - data['close']) / data['close'] * 100).clip(-20, 20)
    cols = base + [f'{f}_lag{l}' for l in [1,3,6,12,24] for f in ['rsi_norm','macd_norm','ema50_diff','vol_ratio','buy_ratio']]
    data = data.dropna(subset=['target'] + cols)
    return data, cols

def _make_ranks(targets, block_size):
    """Внутри каждого блока - целочисленные ранги 0..N-1 по target."""
    n = len(targets)
    ranks = np.zeros(n, dtype=int)
    n_blocks = (n + block_size - 1) // block_size
    for b in range(n_blocks):
        s = b*block_size; e = min((b+1)*block_size, n)
        if e - s < 2: continue
        order = np.argsort(-targets[s:e])  # по убыванию target
        for r, idx in enumerate(order):
            ranks[s + idx] = r
    return ranks

def _backtest_from_scores(data, scores, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    tp_max = float(bt.get("tp_max_pct", 10.0)); sl_max = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5)); pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))
    # scores у ranker в 0..N, конвертим в псевдо-%
    if scores.max() > 0:
        scores_pct = scores / scores.max() * 5.0  # 0..5%
    else:
        scores_pct = scores
    if len(scores_pct) == 0:
        return {"trades":0,"score":-99999.0,"pnl":0.0,"winrate":0.0,"max_dd":-0.1}
    if side == "long":
        th = np.percentile(scores_pct, pct_top); cond = lambda p: p >= th
    else:
        th = np.percentile(scores_pct, 100 - pct_top); cond = lambda p: p <= th
    trades=[]; in_pos=False; ep=0.0; ei=0; dtp=0.0; dsl=0.0
    for i in range(len(data)):
        row = data.iloc[i]; prob = scores_pct[i]
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
            return {"params": {"model_type":"lgbm_ranker","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":None}, "metrics":{"trades":0,"score":-99999.0}}
        train_d, test_d = data.iloc[:split_idx], data.iloc[split_idx:].copy()

        block_size = horizon
        y_tr_rank = _make_ranks(train_d['target'].values, block_size)
        # groups: размеры блоков
        n = len(train_d)
        group_sizes = []
        for b in range((n + block_size - 1) // block_size):
            s = b*block_size; e = min((b+1)*block_size, n)
            group_sizes.append(e - s)

        X_tr = train_d[cols].values; X_te = test_d[cols].values
        # для ranker нужны целые неотрицательные метки
        model = lgb.LGBMRanker(
            n_estimators=200, num_leaves=31, learning_rate=0.05,
            objective="lambdarank", metric="ndcg", verbose=-1, random_state=42,
            label_gain=[2**i - 1 for i in range(50)]
        )
        model.fit(X_tr, y_tr_rank, group=group_sizes)
        scores_te = model.predict(X_te)
        metrics = _backtest_from_scores(test_d.reset_index(drop=True), scores_te, side, horizon, cfg)

        # финальный fit на 100%
        y_all_rank = _make_ranks(data['target'].values, block_size)
        n_all = len(data)
        gs_all = []
        for b in range((n_all + block_size - 1) // block_size):
            s = b*block_size; e = min((b+1)*block_size, n_all)
            gs_all.append(e - s)
        final = lgb.LGBMRanker(
            n_estimators=200, num_leaves=31, learning_rate=0.05,
            objective="lambdarank", metric="ndcg", verbose=-1, random_state=42,
            label_gain=[2**i - 1 for i in range(50)]
        )
        final.fit(data[cols].values, y_all_rank, group=gs_all)

        fname = f"model_lgbm_ranker_{mode}_{side}.txt"
        final.booster_.save_model(str(models_dir / fname))

        return {
            "params": {"model_type":"lgbm_ranker","horizon":horizon,"time_exit_h":horizon,"features":cols,"model_file":fname},
            "metrics": metrics,
        }
    except Exception as e:
        log.error(f"LightGBM Ranker training failed: {e}")
        return None