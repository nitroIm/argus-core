import os
import json
import logging
import itertools
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.backtest")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
OUT_FILE = SCRIPT_DIR / "grid_search_results.json"

# --- БАЗОВЫЕ НАСТРОЙКИ ---
INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001
TEST_YEARS = 6.0

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

# --- 1. БЛОК ПОИСКА ЗАКОНОМЕРНОСТЕЙ (EDA) ---
def analyze_patterns(df):
    log.info("=" * 60)
    log.info("1. АНАЛИЗ ЗАКОНОМЕРНОСТЕЙ (EDA)")
    log.info("=" * 60)
    
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df['forward_ret'] = df['close'].pct_change(periods=12).shift(-12) * 100.0 # Доходность через 12ч
    
    df_clean = df.dropna(subset=['rsi', 'macd_hist', 'forward_ret']).copy()
    
    for feature in ['rsi', 'macd_hist']:
        log.info(f"\n--- Статистика по {feature.upper()} ---")
        # Разбиваем на 10 равных интервалов (децилей)
        df_clean['bin'] = pd.qcut(df_clean[feature], q=10, labels=False, duplicates='drop')
        stats = df_clean.groupby('bin').agg(
            mean_ret=('forward_ret', 'mean'),
            win_rate=('forward_ret', lambda x: (x > 0).mean() * 100),
            min_val=(feature, 'min'),
            max_val=(feature, 'max')
        ).reset_index()
        
        for _, row in stats.iterrows():
            log.info(f"  [{row['min_val']:.2f} .. {row['max_val']:.2f}] | "
                     f"Ср.доходность: {row['mean_ret']:+.3f}% | Winrate: {row['win_rate']:.1f}%")

# --- 2. СИМУЛЯТОР ОДНОЙ СТРАТЕГИИ ---
def simulate_strategy(df, rsi_min, rsi_max, tp_pct, sl_pct, time_exit):
    trades = []
    in_pos = False
    entry_price = 0.0
    entry_idx = 0
    
    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        if in_pos:
            gross_pct = (curr['close'] - entry_price) / entry_price * 100.0
            bars_held = i - entry_idx
            
            # Условия выхода: Стоп, Тейк или Время
            if gross_pct <= -sl_pct or gross_pct >= tp_pct or bars_held >= time_exit:
                net_pct = gross_pct - (FEE_RATE * 200)
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                trades.append(pnl_usd)
                in_pos = False
        
        if not in_pos:
            # Вход: MACD > 0 и RSI в заданном диапазоне
            if curr['macd_hist'] > 0 and (rsi_min < curr['rsi'] < rsi_max):
                in_pos = True
                entry_price = curr['close']
                entry_idx = i
                
    return trades

# --- 3. ГЛАВНЫЙ ЦИКЛ (GRID SEARCH) ---
def main():
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    log.info("Loading data...")
    df = pd.read_parquet(DATA_FILE)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)
    
    # 1. Сначала смотрим на исторические закономерности
    analyze_patterns(df.copy())
    
    # 2. Подготовка данных для бэктеста
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df = df.dropna().reset_index(drop=True)
    
    # 3. Сетка параметров (мы переберем 180 комбинаций)
    rsi_ranges = [(30, 45), (35, 50), (40, 55), (45, 60), (35, 55), (40, 60)]
    tp_sls = [(4.0, 2.0), (5.0, 2.5), (6.0, 3.0), (8.0, 4.0), (10.0, 5.0)]
    time_exits = [12, 24, 48, 72, 168] # от 12 часов до 1 недели
    
    combos = list(itertools.product(rsi_ranges, tp_sls, time_exits))
    log.info(f"\n{'='*60}")
    log.info(f"2. GRID SEARCH: ЗАПУСК {len(combos)} КОМБИНАЦИЙ...")
    log.info(f"{'='*60}")
    
    results = []
    
    for idx, ((r_min, r_max), (tp, sl), t_exit) in enumerate(combos):
        trades = simulate_strategy(df, r_min, r_max, tp, sl, t_exit)
        
        if len(trades) < 10: # Игнорируем стратегии с малым количеством сделок
            continue
            
        pnls = np.array(trades)
        wins = (pnls > 0).sum()
        total_pnl = pnls.sum()
        
        # Считаем максимальную просадку
        cum = np.cumsum(pnls) + INITIAL_BALANCE
        peak = np.maximum.accumulate(cum)
        dd = ((cum - peak) / peak * 100).min()
        
        results.append({
            "rsi": f"{r_min}-{r_max}",
            "tp_sl": f"{tp}/{sl}",
            "time_exit": t_exit,
            "trades": len(trades),
            "winrate": round((wins / len(trades)) * 100, 2),
            "pnl": round(total_pnl, 2),
            "max_dd": round(dd, 2)
        })
        
        if (idx + 1) % 50 == 0:
            log.info(f"  Обработано {idx + 1} / {len(combos)} комбинаций...")
    
    # 4. Вывод результатов
    df_res = pd.DataFrame(results)
    if df_res.empty:
        log.info("Не найдено ни одной рабочей комбинации.")
        return
        
    df_res = df_res.sort_values(by="pnl", ascending=False).reset_index(drop=True)
    
    log.info("\n" + "=" * 60)
    log.info("🏆 ТОП-10 ЛУЧШИХ СТРАТЕГИЙ (по чистой прибыли):")
    log.info("=" * 60)
    for i, row in df_res.head(10).iterrows():
        log.info(f"  {i+1}. RSI={row['rsi']} | TP/SL={row['tp_sl']} | Time={row['time_exit']}ч | "
                 f"PnL=${row['pnl']} | WR={row['winrate']}% | Сделок={row['trades']}")
                 
    log.info("\n" + "=" * 60)
    log.info("🛡️ ТОП-3 САМЫЕ БЕЗОПАСНЫЕ (минимальная просадка при плюсовом PnL):")
    log.info("=" * 60)
    safe_df = df_res[df_res['pnl'] > 0].sort_values(by="max_dd", ascending=False)
    for i, row in safe_df.head(3).iterrows():
        log.info(f"  {i+1}. RSI={row['rsi']} | TP/SL={row['tp_sl']} | Time={row['time_exit']}ч | "
                 f"PnL=${row['pnl']} | MaxDD={row['max_dd']}% | Сделок={row['trades']}")
                 
    # Сохраняем все результаты в файл
    out_data = {"run_at": datetime.now(timezone.utc).isoformat(), "results": results}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    log.info(f"\nПолная таблица сохранена в {OUT_FILE.name}")

if __name__ == "__main__":
    main()