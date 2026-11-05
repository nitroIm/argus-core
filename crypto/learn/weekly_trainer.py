# ============================================================
# ARGUS - WEEKLY TRAINER ORCHESTRATOR v4.1 (WITH REPORT)
# Path: crypto/learn/weekly_trainer.py
# ============================================================

import os
import json
import logging
import importlib.util
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import psycopg
import warnings

warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

VERSION = "4.1.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.trainer")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
MODEL_OUT_FILE = SCRIPT_DIR.parent / "data" / "models" / "best_params.json"
MODEL_OUT_FILE.parent.mkdir(parents=True, exist_ok=True)

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

def calculate_ema(series, period): return series.ewm(span=period, adjust=False).mean()

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

def calculate_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()

def fetch_fresh_candles():
    if not DB_URL: return pd.DataFrame()
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        query = "SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' AND open_time > NOW() - INTERVAL '30 days' ORDER BY open_time ASC"
        df = pd.read_sql(query, conn)
        conn.close()
        if df.empty: return df
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df = df.set_index('open_time')
        df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'taker_buy_volume': 'sum', 'count': 'sum'}).dropna().reset_index()
        return df_1h
    except Exception as e:
        log.error(f"Failed to fetch from DB: {e}")
        return pd.DataFrame()

def load_models():
    models = []
    for file_path in SCRIPT_DIR.glob("model_*.py"):
        model_name = file_path.stem
        try:
            spec = importlib.util.spec_from_file_location(model_name, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "train"):
                models.append((model_name, module))
                log.info(f"Loaded model: {model_name}")
            else:
                log.warning(f"Model {model_name} skipped: no 'train' function.")
        except Exception as e:
            log.error(f"Failed to load model {model_name}: {e}")
    return models

def main():
    log.info(f"STARTING WEEKLY TRAINER ORCHESTRATOR v{VERSION}")
    
    if not DATA_FILE.exists(): 
        log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")
        return
    
    log.info("Loading historical data...")
    df_hist = pd.read_parquet(DATA_FILE)
    df_hist['open_time'] = df_hist['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df_hist['open_time'] = pd.to_datetime(df_hist['open_time'], unit='ms', utc=True)
    
    if 'taker_buy_volume' not in df_hist.columns: df_hist['taker_buy_volume'] = df_hist['volume'] * 0.5
    if 'count' not in df_hist.columns: df_hist['count'] = 100
    
    df_fresh = fetch_fresh_candles()
    df = pd.concat([df_hist, df_fresh]) if not df_fresh.empty else df_hist
    df = df.drop_duplicates(subset=['open_time']).sort_values('open_time').reset_index(drop=True)
    
    log.info("Calculating indicators...")
    df['rsi'] = calculate_rsi(df['close'], 14)
    df['rsi_7'] = calculate_rsi(df['close'], 7)
    df['rsi_21'] = calculate_rsi(df['close'], 21)
    df['macd_hist'] = calculate_macd(df['close'], 12, 26, 9)
    df['ema_50'] = calculate_ema(df['close'], 50)
    df['ema_20'] = calculate_ema(df['close'], 20)
    df['ema_200'] = calculate_ema(df['close'], 200)
    df['vol_sma'] = df['volume'].rolling(window=20).mean()
    df['atr_7'] = calculate_atr(df, 7)
    df['atr_14'] = calculate_atr(df, 14)
    df['atr_50'] = calculate_atr(df, 50)
    df['atr_100'] = calculate_atr(df, 100)
    df['buy_ratio'] = df['taker_buy_volume'] / df['volume'].replace(0, 1)
    df['avg_trade_size'] = df['volume'] / df['count'].replace(0, 1)
    df['price_change_1h'] = df['close'].pct_change(1)
    df['price_change_4h'] = df['close'].pct_change(4)
    df['price_change_24h'] = df['close'].pct_change(24)
    df = df.dropna().reset_index(drop=True)
    log.info(f"Data ready. Total rows: {len(df)}")

    models = load_models()
    if not models: 
        log.error("No models found!")
        return

    out_data = {
        "trainer_version": VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "intraday": {"long": None, "short": None},
        "swing": {"long": None, "short": None},
        "all_scores": {}
    }

    for mode in ["intraday", "swing"]:
        out_data["all_scores"][mode] = {}
        for side in ["long", "short"]:
            log.info(f"--- Searching best model for {mode.upper()} / {side.upper()} ---")
            best_score = -99999
            best_model_data = None
            model_scores = {}

            for model_name, module in models:
                try:
                    res = module.train(df, side=side, mode=mode)
                    if res and res.get("metrics"):
                        sc = res["metrics"].get("score", -99999)
                        model_scores[model_name] = res["metrics"]
                        if sc > best_score:
                            best_score = sc
                            res["source"] = model_name
                            best_model_data = res
                            log.info(f"  🏆 New best: {model_name} (Score: {sc})")
                except Exception as e:
                    log.error(f"  ❌ Model {model_name} crashed on {mode}/{side}: {e}")
                    model_scores[model_name] = {"error": str(e)}
                    
            if best_model_data:
                out_data[mode][side] = best_model_data
            out_data["all_scores"][mode][side] = model_scores

    with open(MODEL_OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {MODEL_OUT_FILE}")

    # --- ЗАПУСК ОТЧЁТА ---
    try:
        log.info("Running weekly_trainer_report...")
        subprocess.run(["python", "crypto/report/weekly_trainer_report.py"], check=False)
    except Exception as e:
        log.error(f"Report failed: {e}")

if __name__ == "__main__":
    main()