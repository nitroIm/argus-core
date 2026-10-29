# ============================================================
# ARGUS - TEST EXTERNAL APIS
# ------------------------------------------------------------
# Проверка API без записи в БД:
# 1. CoinMarketCap (нужен ключ)
# 2. Blockchain.com
# 3. DefiLlama
# 4. Alternative.me
# ============================================================

import os
import json
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_api")

CMC_API_KEY = (os.getenv("CMC_API_KEY") or "").strip()

def test_cmc():
    log.info("=== 1. COINMARKETCAP (Global Metrics) ===")
    if not CMC_API_KEY:
        log.error("CMC_API_KEY not set")
        return
    
    url = "https://pro-api.coinmarketcap.com/v1/global-metrics/quotes/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY, "Accept": "application/json"}
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            data = r.json().get("data", {})
            btc_dom = data.get("btc_dominance", 0)
            total_mcap = data.get("quote", {}).get("USD", {}).get("total_market_cap", 0)
            stable_mcap = data.get("quote", {}).get("USD", {}).get("stablecoin_market_cap", 0)
            log.info(f"OK: BTC Dominance: {btc_dom:.2f}%")
            log.info(f"OK: Total Market Cap: ${total_mcap:,.0f}")
            log.info(f"OK: Stablecoin Cap: ${stable_mcap:,.0f}")
        else:
            log.error(f"CMC HTTP Error: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"CMC Error: {e}")


def test_blockchain():
    log.info("=== 2. BLOCKCHAIN.COM (Network) ===")
    charts = ["hash-rate", "market-price"]
    
    for chart in charts:
        url = f"https://api.blockchain.info/charts/{chart}?timespan=1weeks&format=json"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                data = r.json()
                values = data.get("values", [])
                if values:
                    last_val = values[-1]
                    log.info(f"OK: {chart} = {last_val.get('y')} (time: {last_val.get('x')})")
            else:
                log.error(f"Blockchain HTTP Error: {r.status_code}")
        except Exception as e:
            log.error(f"Blockchain Error: {e}")


def test_defillama():
    log.info("=== 3. DEFILLAMA (Stablecoins) ===")
    url = "https://stablecoins.llama.fi/stablecoins"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            pegged_assets = data.get("peggedAssets", [])
            log.info(f"OK: Found {len(pegged_assets)} stablecoins.")
            
            # Ищем USDT, USDC, DAI для примера
            targets = ["USDT", "USDC", "DAI"]
            for asset in pegged_assets:
                if asset.get("symbol") in targets:
                    name = asset.get("name")
                    circ = asset.get("circulating", {}).get("peggedUSD", 0)
                    price = asset.get("price", 0)
                    log.info(f"OK: {asset.get('symbol')} ({name}) - Circulating: ${circ:,.0f}, Price: {price}")
        else:
            log.error(f"DefiLlama HTTP Error: {r.status_code}")
    except Exception as e:
        log.error(f"DefiLlama Error: {e}")


def test_alternative_me():
    log.info("=== 4. ALTERNATIVE.ME (Fear & Greed) ===")
    url = "https://api.alternative.me/fng/"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            data = r.json()
            fng = data.get("data", [])
            if fng:
                item = fng[0]
                val = item.get("value")
                cls = item.get("value_classification")
                ts = item.get("timestamp")
                log.info(f"OK: F&G Index = {val} ({cls}) at {ts}")
        else:
            log.error(f"Alternative.me HTTP Error: {r.status_code}")
    except Exception as e:
        log.error(f"Alternative.me Error: {e}")


if __name__ == "__main__":
    log.info("STARTING API TESTS...")
    test_cmc()
    test_blockchain()
    test_defillama()
    test_alternative_me()
    log.info("ALL TESTS FINISHED.")