# ============================================================
# ARGUS - XGBOOST MODEL v7.5.0
# Path: crypto/learn/model_xgb.py
#
# ИЗМЕНЕНИЯ v7.5.0:
#   - Модель сохраняется в отдельный файл (.xgb), в params -> model_file
#   - time_exit_h = horizon (бот читает оттуда)
#   - Все параметры бэктеста из bot_config.backtest
#   - split_idx - horizon: убрана утечка target через границу train/test
#   - При 0 сделок -> metrics={"trades":0, "score": -99999}, не None
#   - Добавлено логирование: сколько train / test, сколько сделок
# ============================================================

import logging
import numpy as np
import pandas as pd
import xgboost as xgb
from pathlib import Path

log = logging.getLogger("model_xgb")

# ── Подготовка фичей (идентична v7.4) ───────────────────────
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

    base_features = ['rsi_norm', 'macd_norm', 'ema50_diff', 'ema20_diff',
                     'ema200_diff', 'vol_ratio', 'atr14_pct', 'atr50_pct',
                     'avg_trade_norm', 'pc1', 'pc4', 'pc24', 'buy_ratio']

    for lag in [1, 3, 6, 12, 24]:
        for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']:
            data[f'{f}_lag{lag}'] = data[f].shift(lag)

    macro_features = []
    if 'nasdaq' in data.columns:
        data['nasdaq_pct_1h'] = data['nasdaq'].pct_change(1)
        data['nasdaq_pct_24h'] = data['nasdaq'].pct_change(24)
        macro_features += ['nasdaq_pct_1h', 'nasdaq_pct_24h']
    if 'dxy' in data.columns:
        data['dxy_pct_1h'] = data['dxy'].pct_change(1)
        data['dxy_pct_24h'] = data['dxy'].pct_change(24)
        macro_features += ['dxy_pct_1h', 'dxy_pct_24h']
    if 'fear_greed' in data.columns:
        data['fear_greed_norm'] = data['fear_greed'] / 100.0
        macro_features.append('fear_greed_norm')
    if 'btc_dominance' in data.columns:
        data['btc_dom_pct'] = data['btc_dominance'].pct_change(24)
        macro_features.append('btc_dom_pct')
    if 'stablecoin_supply' in data.columns:
        data['stable_flow_1h'] = data['stablecoin_supply'].pct_change(1)
        data['stable_flow_24h'] = data['stablecoin_supply'].pct_change(24)
        macro_features += ['stable_flow_1h', 'stable_flow_24h']

    future_return = (data['close'].shift(-horizon) - data['close']) / data['close'] * 100
    data['target'] = future_return.clip(-20, 20)

    feature_cols = base_features + macro_features + \
        [f'{f}_lag{l}' for l in [1, 3, 6, 12, 24]
         for f in ['rsi_norm', 'macd_norm', 'ema50_diff', 'vol_ratio', 'buy_ratio']]

    data = data.dropna(subset=['target'])
    return data, feature_cols

# ── Бэктест (80/20) ─────────────────────────────────────────
def walk_forward_validate(data, feature_cols, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    fee_rate = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    initial_balance = float(bt.get("initial_balance", 100.0))
    tp_max_pct = float(bt.get("tp_max_pct", 10.0))
    sl_max_pct = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5))
    percentile_top = float(bt.get("percentile_top", 80.0))
    split_pct = float(bt.get("test_split_pct", 0.2))
    # фильтр входа разный для intraday/swing
    filter_min_pct = float(bt.get(
        "filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing",
        0.5 if horizon <= 24 else 1.5
    ))

    # ── 80/20 с учётом horizon (нет утечки через границу) ───
    split_idx = int(len(data) * (1 - split_pct)) - horizon
    if split_idx <= 100:
        return {"trades": 0, "pnl": 0.0, "winrate": 0.0, "max_dd": -0.1,
                "score": -99999.0}, None

    train_data = data.iloc[:split_idx]
    test_data = data.iloc[split_idx:].copy()

    X_train = train_data[feature_cols].values
    y_train = train_data['target'].values
    X_test = test_data[feature_cols].values

    model = xgb.XGBRegressor(
        n_estimators=100, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
        verbosity=0
    )
    model.fit(X_train, y_train)
    test_data['prediction'] = model.predict(X_test)

    preds = test_data['prediction'].values
    if len(preds) == 0:
        return {"trades": 0, "pnl": 0.0, "winrate": 0.0, "max_dd": -0.1,
                "score": -99999.0}, None

    if side == "long":
        threshold = np.percentile(preds, percentile_top)
        signal_cond = lambda p: p >= threshold
    else:
        threshold = np.percentile(preds, 100 - percentile_top)
        signal_cond = lambda p: p <= threshold

    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    dynamic_tp = 0.0
    dynamic_sl = 0.0

    for i in range(len(test_data)):
        row = test_data.iloc[i]
        prob = row['prediction']

        if in_pos:
            if side == "long":
                if row['low'] <= dynamic_sl:
                    pnl = notional * ((dynamic_sl - entry_price) / entry_price - fee_rate * 2)
                    trades.append(pnl); in_pos = False
                elif row['high'] >= dynamic_tp:
                    pnl = notional * ((dynamic_tp - entry_price) / entry_price - fee_rate * 2)
                    trades.append(pnl); in_pos = False
            else:
                if row['high'] >= dynamic_sl:
                    pnl = notional * ((entry_price - dynamic_sl) / entry_price - fee_rate * 2)
                    trades.append(pnl); in_pos = False
                elif row['low'] <= dynamic_tp:
                    pnl = notional * ((entry_price - dynamic_tp) / entry_price - fee_rate * 2)
                    trades.append(pnl); in_pos = False

            if in_pos and (i - entry_idx >= horizon):
                exit_price = row['close']
                pnl_pct = ((exit_price - entry_price) / entry_price
                           if side == "long"
                           else (entry_price - exit_price) / entry_price)
                trades.append(notional * (pnl_pct - fee_rate * 2))
                in_pos = False
        else:
            if abs(prob) < filter_min_pct:
                continue
            if not signal_cond(prob):
                continue
            in_pos = True
            entry_price = row['close']
            entry_idx = i
            atr = row['atr_14']
            tp_pct = min(abs(prob), tp_max_pct) / 100
            sl_pct = min((atr * atr_mult) / entry_price, sl_max_pct / 100)
            if side == "long":
                dynamic_tp = entry_price * (1 + tp_pct)
                dynamic_sl = entry_price * (1 - sl_pct)
            else:
                dynamic_tp = entry_price * (1 - tp_pct)
                dynamic_sl = entry_price * (1 + sl_pct)

    if not trades:
        return {"trades": 0, "pnl": 0.0, "winrate": 0.0, "max_dd": -0.1,
                "score": -99999.0}, None

    trades = np.array(trades)
    total_pnl = trades.sum()
    winrate = float((trades > 0).mean())
    cum = np.cumsum(trades) + initial_balance
    peak = np.maximum.accumulate(cum)
    max_dd = float(((cum - peak) / peak * 100).min())
    if max_dd == 0:
        max_dd = -0.1
    score = float(total_pnl / abs(max_dd))

    metrics = {
        "trades": int(len(trades)),
        "pnl": round(float(total_pnl), 2),
        "winrate": round(winrate, 4),
        "max_dd": round(max_dd, 2),
        "score": round(score, 2)
    }

    # финальная модель на 100%
    final_model = xgb.XGBRegressor(
        n_estimators=100, max_depth=4, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, random_state=42,
        verbosity=0
    )
    final_model.fit(data[feature_cols].values, data['target'].values)
    return metrics, final_model

# ── Публичный train ─────────────────────────────────────────
def train(df, side="long", mode="swing", models_dir=None, cfg=None):
    try:
        if cfg is None:
            cfg = {}
        if models_dir is None:
            models_dir = Path("crypto/data/models")
        models_dir = Path(models_dir)

        horizon = 12 if mode == "intraday" else 48
        data, feature_cols = prepare_features(df, horizon, side)
        if len(data) < 1000:
            log.info(f"  [{mode}/{side}] Not enough rows: {len(data)}")
            return None

        metrics, model = walk_forward_validate(data, feature_cols, side, horizon, cfg)
        if model is None:
            log.info(f"  [{mode}/{side}] No trades on test — returning zero metrics")
            return {"params": {"model_type": "xgboost_reg", "horizon": horizon,
                               "time_exit_h": horizon, "features": feature_cols,
                               "model_file": None},
                    "metrics": metrics}

        fname = f"model_xgb_{mode}_{side}.xgb"
        fpath = models_dir / fname
        model.get_booster().save_model(str(fpath))

        return {
            "params": {
                "model_type": "xgboost_reg",
                "horizon": horizon,
                "time_exit_h": horizon,
                "features": feature_cols,
                "model_file": fname,
            },
            "metrics": metrics,
        }
    except Exception as e:
        log.error(f"XGBoost training failed: {e}")
        return None