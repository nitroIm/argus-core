# ============================================================
# ARGUS - BASE MODEL (RULES & GRID SEARCH)
# Path: crypto/learn/model_base.py
# ============================================================

import logging
import itertools
import numpy as np

log = logging.getLogger("model_base")

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001

# Сетка для Лонга
GRID_LONG = {
    "use_trend_filter": [True, False],
    "use_volume_filter": [True, False],
    "rsi_min": [25, 30, 35],
    "rsi_max": [50, 55, 60, 70],
    "tp_pct": [2.0, 3.0, 5.0, 8.0, 15.0],
    "sl_pct": [1.5, 2.0, 3.0, 5.0],
    "time_exit_h": [24, 48, 72, 168]
}

# Сетка для Шорта
GRID_SHORT = {
    "use_trend_filter": [True, False],
    "use_volume_filter": [True, False],
    "rsi_min": [50, 60, 70],
    "rsi_max": [70, 80, 90],
    "tp_pct": [2.0, 3.0, 5.0, 8.0, 15.0],
    "sl_pct": [1.5, 2.0, 3.0, 5.0],
    "time_exit_h": [24, 48, 72, 168]
}

def run_backtest(df, params, side="long"):
    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0

    for i, curr in enumerate(df.itertuples()):
        if i == 0: continue
        
        if in_pos:
            if side == "long":
                gross_pct = (curr.close - entry_price) / entry_price * 100.0
            else:
                gross_pct = (entry_price - curr.close) / entry_price * 100.0

            bars_held = i - entry_idx
            
            exit_reason = None
            if gross_pct <= -params["sl_pct"]:
                exit_reason = "STOP_LOSS"
            elif gross_pct >= params["tp_pct"]:
                exit_reason = "TAKE_PROFIT"
            elif bars_held >= params["time_exit_h"]:
                exit_reason = "TIME_EXIT"
                
            if exit_reason:
                net_pct = gross_pct - (FEE_RATE * 200)
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                trades.append(pnl_usd)
                in_pos = False
        
        if not in_pos:
            if side == "long":
                signal = curr.macd_hist > 0 and (params["rsi_min"] < curr.rsi < params["rsi_max"])
                if params["use_trend_filter"]:
                    signal = signal and (curr.close > curr.ema_50)
            else:
                signal = curr.macd_hist < 0 and (params["rsi_min"] < curr.rsi < params["rsi_max"])
                if params["use_trend_filter"]:
                    signal = signal and (curr.close < curr.ema_50)

            if params["use_volume_filter"]:
                signal = signal and (curr.volume > curr.vol_sma)
                
            if signal:
                in_pos = True
                entry_price = curr.close
                entry_idx = i

    n = len(trades)
    if n == 0: return None

    pnls = np.array(trades)
    total_pnl = float(pnls.sum())
    
    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    if max_dd == 0: max_dd = -0.1

    score = total_pnl / abs(max_dd)
    return {
        "trades": n, "pnl": round(total_pnl, 2),
        "winrate": round(float((pnls > 0).mean()), 4),
        "max_dd": round(max_dd, 2), "score": round(score, 2)
    }

def train(df, side="long"):
    """Функция, которую вызывает оркестратор."""
    grid = GRID_LONG if side == "long" else GRID_SHORT
    keys, values = zip(*grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    best_result = None
    best_params = None

    for params in combinations:
        res = run_backtest(df, params, side=side)
        if res and (best_result is None or res["score"] > best_result["score"]):
            best_result = res
            best_params = params

    if not best_params:
        return None

    return {
        "params": best_params,
        "metrics": best_result
    }