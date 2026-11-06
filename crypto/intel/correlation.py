# ============================================================
# ARGUS - CORRELATION ANALYZER (v1.2 - Beta & Price)
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
    log.info("Fetching ALL data from DB (READ ONLY)...")
    conn = get_conn()
    dfs = []

    # 1. BTC Candles
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
            df_1h = df.resample('1h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            df_1h = df_1h.rename(columns={'close': 'BTC_Close'})
            dfs.append(df_1h[['BTC_Close']])
    except Exception as e:
        log.error(f"Failed to fetch candles: {e}")

    # 2. Market Indices
    try:
        query = "SELECT ticker, open_time, close FROM market_indices WHERE open_time > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['open_time'] = pd.to_datetime(df['open_time'], utc=True)
        df_pivot = df.pivot(index='open_time', columns='ticker', values='close').resample('1h').last()
        dfs.append(df_pivot)
    except Exception as e:
        log.error(f"Failed to fetch indices: {e}")

    # 3. Global Metrics
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
    except Exception as e:
        log.error(f"Failed to fetch global_metrics: {e}")

    # 4. Network
    try:
        query = "SELECT chart_time, chart_name, value FROM btc_network WHERE chart_time > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['chart_time'] = pd.to_datetime(df['chart_time'], utc=True)
        df_pivot = df.pivot(index='chart_time', columns='chart_name', values='value').resample('1h').last()
        dfs.append(df_pivot)
    except Exception as e:
        log.error(f"Failed to fetch btc_network: {e}")

    # 5. Fear & Greed
    try:
        query = "SELECT timestamp, value as FearGreed FROM fear_greed WHERE timestamp > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df = df.set_index('timestamp').resample('1h').last()
        dfs.append(df)
    except Exception as e:
        log.error(f"Failed to fetch fear_greed: {e}")

    # 6. Stablecoins
    try:
        query = "SELECT timestamp, symbol, circulating FROM stablecoins WHERE timestamp > NOW() - INTERVAL '90 days'"
        df = pd.read_sql(query, conn)
        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True)
        df_pivot = df.pivot(index='timestamp', columns='symbol', values='circulating').resample('1h').last()
        df_pivot = df_pivot.add_prefix('Stable_')
        dfs.append(df_pivot)
    except Exception as e:
        log.error(f"Failed to fetch stablecoins: {e}")

    conn.close()

    if not dfs: return pd.DataFrame()

    merged = pd.concat(dfs, axis=1).sort_index().ffill()
    if 'BTC_Close' in merged.columns:
        merged = merged.dropna(subset=['BTC_Close'])
    return merged

def calculate_correlations(df):
    log.info("Calculating correlations, lags, and beta...")
    results = {}
    
    if 'BTC_Close' not in df.columns:
        return results

    # Текущая цена BTC (последняя свеча)
    current_btc_price = float(df['BTC_Close'].iloc[-1])
    results["current_btc_price"] = round(current_btc_price, 2)

    # Процентные изменения (возвраты)
    returns = df.pct_change().dropna()
    if returns.empty: return results
    
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

        # Считаем Бету (волатильность BTC относительно актива)
        # Beta = Cov(BTC, Target) / Var(Target)
        beta = 0.0
        if target_returns.var() != 0:
            beta = btc_returns.cov(target_returns) / target_returns.var()

        results[target] = {
            "best_corr": round(float(best_corr), 3),
            "best_lag_h": best_lag,
            "beta": round(float(beta), 3),
            "all_lags": all_lags
        }
        
    return results

def archive_and_save(new_results):
    log.info("Saving results to JSON...")
    data = load_json(OUT_FILE, {"history": [], "archive": []})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    data["history"] = [h for h in data["history"] if h["date"] != today]
    data["history"].append({"date": today, "correlations": new_results})
    
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    fresh, old = [], []
    
    for entry in data["history"]:
        entry_date = datetime.strptime(entry["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if entry_date >= cutoff: fresh.append(entry)
        else: old.append(entry)
            
    if old:
        by_month = {}
        for entry in old:
            month = entry["date"][:7]
            if month not in by_month: by_month[month] = []
            by_month[month].append(entry["correlations"])
            
        for month, corrs in by_month.items():
            month_rec = next((a for a in data["archive"] if a["month"] == month), None)
            if not month_rec:
                month_rec = {"month": month, "avg_corr": {}}
                data["archive"].append(month_rec)
            
            for t in corrs[0].keys():
                if t == "current_btc_price": continue
                vals = [c[t]["best_corr"] for c in corrs if t in c and "best_corr" in c[t]]
                if vals: month_rec["avg_corr"][t] = round(sum(vals)/len(vals), 3)

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