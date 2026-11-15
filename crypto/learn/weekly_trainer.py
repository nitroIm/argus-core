# ============================================================
# ARGUS - WEEKLY TRAINER ORCHESTRATOR v7.3 (FULL HISTORY)
# Path: crypto/learn/weekly_trainer.py
# ============================================================

import os, json, logging, importlib.util, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import psycopg
import warnings

warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

VERSION = "7.3.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.trainer")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
MODEL_OUT_FILE = SCRIPT_DIR.parent / "data" / "models" / "best_params.json"
CONFIG_FILE = SCRIPT_DIR.parent / "data" / "intel" / "bot_config.json"

MODEL_OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
LOOKBACK_DAYS = 730 # Берем 2 года

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

def fetch_macro_data(start_date):
    log.info("Fetching Macro Data from DB...")
    if not DB_URL: return pd.DataFrame()
    
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        frames = []
        
        df_mi = pd.read_sql("SELECT ticker, open_time as timestamp, close FROM market_indices WHERE open_time >= %s ORDER BY open_time ASC", conn, params=(start_date,))
        if not df_mi.empty:
            df_mi['timestamp'] = pd.to_datetime(df_mi['timestamp'], utc=True)
            df_mi = df_mi.pivot(index='timestamp', columns='ticker', values='close')
            df_mi = df_mi.resample('1h').ffill()
            df_mi = df_mi.rename(columns={'^IXIC': 'nasdaq', 'DX-Y.NYB': 'dxy', '^GSPC': 'sp500', 'GC=F': 'gold', 'CL=F': 'oil'})
            frames.append(df_mi)

        df_gm = pd.read_sql("SELECT last_updated as timestamp, btc_dominance, total_market_cap, stablecoin_market_cap FROM global_metrics WHERE last_updated >= %s ORDER BY last_updated ASC", conn, params=(start_date,))
        if not df_gm.empty:
            df_gm['timestamp'] = pd.to_datetime(df_gm['timestamp'], utc=True)
            df_gm = df_gm.set_index('timestamp').resample('1h').ffill()
            frames.append(df_gm)

        df_st = pd.read_sql("SELECT timestamp, SUM(circulating) as stablecoin_supply FROM stablecoins WHERE timestamp >= %s GROUP BY timestamp ORDER BY timestamp ASC", conn, params=(start_date,))
        if not df_st.empty:
            df_st['timestamp'] = pd.to_datetime(df_st['timestamp'], utc=True)
            df_st = df_st.set_index('timestamp').resample('1h').ffill()
            frames.append(df_st)

        df_fg = pd.read_sql("SELECT timestamp, value as fear_greed FROM fear_greed WHERE timestamp >= %s ORDER BY timestamp ASC", conn, params=(start_date,))
        if not df_fg.empty:
            df_fg['timestamp'] = pd.to_datetime(df_fg['timestamp'], utc=True)
            df_fg = df_fg.set_index('timestamp').resample('1h').ffill()
            frames.append(df_fg)

        conn.close()
        if frames: return pd.concat(frames, axis=1, sort=True)
        return pd.DataFrame()
    except Exception as e:
        log.error(f"Failed to fetch macro data: {e}")
        return pd.DataFrame()

def fetch_fresh_candles():
    if not DB_URL: return pd.DataFrame()
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        df = pd.read_sql("SELECT open_time, open, high, low, close, volume, taker_buy_volume, count FROM candles WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot' AND open_time > NOW() - INTERVAL '30 days' ORDER BY open_time ASC", conn)
        conn.close()
        if df.empty: return df
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df = df.set_index('open_time')
        return df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum', 'taker_buy_volume': 'sum', 'count': 'sum'}).dropna().reset_index()
    except Exception: return pd.DataFrame()

def load_models():
    models = []
    for file_path in SCRIPT_DIR.glob("model_*.py"):
        try:
            spec = importlib.util.spec_from_file_location(file_path.stem, file_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "train"): models.append((file_path.stem, module))
        except Exception as e: log.error(f"Failed to load {file_path.stem}: {e}")
    return models

def main():
    log.info(f"STARTING WEEKLY TRAINER ORCHESTRATOR v{VERSION}")
    
    if not DATA_FILE.exists(): return log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")

    df_hist = pd.read_parquet(DATA_FILE)
    df_hist['open_time'] = pd.to_datetime(df_hist['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x), unit='ms', utc=True)
    if 'taker_buy_volume' not in df_hist.columns: df_hist['taker_buy_volume'] = df_hist['volume'] * 0.5
    if 'count' not in df_hist.columns: df_hist['count'] = 100
    
    df_fresh = fetch_fresh_candles()
    df_btc = pd.concat([df_hist, df_fresh]).drop_duplicates(subset=['open_time']).sort_values('open_time').reset_index(drop=True)
    
    max_date = df_btc['open_time'].max()
    cutoff_date = max_date - pd.Timedelta(days=LOOKBACK_DAYS)
    df_btc = df_btc[df_btc['open_time'] >= cutoff_date].reset_index(drop=True)
    
    df_btc['rsi'] = calculate_rsi(df_btc['close'], 14)
    df_btc['rsi_7'] = calculate_rsi(df_btc['close'], 7)
    df_btc['rsi_21'] = calculate_rsi(df_btc['close'], 21)
    df_btc['macd_hist'] = calculate_macd(df_btc['close'], 12, 26, 9)
    df_btc['ema_50'] = calculate_ema(df_btc['close'], 50)
    df_btc['ema_20'] = calculate_ema(df_btc['close'], 20)
    df_btc['ema_200'] = calculate_ema(df_btc['close'], 200)
    df_btc['vol_sma'] = df_btc['volume'].rolling(window=20).mean()
    df_btc['atr_7'] = calculate_atr(df_btc, 7)
    df_btc['atr_14'] = calculate_atr(df_btc, 14)
    df_btc['atr_50'] = calculate_atr(df_btc, 50)
    df_btc['atr_100'] = calculate_atr(df_btc, 100)
    df_btc['buy_ratio'] = df_btc['taker_buy_volume'] / df_btc['volume'].replace(0, 1)
    df_btc['avg_trade_size'] = df_btc['volume'] / df_btc['count'].replace(0, 1)
    df_btc['price_change_1h'] = df_btc['close'].pct_change(1)
    df_btc['price_change_4h'] = df_btc['close'].pct_change(4)
    df_btc['price_change_24h'] = df_btc['close'].pct_change(24)

    # --- СКЛЕЙКА (БЕЗ ОБРЕЗКИ ИСТОРИИ) ---
    macro_df = fetch_macro_data(cutoff_date)
    
    if not macro_df.empty:
        macro_df = macro_df.ffill().bfill()
        df_btc = df_btc.set_index('open_time')
        # Джойним. Не удаляем строки, где макро-данных нет. Оставляем NaN. XGBoost с этим разберется.
        df = df_btc.join(macro_df, how='left').reset_index()
        log.info(f"Merged Data (with NaNs for old dates)! Total rows: {len(df)}")
    else:
        log.warning("No macro data found in DB. Using BTC only.")
        df = df_btc.reset_index(drop=True)

    if df.empty or len(df) < 1000:
        log.error("CRITICAL: Not enough data to train models. Exiting.")
        return

    log.info(f"Training window: {df['open_time'].min().strftime('%Y-%m-%d')} to {df['open_time'].max().strftime('%Y-%m-%d')} | Rows: {len(df)}")

    models = load_models()
    
    # --- БЕЗОПАСНАЯ ЗАГРУЗКА СТАРОГО JSON ---
    old_data = {}
    if MODEL_OUT_FILE.exists():
        try:
            with open(MODEL_OUT_FILE, "r", encoding="utf-8") as f: 
                old_data = json.load(f)
                if old_data is None: old_data = {}
        except: pass

    out_data = {"trainer_version": VERSION, "updated_at": datetime.now(timezone.utc).isoformat(), "intraday": {"long": None, "short": None}, "swing": {"long": None, "short": None}, "all_scores": {}}

    for mode in ["intraday", "swing"]:
        out_data["all_scores"][mode] = {}
        for side in ["long", "short"]:
            log.info(f"--- Searching best model for {mode.upper()} / {side.upper()} ---")
            best_score = -99999; best_model_data = None; model_scores = {}

            for model_name, module in models:
                try:
                    res = module.train(df, side=side, mode=mode)
                    if res and res.get("metrics"):
                        sc = res["metrics"].get("score", -99999)
                        model_scores[model_name] = res["metrics"]
                        if sc > best_score:
                            best_score = sc; res["source"] = model_name; best_model_data = res
                            log.info(f"  🏆 New best: {model_name} (Score: {sc})")
                except Exception as e:
                    log.error(f"  ❌ Model {model_name} crashed: {e}")
                    model_scores[model_name] = {"error": str(e)}
            
            # БЕЗОПАСНОЕ ЧТЕНИЕ СТАРОГО СКОРА
            old_mode = old_data.get(mode) or {}
            old_side = old_mode.get(side) or {}
            old_metrics = old_side.get("metrics") or {}
            old_score = old_metrics.get("score", -99999)
                    
            if best_model_data and best_score <= old_score:
                log.info(f"  🛡️ Keeping OLD model (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = old_side
            elif best_model_data:
                log.info(f"  🚀 Updating model! (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = best_model_data
            out_data["all_scores"][mode][side] = model_scores

    with open(MODEL_OUT_FILE, "w", encoding="utf-8") as f: json.dump(out_data, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {MODEL_OUT_FILE}")

    try: subprocess.run(["python", "crypto/report/weekly_trainer_report.py"], check=False)
    except Exception as e: log.error(f"Report failed: {e}")

if __name__ == "__main__":
    main()