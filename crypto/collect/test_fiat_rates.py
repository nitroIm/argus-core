# ============================================================
# ARGUS - TEST FIAT RATES COLLECTOR
# Path: crypto/collect/test_fiat_rates.py
# ============================================================

import requests
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_fiat")

def get_cbr_usd():
    """Получает курс USD/RUB от ЦБ РФ."""
    log.info("Fetching USD/RUB from CBR...")
    try:
        url = "https://www.cbr-xml-daily.ru/daily_json.js"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        usd = data.get("Valute", {}).get("USD", {})
        current_rate = usd.get("Value")
        prev_rate = usd.get("Previous")
        
        if current_rate and prev_rate:
            change = current_rate - prev_rate
            change_pct = (change / prev_rate) * 100
            return {
                "rate": current_rate,
                "prev": prev_rate,
                "change": round(change, 2),
                "change_pct": round(change_pct, 2)
            }
    except Exception as e:
        log.error(f"CBR Error: {e}")
        return None

def get_usdt_rub():
    """Получает курс USDT/RUB от CoinGecko."""
    log.info("Fetching USDT/RUB from CoinGecko...")
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        rate = data.get("tether", {}).get("rub")
        if rate:
            return {"rate": rate}
    except Exception as e:
        log.error(f"CoinGecko Error: {e}")
        return None

def main():
    log.info("STARTING FIAT RATES TEST")
    
    usd_data = get_cbr_usd()
    usdt_data = get_usdt_rub()
    
    print("\n" + "="*40)
    print("РЕЗУЛЬТАТЫ ТЕСТА:")
    print("="*40)
    
    if usd_data:
        print(f"USD/RUB (ЦБ РФ): {usd_data['rate']:.2f} руб.")
        print(f"  Изменение: {usd_data['change']:+.2f} ({usd_data['change_pct']:+.2f}%)")
    else:
        print("USD/RUB: ❌ Ошибка получения")
        
    if usdt_data:
        print(f"USDT/RUB (CoinGecko): {usdt_data['rate']:.2f} руб.")
    else:
        print("USDT/RUB: ❌ Ошибка получения")
        
    print("="*40 + "\n")

if __name__ == "__main__":
    main()