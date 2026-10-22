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

# ТВОИ НАСТРОЙКИ
INITIAL_BALANCE = float(os.getenv("BT_BALANCE", "100.0")) # Баланс 100$
POSITION_NOTIONAL = float(os.getenv("BT_NOTIONAL", "20.0")) # Позиция 20$
FEE_RATE = 0.001 # 0.1% комиссия
TEST_YEARS = 6.0

# НАСТРОЙКИ СТРАТЕГИИ
EMA_FAST = 20
EMA_SLOW = 50
RSI_LIMIT = 65
TP_PCT = 8.0  # Тейк-профит 8%
SL_PCT = 3.0  # Стоп-лосс 3%

def calculate_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def main():
    log.info("STARTING BTC/USDT 1H BACKTEST")
    log.info(f"Balance: ${INITIAL_BALANCE} | Notional: ${POSITION_NOTIONAL}")
    
    if not DATA_FILE.exists():
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return

    log.info("Loading data...")
    df = pd.read_parquet(DATA_FILE)
    
    # Фикс времени
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)

    log.info("Calculating indicators...")
    df['ema_fast'] = calculate_ema(df['close'], EMA_FAST)
    df['ema_slow'] = calculate_ema(df['close'], EMA_SLOW)
    df['rsi_14'] = calculate_rsi(df['close'], 14)
    
    df = df.dropna().reset_index(drop=True)

    log.info("Simulating trades...")
    trades = []
    in_position = False
    entry_price = 0.0

    for i in range(1, len(df)):
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        
        if in_position:
            gross_pct = (curr['close'] - entry_price) / entry_price * 100.0
            is_death_cross = (prev['ema_fast'] >= prev['ema_slow']) and (curr['ema_fast'] < curr['ema_slow'])
            
            # Выход: Стоп-лосс, Тейк-профит или обратное пересечение
            if gross_pct <= -SL_PCT or gross_pct >= TP_PCT or is_death_cross:
                net_pct = gross_pct - (FEE_RATE * 200)
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                
                trades.append({
                    "timestamp": curr['open_time'].isoformat(),
                    "net_pct": round(net_pct, 4),
                    "pnl_usd": round(pnl_usd, 4)
                })
                in_position = False

        if not in_position:
            golden_cross = (prev['ema_fast'] <= prev['ema_slow']) and (curr['ema_fast'] > curr['ema_slow'])
            # Вход: Золотой крест и RSI ниже лимита
            if golden_cross and curr['rsi_14'] < RSI_LIMIT:
                in_position = True
                entry_price = curr['close']

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
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "total_return_pct": round(total_pnl / INITIAL_BALANCE * 100.0, 4),
        "max_drawdown_pct": round(max_dd, 3),
    }

    log.info("=" * 40)
    log.info(f"Trades: {n} | Winrate: {metrics['win_rate']*100:.2f}%")
    log.info(f"PnL: ${total_pnl:.2f} | Return: {metrics['total_return_pct']}%")
    log.info(f"Max Drawdown: {max_dd:.2f}%")
    log.info("=" * 40)

    out = {"run_at": datetime.now(timezone.utc).isoformat(), "metrics": metrics, "trades": trades}
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {OUT_FILE}")

if __name__ == "__main__":
    main()