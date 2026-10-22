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
OUT_FILE = SCRIPT_DIR / "grid_search_results.json"

# --- НАСТРОЙКИ ПОБЕДИТЕЛЯ (из Grid Search) ---
INITIAL_BALANCE = 100.0
POSITION_NOTIONAL = 20.0
FEE_RATE = 0.001

# Лучшие параметры: RSI=35-50 | TP=10% | SL=5% | Time=168ч (1 неделя)
RSI_MIN = 35
RSI_MAX = 50
TP_PCT = 10.0
SL_PCT = 5.0
TIME_EXIT_HOURS = 168

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

def main():
    log.info("STARTING GOLDEN STRATEGY BACKTEST")
    log.info(f"Params: RSI {RSI_MIN}-{RSI_MAX} | TP {TP_PCT}% | SL {SL_PCT}% | Time {TIME_EXIT_HOURS}h")
    
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    df = pd.read_parquet(DATA_FILE)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)

    df['rsi'] = calculate_rsi(df['close'], 14)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df = df.dropna().reset_index(drop=True)

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
                    "reason": exit_reason
                })
                in_pos = False
        
        if not in_pos:
            # Вход: MACD > 0 и RSI в золотой зоне 35-50
            if curr['macd_hist'] > 0 and (RSI_MIN < curr['rsi'] < RSI_MAX):
                in_pos = True
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

    log.info("=" * 40)
    log.info(f"Total Trades: {n}")
    log.info(f"Winrate: {(wins/n)*100:.2f}%")
    log.info(f"Total PnL: ${total_pnl:.2f}")
    log.info(f"Total Return: {(total_pnl/INITIAL_BALANCE)*100:.2f}%")
    log.info(f"Max Drawdown: {max_dd:.2f}%")
    
    # Статистика выходов
    exit_stats = pd.DataFrame(trades)['reason'].value_counts().to_dict()
    log.info(f"Exit Reasons: {exit_stats}")
    log.info("=" * 40)

    out = {"run_at": datetime.now(timezone.utc).isoformat(), "metrics": {"trades": n, "pnl": total_pnl, "winrate": wins/n}, "trades": trades}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {OUT_FILE}")

if __name__ == "__main__":
    main()