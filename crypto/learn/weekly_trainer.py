# ============================================================
# ARGUS - WEEKLY TRAINER (MODEL OPTIMIZER)
# ------------------------------------------------------------
# Version: 1.0.0
# Date: 2026-10-28
# Description: 
#   Обучает модель на исторических данных (Parquet).
#   Перебирает сетку параметров (Grid Search) для поиска 
#   лучшей стратегии (RSI, TP, SL, фильтры тренда/объема).
#   Сохраняет лучшие параметры в best_params.json.
#   Запускается раз в неделю (по воскресеньям).
# ============================================================

import os
import json
import logging
import itertools
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

VERSION = "1.0.0"

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger("crypto.trainer")

SCRIPT_DIR = Path(__file__).resolve().parent
# Путь к историческим данным (6 лет)
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
# Куда сохраняем результат обучения
OUT_FILE = SCRIPT_DIR / "best_params.json" 

INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001

# --- СЕТКА ПАРАМЕТРОВ (Что будет перебирать бот) ---
PARAM_GRID = {
    "use_trend_filter": [True, False],  # Фильтр по EMA50
    "use_volume_filter": [True, False], # Фильтр по объему (SMA20)
    "rsi_min": [25, 30, 35],
    "rsi_max": [50, 55, 60, 70],
    "tp_pct": [2.0, 3.0, 5.0, 8.0, 15.0],
    "sl_pct": [1.5, 2.0, 3.0, 5.0],
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

def run_backtest(df, params):
    """Прогоняет стратегию с конкретными настройками"""
    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0

    for i, curr in enumerate(df.itertuples()):
        if i == 0: continue
        
        if in_pos:
            gross_pct = (curr.close - entry_price) / entry_price * 100.0
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
            # 1. Базовая логика (MACD > 0 и RSI в диапазоне)
            signal = curr.macd_hist > 0 and (params["rsi_min"] < curr.rsi < params["rsi_max"])
            
            # 2. Дополнительные фильтры
            if params["use_trend_filter"]:
                signal = signal and (curr.close > curr.ema_50)
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
    
    # Считаем максимальную просадку (Max Drawdown)
    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    if max_dd == 0: max_dd = -0.1

    # Оценка (Score): Профит делим на просадку. Чем выше, тем лучше.
    score = total_pnl / abs(max_dd)

    return {
        "trades": n,
        "pnl": round(total_pnl, 2),
        "winrate": round(float((pnls > 0).mean()), 4),
        "max_dd": round(max_dd, 2),
        "score": round(score, 2)
    }

def main():
    log.info(f"STARTING WEEKLY TRAINER v{VERSION}")
    
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    # 1. Загрузка и подготовка данных
    log.info("Loading historical data & calculating indicators...")
    df = pd.read_parquet(DATA_FILE)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)

    df['rsi'] = calculate_rsi(df['close'], 14)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df['ema_50'] = calculate_ema(df['close'], 50)
    df['vol_sma'] = df['volume'].rolling(window=20).mean()
    df = df.dropna().reset_index(drop=True)
    
    log.info(f"Data loaded. Total candles: {len(df)}")

    # 2. Генерация сетки
    keys, values = zip(*PARAM_GRID.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    
    log.info(f"Testing {len(combinations)} combinations...")
    
    best_result = None
    best_params = None

    # 3. Перебор комбинаций
    for idx, params in enumerate(combinations):
        res = run_backtest(df, params)
        
        # Если результат лучше предыдущего лучшего — запоминаем
        if res and (best_result is None or res["score"] > best_result["score"]):
            best_result = res
            best_params = params

        if (idx + 1) % 100 == 0:
            log.info(f"Checked {idx + 1}/{len(combinations)}...")

    # 4. Сохранение результатов
    if best_params:
        log.info("=" * 50)
        log.info("🏆 BEST STRATEGY FOUND:")
        for k, v in best_params.items():
            log.info(f"  {k}: {v}")
        log.info(f"📈 PnL: ${best_result['pnl']} | Winrate: {best_result['winrate']*100:.1f}% | Max DD: {best_result['max_dd']}%")
        log.info("=" * 50)

        out_data = {
            "trainer_version": VERSION,
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