import os
import json
import logging
import itertools
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.optimizer")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
OUT_FILE = SCRIPT_DIR / "best_params.json" # Сюда сохраним лучшие настройки

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001

# --- СЕТКА ПАРАМЕТРОВ ДЛЯ ОБУЧЕНИЯ ---
# Бот переберет все возможные комбинации
PARAM_GRID = {
    "rsi_min": [25, 30, 35, 40],
    "rsi_max": [55, 60, 65, 70],
    "tp_pct": [3.0, 5.0, 8.0, 10.0, 15.0],
    "sl_pct": [2.0, 3.0, 5.0, 7.0],
    "time_exit_h": [24, 48, 72, 168]
}

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_macd(series, fast=12, slow=26, signal=9):
    ema_fast = calculate_ema(series, fast)
    ema_slow = calculate_ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    return macd_line - signal_line

def run_backtest(df, rsi_min, rsi_max, tp_pct, sl_pct, time_exit_h):
    """Прогоняет стратегию с конкретными параметрами и возвращает метрики"""
    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        
        if in_pos:
            gross_pct = (curr['close'] - entry_price) / entry_price * 100.0
            bars_held = i - entry_idx
            
            exit_reason = None
            if gross_pct <= -sl_pct:
                exit_reason = "STOP_LOSS"
            elif gross_pct >= tp_pct:
                exit_reason = "TAKE_PROFIT"
            elif bars_held >= time_exit_h:
                exit_reason = "TIME_EXIT"
                
            if exit_reason:
                net_pct = gross_pct - (FEE_RATE * 200)
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                trades.append(pnl_usd)
                in_pos = False
        
        if not in_pos:
            # Логика входа: MACD > 0 и RSI в заданном диапазоне
            if curr['macd_hist'] > 0 and (rsi_min < curr['rsi'] < rsi_max):
                in_pos = True
                entry_price = curr['close']
                entry_idx = i

    n = len(trades)
    if n == 0: return None

    pnls = np.array(trades)
    total_pnl = float(pnls.sum())
    
    # Считаем просадку
    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0

    # Защита от нулевой просадки
    if max_dd == 0: max_dd = -0.1

    # Оценка (Score): Профит делим на просадку (чем больше, тем лучше)
    score = total_pnl / abs(max_dd)

    return {
        "trades": n,
        "pnl": total_pnl,
        "winrate": float((pnls > 0).mean()),
        "max_dd": max_dd,
        "score": score
    }

def main():
    log.info("STARTING GRID SEARCH OPTIMIZATION")
    
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    # Загружаем и готовим данные
    log.info("Loading data...")
    df = pd.read_parquet(DATA_FILE)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df = df.dropna().reset_index(drop=True)

    # Генерируем все комбинации
    keys, values = zip(*PARAM_GRID.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    log.info(f"Testing {len(combinations)} combinations...")
    
    best_result = None
    best_params = None

    for idx, params in enumerate(combinations):
        res = run_backtest(
            df, 
            params["rsi_min"], params["rsi_max"], 
            params["tp_pct"], params["sl_pct"], 
            params["time_exit_h"]
        )
        
        if res and (best_result is None or res["score"] > best_result["score"]):
            best_result = res
            best_params = params

        if (idx + 1) % 50 == 0:
            log.info(f"Checked {idx + 1}/{len(combinations)}...")

    if best_params:
        log.info("=" * 40)
        log.info("BEST STRATEGY FOUND:")
        log.info(f"Params: {best_params}")
        log.info(f"PnL: ${best_result['pnl']:.2f} | Winrate: {best_result['winrate']*100:.1f}% | Max DD: {best_result['max_dd']:.2f}%")
        log.info("=" * 40)

        # Сохраняем лучшие параметры для торгового бота
        out_data = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "params": best_params,
            "metrics": best_result
        }
        with open(OUT_FILE, "w", encoding="utf-8") as f:
            json.dump(out_data, f, ensure_ascii=False, indent=2)
        log.info(f"Saved to {OUT_FILE}")
    else:
        log.info("No profitable combinations found.")

if __name__ == "__main__":
    main()