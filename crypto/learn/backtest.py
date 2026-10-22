import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.backtest")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
OUT_FILE = SCRIPT_DIR / "backtest_results.json"

# --- НАСТРОЙКИ БЭКТЕСТА ---
INITIAL_BALANCE = float(os.getenv("BT_BALANCE", "100.0"))
POSITION_NOTIONAL = float(os.getenv("BT_NOTIONAL", "20.0"))
FEE_RATE = 0.001
TEST_YEARS = 6.0

# --- НАСТРОЙКИ СТРАТЕГИИ V4 ---
RSI_MIN = 36.0
RSI_MAX = 55.0
TP_PCT = 6.0
SL_PCT = 3.0
TIME_EXIT_HOURS = 12     # Закрываем сделку принудительно через 12 часов
COOLDOWN_HOURS = 12      # После сделки ждем 12 часов перед новой

# --- НАСТРОЙКИ АНАЛИЗА ЗАКОНОМЕРНОСТЕЙ ---
FORWARD_HORIZON = 12
N_BINS = 5

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
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def analyze_patterns(df):
    log.info("=" * 60)
    log.info("АНАЛИЗ ЗАКОНОМЕРНОСТЕЙ (EDA)")
    log.info("=" * 60)
    df['rsi'] = calculate_rsi(df['close'], 14)
    _, _, df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df['forward_ret'] = df['close'].pct_change(periods=FORWARD_HORIZON).shift(-FORWARD_HORIZON) * 100.0
    df_clean = df.dropna(subset=['rsi', 'macd_hist', 'forward_ret']).copy()

    def analyze_feature(feature_name, feature_series):
        log.info(f"\n--- Анализ: {feature_name} ---")
        df_clean['bin'] = pd.qcut(feature_series, q=N_BINS, labels=False, duplicates='drop')
        stats = df_clean.groupby('bin').agg(
            mean_ret=('forward_ret', 'mean'),
            win_rate=('forward_ret', lambda x: (x > 0).mean() * 100),
            count=('forward_ret', 'size'),
            min_val=(feature_name, 'min'),
            max_val=(feature_name, 'max')
        ).reset_index()
        for _, row in stats.iterrows():
            log.info(f"  Бин {int(row['bin'])}: [{row['min_val']:.2f} .. {row['max_val']:.2f}] | Ср. доходность: {row['mean_ret']:+.3f}% | Winrate: {row['win_rate']:.1f}%")

    for feature in ['rsi', 'macd_hist']:
        analyze_feature(feature, df_clean[feature])
    log.info("=" * 60)

def main():
    log.info("STARTING BTC/USDT 1H BACKTEST V4")
    
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    df = pd.read_parquet(DATA_FILE)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)

    # 1. Анализ
    analyze_patterns(df.copy())

    # 2. Бэктест (Стратегия V4)
    log.info("\n" + "=" * 60)
    log.info("ЗАПУСК БЭКТЕСТА СТРАТЕГИИ V4 (Триггерный вход + Time Exit)")
    log.info("=" * 60)

    df['rsi_14'] = calculate_rsi(df['close'], 14)
    _, _, df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df = df.dropna().reset_index(drop=True)

    trades = []
    in_position = False
    entry_price = 0.0
    entry_idx = 0
    cooldown_until_idx = 0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        if in_position:
            gross_pct = (curr['close'] - entry_price) / entry_price * 100.0
            bars_held = i - entry_idx
            
            # Условия выхода: Стоп, Тейк или Время (12 часов)
            exit_reason = None
            if gross_pct <= -SL_PCT:
                exit_reason = "STOP_LOSS"
            elif gross_pct >= TP_PCT:
                exit_reason = "TAKE_PROFIT"
            elif bars_held >= TIME_EXIT_HOURS:
                exit_reason = "TIME_EXIT"
                
            if exit_reason:
                net_pct = gross_pct - (FEE_RATE * 200)
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                trades.append({
                    "timestamp": curr['open_time'].isoformat(), 
                    "net_pct": round(net_pct, 4), 
                    "pnl_usd": round(pnl_usd, 4),
                    "exit_reason": exit_reason
                })
                in_position = False
                cooldown_until_idx = i + COOLDOWN_HOURS

        if not in_position and i > cooldown_until_idx:
            # Вход: MACD пробивает ноль СНИЗУ ВВЕРХ и RSI в золотой зоне
            macd_cross = (prev['macd_hist'] <= 0) and (curr['macd_hist'] > 0)
            rsi_ok = RSI_MIN < curr['rsi_14'] < RSI_MAX
            
            if macd_cross and rsi_ok:
                in_position = True
                entry_price = curr['close']
                entry_idx = i

    n = len(trades)
    if n == 0:
        log.info("0 trades generated.")
        return

    pnls = np.array([t["pnl_usd"] for t in trades])
    wins = int((pnls > 0).sum())
    total_pnl = float(pnls.sum())
    
    cum = np.cumsum(pnls) + INITIAL_BALANCE
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0

    metrics = {
        "n_trades": n,
        "wins": wins,
        "win_rate": round(wins / n, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "total_return_pct": round(total_pnl / INITIAL_BALANCE * 100.0, 4),
        "max_drawdown_pct": round(max_dd, 3),
    }

    log.info("=" * 40)
    log.info(f"Trades: {n} | Winrate: {metrics['win_rate']*100:.2f}%")
    log.info(f"PnL: ${total_pnl:.2f} | Return: {metrics['total_return_pct']}%")
    log.info(f"Max Drawdown: {max_dd:.2f}%")
    
    # Считаем статистику по причинам выхода
    exit_stats = pd.DataFrame(trades)['exit_reason'].value_counts().to_dict()
    log.info(f"Выходы: {exit_stats}")
    log.info("=" * 40)

    out = {"run_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics, "trades": trades}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {OUT_FILE}")

if __name__ == "__main__":
    main()