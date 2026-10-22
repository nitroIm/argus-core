# ============================================================
# ARGUS-Trader - BACKTEST (Rule-Based BTC 1H)
# ============================================================
import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pandas_ta as ta

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "vision_out"
DATA_FILE = DATA_DIR / "BTCUSDT_spot_1h.parquet"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("crypto.backtest")

# --- Конфигурация ---
INITIAL_BALANCE = 1000.0
POSITION_NOTIONAL = 100.0 # Размер одной сделки (в долларах)
FEE_RATE = 0.001 # 0.1% комиссия
TEST_YEARS = 6 

def compute_metrics(trades, initial_balance):
    if not trades:
        return {"n_trades": 0, "win_rate": 0.0, "total_pnl_usd": 0.0, "sharpe": 0.0, "max_drawdown_pct": 0.0}
    
    n = len(trades)
    pnls = np.array([t["pnl_usd"] for t in trades], dtype=np.float64)
    pcts = np.array([t["net_pct"] for t in trades], dtype=np.float64)
    
    wins = int((pnls > 0).sum())
    total_pnl = float(pnls.sum())
    
    mean_pct = float(pcts.mean())
    std_pct = float(pcts.std(ddof=1)) if n > 1 else 0.0
    
    sharpe = 0.0
    if std_pct > 0 and TEST_YEARS > 0:
        tpy = n / TEST_YEARS
        sharpe = mean_pct / std_pct * np.sqrt(tpy)

    # Drawdown
    cum = np.cumsum(pnls) + initial_balance
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak * 100.0
    max_dd = float(dd.min()) if len(dd) else 0.0
    
    return {
        "n_trades": n, 
        "wins": wins, 
        "losses": n - wins,
        "win_rate": round(wins / n, 4),
        "total_pnl_usd": round(total_pnl, 4),
        "total_return_pct": round(total_pnl / initial_balance * 100.0, 4),
        "avg_trade_pct": round(mean_pct, 4),
        "sharpe": round(float(sharpe), 3),
        "max_drawdown_pct": round(max_dd, 3),
    }

def main():
    log.info("=" * 60)
    log.info("STARTING BTC/USDT 1H BACKTEST")
    log.info("=" * 60)

    if not DATA_FILE.exists():
        log.error(f"File not found: {DATA_FILE}")
        return

    # 1. Загрузка данных
    log.info("Loading data...")
    df = pd.read_parquet(DATA_FILE)
    
    # Фикс времени (как мы делали в check_data.py)
    df['open_time'] = df['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
    df = df.sort_values('open_time').reset_index(drop=True)

    # 2. Расчет индикаторов
    log.info("Calculating indicators (EMA 50/200, RSI 14)...")
    df['ema_50'] = ta.ema(df['close'], length=50)
    df['ema_200'] = ta.ema(df['close'], length=200)
    df['rsi_14'] = ta.rsi(df['close'], length=14)
    
    # Убираем первые 200 строк, где EMA еще не рассчиталась
    df = df.dropna().reset_index(drop=True)

    # 3. Симуляция торговли
    log.info("Simulating trades...")
    trades = []
    in_position = False
    entry_price = 0.0

    for i in range(1, len(df)):
        current_row = df.iloc[i]
        prev_row = df.iloc[i-1]
        
        # --- Логика выхода (Если мы в позиции) ---
        if in_position:
            # Стоп-лосс 2% или Тейк-профит 5%
            gross_pct = (current_row['close'] - entry_price) / entry_price * 100.0
            
            # Выходим если: сработал SL/TP ИЛИ пересечение EMA вниз
            if gross_pct <= -2.0 or gross_pct >= 5.0 or (prev_row['ema_50'] >= prev_row['ema_200'] and current_row['ema_50'] < current_row['ema_200']):
                net_pct = gross_pct - (FEE_RATE * 200) # Комиссия на вход и выход
                pnl_usd = POSITION_NOTIONAL * (net_pct / 100.0)
                
                trades.append({
                    "timestamp": current_row['open_time'].isoformat(),
                    "direction": "LONG",
                    "entry_price": entry_price,
                    "exit_price": current_row['close'],
                    "net_pct": round(net_pct, 4),
                    "pnl_usd": round(pnl_usd, 4)
                })
                in_position = False

        # --- Логика входа (Если мы вне позиции) ---
        if not in_position:
            # Условие: EMA50 пересекает EMA200 снизу вверх (Golden Cross) И RSI < 70
            golden_cross = (prev_row['ema_50'] <= prev_row['ema_200']) and (current_row['ema_50'] > current_row['ema_200'])
            
            if golden_cross and current_row['rsi_14'] < 70:
                in_position = True
                entry_price = current_row['close'] # Входим по цене закрытия свечи (упрощенно)

    # 4. Подсчет результатов
    metrics = compute_metrics(trades, INITIAL_BALANCE)

    log.info("=" * 60)
    log.info("BACKTEST RESULTS")
    log.info("Total Trades: %d", metrics["n_trades"])
    log.info("Win Rate: %.2f%%", metrics["win_rate"] * 100)
    log.info("Total PnL: $%+.2f", metrics["total_pnl_usd"])
    log.info("Total Return: %+.2f%%", metrics["total_return_pct"])
    log.info("Sharpe Ratio: %.3f", metrics["sharpe"])
    log.info("Max Drawdown: %.2f%%", metrics["max_drawdown_pct"])
    log.info("=" * 60)

    # 5. Сохранение результатов
    out = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "strategy": "EMA 50/200 Cross + RSI Filter",
            "initial_balance": INITIAL_BALANCE,
            "fee_rate": FEE_RATE
        },
        "metrics": metrics,
        "trades": trades
    }
    out_path = SCRIPT_DIR / "backtest_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    log.info("Results saved to: %s", out_path.name)


if __name__ == "__main__":
    main()