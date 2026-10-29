# ============================================================
# ARGUS - GLOBAL CRYPTO COLLECTOR (v1.0.1)
# Path: crypto/collect/global_crypto.py
# ============================================================

import os
import sys
import logging
import requests
import psycopg
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("global_crypto")

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
CMC_API_KEY = (os.getenv("CMC_API_KEY") or "").strip()

TZ = ZoneInfo("Europe/Kaliningrad")

def get_conn():
    if not DB_URL: raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-global")

def collect_cmc(conn):
    log.info("Collecting CoinMarketCap...")
    if not CMC_API_KEY:
        raise Exception("CMC_API_KEY not set")
    
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    
    r = requests.get(url, headers=headers, timeout=15)
    if r.status_code != 200:
        raise Exception(f"CMC HTTP Error: {r.status_code}")
    
    data = r.json().get("data", {})
    
    last_updated_str = data.get("last_updated")
    if last_updated_str:
        if last_updated_str.endswith('Z'):
            last_updated_str = last_updated_str[:-1] + '+00:00'
        last_updated = datetime.fromisoformat(last_updated_str).astimezone(timezone.utc)
    else:
        last_updated = datetime.now(timezone.utc)

    quote_usd = data.get("quote", {}).get("USD", {})
    total_mcap = quote_usd.get("total_market_cap", 0)
    total_vol = quote_usd.get("total_volume_24h", 0)
    btc_dom = data.get("btc_dominance", 0)
    eth_dom = data.get("eth_dominance", 0)
    stable_mcap = quote_usd.get("stablecoin_market_cap", 0)
    defi_mcap = quote_usd.get("defi_market_cap", 0)
    
    altcoin_mcap = total_mcap - (total_mcap * btc_dom / 100) - (total_mcap * eth_dom / 100)

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO global_metrics (last_updated, btc_dominance, eth_dominance, total_market_cap, total_volume_24h, altcoin_market_cap, stablecoin_market_cap, defi_market_cap)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
        """, (last_updated, btc_dom, eth_dom, total_mcap, total_vol, altcoin_mcap, stable_mcap, defi_mcap))
    conn.commit()
    log.info("CMC OK")

def collect_defillama(conn):
    log.info("Collecting DefiLlama (Stablecoins)...")
    url = "https://stablecoins.llama.fi/stablecoins"
    
    r = requests.get(url, timeout=15)
    if r.status_code != 200:
        raise Exception(f"DefiLlama HTTP Error: {r.status_code}")
        
    data = r.json()
    pegged_assets = data.get("peggedAssets", [])
    
    targets = ["USDT", "USDC", "DAI", "FDUSD", "TUSD"]
    now_ts = datetime.now(timezone.utc)
    
    with conn.cursor() as cur:
        for asset in pegged_assets:
            if asset.get("symbol") in targets:
                sym = asset.get("symbol")
                name = asset.get("name")
                circ = asset.get("circulating", {}).get("peggedUSD", 0)
                price = asset.get("price", 0)
                peg = asset.get("pegType", "peggedUSD")
                
                cur.execute("""
                    INSERT INTO stablecoins (symbol, timestamp, name, circulating, price, peg_type)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """, (sym, now_ts, name, circ, price, peg))
    conn.commit()
    log.info("DefiLlama OK")

def collect_blockchain(conn):
    log.info("Collecting Blockchain.com...")
    charts = ["hash-rate", "difficulty", "miners-revenue", "n-transactions", "market-price"]
    
    with conn.cursor() as cur:
        for chart in charts:
            url = f"https://api.blockchain.info/charts/{chart}?timespan=1weeks&format=json"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                values = r.json().get("values", [])
                if values:
                    last_val = values[-1]
                    chart_time = datetime.fromtimestamp(last_val.get("x"), tz=timezone.utc)
                    val = last_val.get("y")
                    cur.execute("""
                        INSERT INTO btc_network (chart_time, chart_name, value)
                        VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                    """, (chart_time, chart, val))
            else:
                log.warning(f"Blockchain chart {chart} returned {r.status_code}")
    conn.commit()
    log.info("Blockchain.com OK")

def collect_fear_greed(conn):
    log.info("Collecting Alternative.me (Fear & Greed)...")
    url = "https://api.alternative.me/fng/"
    
    r = requests.get(url, timeout=15)
    if r.status_code == 200:
        data = r.json().get("data", [])
        if data:
            item = data[0]
            val = int(item.get("value"))
            cls = item.get("value_classification")
            ts = datetime.fromtimestamp(int(item.get("timestamp")), tz=timezone.utc)
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO fear_greed (timestamp, value, classification)
                    VALUES (%s, %s, %s) ON CONFLICT DO NOTHING
                """, (ts, val, cls))
            conn.commit()
    log.info("Alternative.me OK")

def main():
    now_kly = datetime.now(timezone.utc).astimezone(TZ)
    log.info(f"STARTING GLOBAL COLLECTOR | Time: {now_kly.strftime('%H:%M')} KLD")
    
    conn = get_conn()
    
    try:
        # 1. Сбор каждый час (CMC и стейблы)
        collect_cmc(conn)
        collect_defillama(conn)
        
        # 2. Утро (07:10 - 07:25) и Вечер (19:10 - 19:25)
        # Окно в 15 минут защищает от задержек запуска GitHub.
        is_morning = (now_kly.hour == 7 and 10 <= now_kly.minute < 25)
        is_evening = (now_kly.hour == 19 and 10 <= now_kly.minute < 25)
        
        if is_morning or is_evening:
            log.info("Morning/Evening window detected. Collecting network & F&G...")
            collect_blockchain(conn)
            collect_fear_greed(conn)
        else:
            log.info("Skip daily collectors (Blockchain/F&G) - not morning/evening time.")
            
    except Exception as e:
        log.error(f"CRITICAL ERROR: {e}")
        conn.close()
        sys.exit(1) # Принудительный выход с ошибкой для GitHub Actions
    
    conn.close()
    log.info("DONE SUCCESSFULLY.")

if __name__ == "__main__":
    main()