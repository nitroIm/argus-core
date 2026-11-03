# ============================================================
# ARGUS - TEST FIAT RATES COLLECTOR (v1.1.0) - RAPIRA
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

def get_rapira_usdt_rub():
    """Получает курс USDT/RUB с биржи Rapira."""
    log.info("Fetching USDT/RUB from Rapira...")
    try:
        url = "https://api.rapira.net/open/market/rates"
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()
        
        # Ищем в ответе пару USDT/RUB
        for item in data.get("data", []):
            if item.get("symbol") == "USDT/RUB":
                price = item.get("close") or item.get("askPrice") or item.get("bidPrice")
                if price:
                    return {"rate": float(price)}
        
        log.warning("USDT/RUB pair not found in Rapira response")
        return None
    except Exception as e:
        log.error(f"Rapira Error: {e}")
        return None

def main():
    log.info("STARTING FIAT RATES TEST (RAPIRA)")
    
    usd_data = get_cbr_usd()
    usdt_data = get_rapira_usdt_rub()
    
    print("\n" + "="*40)
    print("РЕЗУЛЬТАТЫ ТЕСТА:")
    print("="*40)
    
    if usd_data:
        print(f"USD/RUB (ЦБ РФ): {usd_data['rate']:.2f} руб.")
        print(f"  Изменение: {usd_data['change']:+.2f} ({usd_data['change_pct']:+.2f}%)")
    else:
        print("USD/RUB: ❌ Ошибка получения")
        
    if usdt_data:
        print(f"USDT/RUB (Rapira): {usdt_data['rate']:.2f} руб.")
    else:
        print("USDT/RUB: ❌ Ошибка получения")
        
    print("="*40 + "\n")

if __name__ == "__main__":
    main()