# ============================================================
# ARGUS - CORRELATION ANALYZER (v1.1)
# Path: crypto/intel/correlation.py
# ============================================================

import os
import json
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np
import psycopg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("correlation")

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data" / "intel"
DATA_DIR.mkdir(parents=True, exist_ok=True)
OUT_FILE = DATA_DIR / "correlations.json"

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
HISTORY_DAYS = 30
LAG_RANGE = 24

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30)

def load_json(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def fetch_all_data():
    """Собирает абсолютно все данные из базы и приводит к часовому таймфрейму."""
    log.info("Fetching ALL data from DB...")
    conn = get_conn()
    dfs = []

    # 1. BTC Candles (15m -> resample to 1h)
    try:
        query = """
            SELECT open_time, open, high, low, close, volume 
            FROM candles 
            WHERE symbol='BTCUSDT' AND timeframe='15m' AND market_type='spot'
            AND open_time > NOW() - INTERVAL '90 days'
            ORDER BY open_time ASC
        """
        df = pd.read_sql(query, conn)
        if not df.empty:
            df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
            df = df.set_index('open_time')
            # Конвертируем 15m в 1h
            df_1h = df.resample('1h').agg({
                'open': 'first', 'high': 'max', 'low': 'min', 
                'close': 'last', 'volume': 'sum'
            }).dropna()
            df_1h = df_1h.rename(columns={'close': 'BTC_Close'})
            dfs.append(df_1h[['BTC_Close']])
            log.info(f"  Candles: {len(df_1h)} rows (1h)")
        else:
            log.error("  Candles: No data found in DB!")
    except Exception as e:
        log.error(f"Failed to fetch candles: {e}")

    # 2. Market Indices (Все тикеры)
    try:
        query = "SELECT ticker, open_time, close FROM market_indices WHERE open_time > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df_pivot = df.pivot(index='open_time', columns='ticker', values='close')
        df_pivot = df_pivot.resample('1h').last()
        dfs.append(df_pivot)
        log.info(f"  Indices: {len(df_pivot.columns)} tickers")
    except Exception as e:
        log.error(f"Failed to fetch indices: {e}")

    # 3. Global Metrics (CMC)
    try:
        query = """
            SELECT last_updated, btc_dominance, eth_dominance, total_market_cap, 
                   stablecoin_market_cap, altcoin_market_cap, defi_market_cap 
            FROM global_metrics 
            WHERE last_updated > NOW() - INTERVAL '90 days'
        """
        df = pd.read_sql(query, conn)
        df['last_updated'] = pd.to_datetime(df['last_updated'], utc=True)
        df = df.set_index('last_updated').resample('1h').last()
        dfs.append(df)
        log.info(f"  Global Metrics: {len(df)} rows")
    except Exception as e:
        log.error(f"Failed to fetch global_metrics: {e}")

    # 4. Network (Blockchain.com)
    try:
        query = "SELECT chart_time, chart_name, value FROM btc_network WHERE chart_time > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['chart_time'] = pd.to_datetime(df['chart_time'], utc=True)
        df_pivot = df.pivot(index='chart_time', columns='chart_name', values='value')
        df_pivot = df_pivot.resample('1h').last()
        dfs.append(df_pivot)
        log.info(f"  Network: {len(df_pivot.columns)} metrics")
    except Exception as e:
        log.error(f"Failed to fetch btc_network: {e}")

    # 5. Fear & Greed
    try:
        query = "SELECT timestamp, value as FearGreed FROM fear_greed WHERE timestamp > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df = df.set_index('timestamp').resample('1h').last()
        dfs.append(df)
        log.info(f"  FearGreed: {len(df)} rows")
    except Exception as e:
        log.error(f"Failed to fetch fear_greed: {e}")

    # 6. Stablecoins
    try:
        query = "SELECT timestamp, symbol, circulating FROM stablecoins WHERE timestamp > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df_pivot = df.pivot(index='timestamp', columns='symbol', values='circulating')
        df_pivot = df_pivot.resample('1h').last()
        df_pivot = df_pivot.add_prefix('Stable_')
        dfs.append(df_pivot)
        log.info(f"  Stablecoins: {len(df_pivot.columns)} assets")
    except Exception as e:
        log.error(f"Failed to fetch stablecoins: {e}")

    conn.close()

    if not dfs:
        return pd.DataFrame()

    # Объединяем всё по времени
    log.info("Merging and aligning data...")
    merged = pd.concat(dfs, axis=1)
    
    # Сортируем по времени
    merged = merged.sort_index()
    
    # Заполняем пропуски вперед (для выходных на фондовых рынках)
    merged = merged.ffill()
    
    # Удаляем строки, где нет BTC
    if 'BTC_Close' in merged.columns:
        merged = merged.dropna(subset=['BTC_Close'])
    else:
        log.error("BTC_Close missing after merge!")
    
    return merged

def calculate_correlations(df):
    """Считает корреляцию и ищет лаги (Lead-Lag)."""
    log.info("Calculating correlations and lags...")
    results = {}
    
    if 'BTC_Close' not in df.columns:
        log.error("BTC_Close not found in data!")
        return results

    # Логарифмические возвраты
    returns = df.pct_change().dropna()
    if returns.empty: 
        return results
    
    btc_returns = returns['BTC_Close']
    targets = [c for c in returns.columns if c != 'BTC_Close']

    for target in targets:
        target_returns = returns[target]
        
        best_corr = 0
        best_lag = 0
        all_lags = {}

        # Ищем лаг от -24 до +24 часов
        for lag in range(-LAG_RANGE, LAG_RANGE + 1):
            corr = btc_returns.corr(target_returns.shift(lag))
            
            if pd.notna(corr):
                all_lags[str(lag)] = round(float(corr), 3)
                if abs(corr) > abs(best_corr):
                    best_corr = corr
                    best_lag = lag

        results[target] = {
            "best_corr": round(float(best_corr), 3),
            "best_lag_h": best_lag,
            "all_lags": all_lags
        }
        
    return results

def archive_and_save(new_results):
    """Сохраняет данные, детально за 30 дней, остальное сжимает в месячный архив."""
    log.info("Saving results...")
    
    data = load_json(OUT_FILE, {"history": [], "archive": []})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Если запускали сегодня, перезаписываем запись за сегодня
    data["history"] = [h for h in data["history"] if h["date"] != today]
    data["history"].append({
        "date": today,
        "correlations": new_results
    })
    
    # Оставляем детально только 30 дней
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    fresh = []
    old = []
    
    for entry in data["history"]:
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if entry_date >= cutoff:
            fresh.append(entry)
        else:
            old.append(entry)
            
    # Сжимаем старые в месячный архив
    if old:
        by_month = {}
        for entry in old:
            month = entry["date"][:7] # YYYY-MM
            if month not in by_month:
                by_month[month] = []
            by_month[month].append(entry["correlations"])
            
        for month, corrs in by_month.items():
            month_rec = next((a for a in data["archive"] if a["month"] == month), None)
            if not month_rec:
                month_rec = {"month": month, "avg_corr": {}}
                data["archive"].append(month_rec)
            
            targets = corrs[0].keys()
            for t in targets:
                vals = [c[t]["best_corr"] for c in corrs if t in c]
                if vals:
                    month_rec["avg_corr"][t] = round(sum(vals)/len(vals), 3)

    data["history"] = fresh
    save_json(OUT_FILE, data)
    log.info(f"Saved. History: {len(fresh)} days, Archive: {len(data['archive'])} months.")

def main():
    log.info("STARTING CORRELATION ANALYZER")
    try:
        df = fetch_all_data()
        if df.empty:
            log.error("No data to analyze.")
            return
            
        results = calculate_correlations(df)
        if results:
            archive_and_save(results)
            log.info("Correlation analysis complete.")
        else:
            log.error("Failed to calculate correlations.")
    except Exception as e:
        log.error(f"Critical error: {e}")

if __name__ == "__main__":
    main()