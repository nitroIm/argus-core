# ============================================================
# ARGUS - WEEKLY TRAINER ORCHESTRATOR v6.0 (MACRO CONTEXT)
# Path: crypto/learn/weekly_trainer.py
# ============================================================

# --- БЛОК 1: ИМПОРТЫ И НАСТРОЙКИ ---
import os, json, logging, importlib.util, subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import psycopg
import warnings

warnings.filterwarnings("ignore", message=".*SQLAlchemy.*")

VERSION = "6.0.0"
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("crypto.trainer")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_FILE = SCRIPT_DIR.parent / "collect" / "vision_out" / "BTCUSDT_spot_1h.parquet"
MODEL_OUT_FILE = SCRIPT_DIR.parent / "data" / "models" / "best_params.json"
CONFIG_FILE = SCRIPT_DIR.parent / "data" / "intel" / "bot_config.json"

MODEL_OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
LOOKBACK_DAYS = 730 # 2 года данных

# --- БЛОК 2: ИНДИКАТОРЫ БИТКОИНА ---
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

# --- БЛОК 3: ЗАГРУЗКА МАКРО-ДАННЫХ ИЗ БАЗЫ (СВЯЗЬ С ТВОИМИ ТАБЛИЦАМИ) ---
def fetch_macro_data(start_date, end_date):
    log.info("Fetching macro data (Indices, Sentiment, Global Metrics)...")
    if not DB_URL: return pd.DataFrame()
    
    try:
        conn = psycopg.connect(DB_URL, connect_timeout=30)
        
        # 3.1. Тянем Индекс Страха и Жадности (fear_greed)
        query_fg = "SELECT timestamp, value as fear_greed FROM fear_greed WHERE timestamp >= %s ORDER BY timestamp ASC"
        df_fg = pd.read_sql(query_fg, conn, params=(start_date,))
        
        # 3.2. Тянем Глобальные Метрики (Доминация BTC)
        query_gm = "SELECT last_updated as timestamp, btc_dominance FROM global_metrics WHERE last_updated >= %s ORDER BY last_updated ASC"
        df_gm = pd.read_sql(query_gm, conn, params=(start_date,))
        
        # 3.3. Тянем Индексы (NASDAQ, DXY, S&P 500)
        query_mi = "SELECT ticker, open_time as timestamp, close FROM market_indices WHERE open_time >= %s ORDER BY open_time ASC"
        df_mi = pd.read_sql(query_mi, conn, params=(start_date,))
        
        conn.close()

        # Превращаем макро-данные в часовые свечи
        macro_frames = []
        
        if not df_fg.empty:
            df_fg['timestamp'] = pd.to_datetime(df_fg['timestamp'], utc=True)
            df_fg = df_fg.set_index('timestamp').resample('1h').ffill() # Растягиваем дневной индекс на часы
            macro_frames.append(df_fg)
            
        if not df_gm.empty:
            df_gm['timestamp'] = pd.to_datetime(df_gm['timestamp'], utc=True)
            df_gm = df_gm.set_index('timestamp').resample('1h').ffill()
            macro_frames.append(df_gm)
            
        if not df_mi.empty:
            df_mi['timestamp'] = pd.to_datetime(df_mi['timestamp'], utc=True)
            # Разворачиваем таблицу, чтобы тикеры стали колонками (nasdaq, dxy и т.д.)
            df_mi = df_mi.pivot(index='timestamp', columns='ticker', values='close')
            df_mi = df_mi.resample('1h').ffill() # Заполняем ночные пропуски последней ценой
            # Переименуем колонки для удобства
            df_mi = df_mi.rename(columns={'^IXIC': 'nasdaq', 'DX-Y.NYB': 'dxy', '^GSPC': 'sp500'})
            macro_frames.append(df_mi)

        # Склеиваем все макро-данные в один датафрейм
        if macro_frames:
            macro_df = pd.concat(macro_frames, axis=1)
            return macro_df
        return pd.DataFrame()
        
    except Exception as e:
        log.error(f"Failed to fetch macro data: {e}")
        return pd.DataFrame()

# --- БЛОК 4: ЗАГРУЗКА СВЕЖИХ СВЕЧЕЙ BTC ---
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
        return pd.DataFrame()

# --- БЛОК 5: ЗАГРУЗКА МОДЕЛЕЙ ---
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
        except Exception as e:
            log.error(f"Failed to load model {model_name}: {e}")
    return models

# --- БЛОК 6: ГЛАВНАЯ ФУНКЦИЯ ---
def main():
    log.info(f"STARTING WEEKLY TRAINER ORCHESTRATOR v{VERSION}")
    
    if not DATA_FILE.exists(): return log.error(f"DATA FILE NOT FOUND: {DATA_FILE}")

    log.info("Loading historical BTC data...")
    df_hist = pd.read_parquet(DATA_FILE)
    df_hist['open_time'] = df_hist['open_time'].apply(lambda x: x / 1000 if x > 1e14 else x)
    df_hist['open_time'] = pd.to_datetime(df_hist['open_time'], unit='ms', utc=True)
    
    if 'taker_buy_volume' not in df_hist.columns: df_hist['taker_buy_volume'] = df_hist['volume'] * 0.5
    if 'count' not in df_hist.columns: df_hist['count'] = 100
    
    df_fresh = fetch_fresh_candles()
    df_btc = pd.concat([df_hist, df_fresh]) if not df_fresh.empty else df_hist
    df_btc = df_btc.drop_duplicates(subset=['open_time']).sort_values('open_time').reset_index(drop=True)
    
    max_date = df_btc['open_time'].max()
    cutoff_date = max_date - pd.Timedelta(days=LOOKBACK_DAYS)
    df_btc = df_btc[df_btc['open_time'] >= cutoff_date].reset_index(drop=True)
    
    log.info(f"BTC Data window: {df_btc['open_time'].min().strftime('%Y-%m-%d')} to {df_btc['open_time'].max().strftime('%Y-%m-%d')}")
    
    # Считаем индикаторы Биткоина
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
    
    # --- БЛОК 7: СКЛЕЙКА (MERGE) БИТКОИНА И МАКРО-ДАННЫХ ---
    macro_df = fetch_macro_data(cutoff_date, max_date)
    if not macro_df.empty:
        # Соединяем по времени (open_time). Если макро-данных за какой-то час нет, заполняем предыдущими.
        df_btc = df_btc.set_index('open_time')
        df = df_btc.join(macro_df, how='left').ffill().dropna().reset_index()
        log.info("Successfully merged BTC with Macro Data!")
    else:
        log.warning("No macro data found in DB. Training on BTC candles only.")
        df = df_btc.dropna()

    models = load_models()
    old_data = {}
    if MODEL_OUT_FILE.exists():
        try:
            with open(MODEL_OUT_FILE, "r", encoding="utf-8") as f: old_data = json.load(f)
        except: pass

    out_data = {
        "trainer_version": VERSION, "updated_at": datetime.now(timezone.utc).isoformat(),
        "intraday": {"long": None, "short": None}, "swing": {"long": None, "short": None}, "all_scores": {}
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
                    log.error(f"  ❌ Model {model_name} crashed: {e}")
                    model_scores[model_name] = {"error": str(e)}
                    
            old_score = old_data.get(mode, {}).get(side, {}).get("metrics", {}).get("score", -99999)
            if best_model_data and best_score <= old_score:
                log.info(f"  🛡️ Keeping OLD model (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = old_data[mode][side]
            elif best_model_data:
                log.info(f"  🚀 Updating model! (New: {best_score}, Old: {old_score})")
                out_data[mode][side] = best_model_data
                
            out_data["all_scores"][mode][side] = model_scores

    with open(MODEL_OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    log.info(f"Saved to {MODEL_OUT_FILE}")

    try: subprocess.run(["python", "crypto/report/weekly_trainer_report.py"], check=False)
    except Exception as e: log.error(f"Report failed: {e}")

if __name__ == "__main__":
    main()