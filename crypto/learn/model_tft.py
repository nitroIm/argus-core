# ============================================================
# ARGUS - TEMPORAL FUSION TRANSFORMER (TFT) v1.0.1
# Path: crypto/learn/model_tft.py
# ============================================================

import logging, numpy as np, pandas as pd, pickle
from pathlib import Path

log = logging.getLogger("model_tft")

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    log.warning("PyTorch not installed. model_tft will be skipped.")

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
    
    # Защита на случай, если buy_ratio не пришел
    if 'buy_ratio' not in data.columns:
        data['buy_ratio'] = data['taker_buy_volume'] / data['volume'].replace(0, 1)

    base = ['rsi_norm','macd_norm','ema50_diff','ema20_diff','ema200_diff','vol_ratio','atr14_pct','avg_trade_norm','pc1','pc4','pc24','buy_ratio']
    data['target'] = ((data['close'].shift(-horizon) - data['close']) / data['close'] * 100).clip(-20, 20)
    data = data.dropna(subset=base + ['target'])
    
    vals = data[base].values
    targets = data['target'].values
    X_seq, y_seq = [], []
    for i in range(len(data) - seq_len):
        X_seq.append(vals[i:i+seq_len])
        y_seq.append(targets[i+seq_len-1])
    return np.array(X_seq, dtype=np.float32), np.array(y_seq, dtype=np.float32), data

class TFTWrapper:
    def __init__(self, model, mean, std, seq_len):
        self.model = model
        self.mean = mean
        self.std = std
        self.seq_len = seq_len
    def predict(self, X_seq):
        self.model.eval()
        with torch.no_grad():
            X_t = torch.tensor(X_seq, dtype=torch.float32)
            X_t = (X_t - self.mean) / self.std
            return self.model(X_t).numpy()

class SimpleTFT(nn.Module):
    def __init__(self, input_dim, d_model=32):
        super().__init__()
        self.vsn = nn.Linear(input_dim, input_dim) # Variable Selection
        self.lstm = nn.LSTM(input_dim, d_model, num_layers=1, batch_first=True)
        self.attn = nn.MultiheadAttention(d_model, num_heads=2, batch_first=True)
        self.fc = nn.Linear(d_model, 1)
        
    def forward(self, x):
        x = torch.relu(self.vsn(x))
        x, _ = self.lstm(x)
        x, _ = self.attn(x, x, x)
        x = x[:, -1, :] # последний шаг
        return self.fc(x).squeeze(-1)

def _backtest(data_aligned, preds, side, horizon, cfg):
    bt = cfg.get("backtest", {})
    fee = float(cfg.get("global", {}).get("fee_pct", 0.001))
    notional = float(bt.get("position_notional", 20.0))
    init_bal = float(bt.get("initial_balance", 100.0))
    tp_max = float(bt.get("tp_max_pct", 10.0)); sl_max = float(bt.get("sl_max_pct", 3.0))
    atr_mult = float(bt.get("atr_mult", 1.5)); pct_top = float(bt.get("percentile_top", 80.0))
    filt = float(bt.get("filter_min_pct_intraday" if horizon <= 24 else "filter_min_pct_swing", 0.5 if horizon <= 24 else 1.5))
    if len(preds) == 0: return {"trades":0,"score":-99999.0}
    if side == "long":
        th = np.percentile(preds, pct_top); cond = lambda p: p >= th
    else:
        th = np.percentile(preds, 100 - pct_top); cond = lambda p: p <= th
    trades=[]; in_pos=False; ep=0.0; ei=0; dtp=0.0; dsl=0.0
    for i in range(len(data_aligned)):
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
        
        X_seq, y_seq, data = prepare_features(df, horizon, seq_len)
        if len(X_seq) < 1000: return None
            
        bt = cfg.get("backtest", {})
        split_pct = float(bt.get("test_split_pct", 0.2))
        split_idx = int(len(X_seq) * (1 - split_pct))
        
        X_tr, y_tr = torch.tensor(X_seq[:split_idx]), torch.tensor(y_seq[:split_idx])
        X_te, y_te = torch.tensor(X_seq[split_idx:]), torch.tensor(y_seq[split_idx:])
        
        # Нормализация X
        mean = X_tr.mean(dim=(0,1), keepdim=True)
        std = X_tr.std(dim=(0,1), keepdim=True) + 1e-6
        X_tr = (X_tr - mean) / std
        X_te = (X_te - mean) / std
        
        # НОРМАЛИЗАЦИЯ Y (КРИТИЧЕСКИЙ ФИКС)
        y_mean = y_tr.mean()
        y_std = y_tr.std() + 1e-6
        y_tr_norm = (y_tr - y_mean) / y_std
        
        model = SimpleTFT(input_dim=X_tr.shape[2], d_model=32)
        opt = torch.optim.Adam(model.parameters(), lr=0.001)
        loss_fn = nn.MSELoss()
        
        loader = DataLoader(TensorDataset(X_tr, y_tr_norm), batch_size=128, shuffle=True)
        model.train()
        # УВЕЛИЧИЛИ ЭПОХИ ДО 50
        for _ in range(50):
            for bx, by in loader:
                opt.zero_grad()
                pred = model(bx)
                loss = loss_fn(pred, by)
                loss.backward()
                opt.step()
                
        model.eval()
        with torch.no_grad():
            preds_norm = model(X_te).numpy()
            # ВОЗВРАЩАЕМ МАСШТАБ Y
            preds = preds_norm * y_std.item() + y_mean.item()
            
        test_data_aligned = data.iloc[seq_len-1:].iloc[split_idx:].reset_index(drop=True)
        full_preds = np.full(len(test_data_aligned), np.nan)
        full_preds[:len(preds)] = preds
        metrics = _backtest(test_data_aligned, full_preds, side, horizon, cfg)
        
        wrapper = TFTWrapper(model, mean, std, seq_len)
        fname = f"model_tft_{mode}_{side}.pkl"
        with open(models_dir / fname, "wb") as f:
            pickle.dump(wrapper, f)
            
        return {
            "params": {"model_type":"tft","horizon":horizon,"time_exit_h":horizon,"features":[],"model_file":fname},
            "metrics": metrics
        }
    except Exception as e:
        log.error(f"TFT training failed: {e}")
        return None