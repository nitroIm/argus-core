# ============================================================
# ARGUS - GRU + ATTENTION MODEL v1.0.0
# Path: crypto/learn/model_gru.py
#
# ИДЕЯ: Bidirectional GRU захватывает временные паттерны (48ч окно).
# Multi-Head Attention выделяет важные моменты.
# Лучше всего работает на intraday (короткий горизонт).
#
# ЗАВИСИМОСТИ: torch (добавить в train.yml!)
# ============================================================

import logging, numpy as np, pandas as pd, pickle
from pathlib import Path

log = logging.getLogger("model_gru")

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    log.warning("PyTorch not installed. model_gru will be skipped.")

def prepare_features(df, horizon, seq_len=48):
    data = df.copy()
    data['rsi_norm'] = data['rsi'] / 100.0
    data['macd_norm'] = data['macd_hist'] / data['close']
    data['ema50_diff'] = (data['close'] - data['ema_50']) / data['ema_50']
    data['ema20_diff'] = (data['close'] - data['ema_20']) / data['ema_20']
    data['ema200_diff'] = (data['close'] - data['ema_200']) / data['ema_200']
    data['vol_ratio'] = data['volume'] / data['vol_sma']
    data['atr14_pct'] = data['atr_14'] / data['close']
    data['avg_trade_norm'] = data['avg_trade_size'] / data['volume'].rolling(20).mean()
    data['pc1'] = data['price_change_1h']; data['pc4'] = data['price_change_4h']; data['pc24'] = data['price_change_24h']
    base = ['rsi_norm','macd_norm','ema50_diff','ema20_diff','ema200_diff','vol_ratio','atr14_pct','avg_trade_norm','pc1','pc4','pc24','buy_ratio']
    data['target'] = ((data['close'].shift(-horizon) - data['close']) / data['close'] * 100).clip(-20, 20)
    data = data.dropna(subset=base + ['target'])
    
    # Формируем последовательности
    X_seq, y_seq, index_seq = [], [], []
    vals = data[base].values
    targets = data['target'].values
    idxs = data.index.values
    
    for i in range(len(data) - seq_len):
        X_seq.append(vals[i:i+seq_len])
        y_seq.append(targets[i+seq_len-1])
        index_seq.append(idxs[i+seq_len-1])
        
    return np.array(X_seq, dtype=np.float32), np.array(y_seq, dtype=np.float32), index_seq, data

class GRUAttention(nn.Module):
    def __init__(self, input_dim, hidden_dim=64, num_heads=4):
        super().__init__()
        self.gru = nn.GRU(input_dim, hidden_dim, num_layers=2, batch_first=True, bidirectional=True)
        self.attn = nn.MultiheadAttention(embed_dim=hidden_dim*2, num_heads=num_heads, batch_first=True)
        self.fc = nn.Sequential(
            nn.Linear(hidden_dim*2, 32),
            nn.ReLU(),
            nn.Dropout(0.25),
            nn.Linear(32, 1)
        )
        
    def forward(self, x):
        out, _ = self.gru(x)
        attn_out, _ = self.attn(out, out, out)
        pooled = torch.mean(attn_out, dim=1)
        return self.fc(pooled).squeeze(-1)

class GRUPredictor:
    def __init__(self, model, seq_len, feature_cols, base_cols):
        self.model = model
        self.seq_len = seq_len
        self.feature_cols = feature_cols
        self.base_cols = base_cols
    def predict(self, X_flat):
        # X_flat - плоский массив фичей (N, features) для совместимости с бэктестом
        # Нам нужно нарезать его на окна
        N = len(X_flat)
        preds = np.zeros(N)
        if N < self.seq_len: return preds
        self.model.eval()
        with torch.no_grad():
            # Берем последнее окно для каждого шага (или батчами)
            # Для простоты - один батч всех окон (осторожно с памятью)
            windows = []
            for i in range(N - self.seq_len + 1):
                windows.append(X_flat[i:i+self.seq_len])
            if not windows: return preds
            X_t = torch.tensor(np.array(windows), dtype=torch.float32)
            p = self.model(X_t).numpy()
            preds[self.seq_len-1:] = p
        return preds

def _backtest(data_aligned, preds, side, horizon, cfg):
    # То же самое, что в XGB
    bt = cfg.get("backtest", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    tp_max = float(bt.get("tp_max_pct", 10.0)); sl_max = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5)); pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))
    
    valid_mask = ~np.isnan(preds)
    if valid_mask.sum() == 0: return {"trades":0,"score":-99999.0}
    p_valid = preds[valid_mask]
    if side == "long":
        th = np.percentile(p_valid, pct_top); cond = lambda p: p >= th
    else:
        th = np.percentile(p_valid, 100 - pct_top); cond = lambda p: p <= th

    trades=[]; in_pos=False; ep=0.0; ei=0; dtp=0.0; dsl=0.0
    for i in range(len(data_aligned)):
        if np.isnan(preds[i]): continue
        row = data_aligned.iloc[i]; prob = preds[i]
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
    if not HAS_TORCH: return None
    try:
        if cfg is None: cfg = {}
        if models_dir is None: models_dir = Path("crypto/data/models")
        models_dir = Path(models_dir)
        horizon = 12 if mode == "intraday" else 48
        seq_len = 48
        
        X_seq, y_seq, idx_seq, data = prepare_features(df, horizon, seq_len)
        if len(X_seq) < 1000:
            log.info(f"  [{mode}/{side}] Not enough sequences"); return None
            
        bt = cfg.get("backtest", {})
        split_pct = float(bt.get("test_split_pct", 0.2))
        split_idx = int(len(X_seq) * (1 - split_pct))
        if split_idx < 500: return None
        
        X_tr, y_tr = torch.tensor(X_seq[:split_idx]), torch.tensor(y_seq[:split_idx])
        X_te, y_te = torch.tensor(X_seq[split_idx:]), torch.tensor(y_seq[split_idx:])
        
        # Масштабирование (нормализация по train)
        mean = X_tr.mean(dim=(0,1), keepdim=True)
        std = X_tr.std(dim=(0,1), keepdim=True) + 1e-6
        X_tr = (X_tr - mean) / std
        X_te = (X_te - mean) / std
        
        model = GRUAttention(input_dim=X_tr.shape[2], hidden_dim=64)
        opt = torch.optim.Adam(model.parameters(), lr=0.001)
        loss_fn = nn.MSELoss()
        
        loader = DataLoader(TensorDataset(X_tr, y_tr), batch_size=128, shuffle=True)
        epochs = 15
        model.train()
        for ep in range(epochs):
            for bx, by in loader:
                opt.zero_grad()
                pred = model(bx)
                loss = loss_fn(pred, by)
                loss.backward()
                opt.step()
                
        # Бэктест на тесте
        model.eval()
        with torch.no_grad():
            preds_te = model(X_te).numpy()
            
        # Align backtest data
        test_data_aligned = data.iloc[seq_len-1:].iloc[split_idx:].reset_index(drop=True)
        full_preds = np.full(len(test_data_aligned), np.nan)
        full_preds[:len(preds_te)] = preds_te
        metrics = _backtest(test_data_aligned, full_preds, side, horizon, cfg)
        
        # Финальное сохранение (модель + scaler + metadata)
        final_model = GRUAttention(input_dim=X_seq.shape[2], hidden_dim=64)
        final_model.load_state_dict(model.state_dict())
        wrapper = GRUPredictor(final_model, seq_len, [], list(data.columns))
        wrapper.mean = mean
        wrapper.std = std
        
        fname = f"model_gru_{mode}_{side}.pkl"
        with open(models_dir / fname, "wb") as f:
            pickle.dump(wrapper, f)
            
        return {
            "params": {"model_type":"gru_attention","horizon":horizon,"time_exit_h":horizon,"features":[],"model_file":fname},
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"GRU training failed: {e}")
        return None