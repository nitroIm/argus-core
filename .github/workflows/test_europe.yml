# ============================================================
# ARGUS - TEST EUROPE & RUSSIA MARKETS
# Path: crypto/collect/test_europe.py
# ============================================================

import sys
import logging
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_europe")

TICKERS = {
    "^GDAXI": "DAX (Germany)",
    "^FTSE": "FTSE 100 (UK)",
    "^FCHI": "CAC 40 (France)",
    "IMOEX.ME": "Индекс МосБиржи (Russia)",
    "RTSI.ME": "Индекс РТС (Russia)"
}

def test_ticker(ticker, name):
    try:
        log.info(f"Checking {ticker} ({name})...")
        data = yf.download(ticker, period="1d", progress=False)
        
        if data.empty:
            log.warning(f"⚠️ {ticker}: Нет данных (пустой ответ)")
            return
            
        close_values = data['Close'].values.flatten()
        close_price = float(close_values[-1])
        
        log.info(f"✅ {ticker}: Цена {close_price:.2f}")
    except Exception as e:
        log.error(f"❌ {ticker}: Ошибка - {str(e)}")

def main():
    log.info("STARTING EUROPE & RUSSIA MARKETS TEST")
    for ticker, name in TICKERS.items():
        test_ticker(ticker, name)
    log.info("TEST FINISHED")

if __name__ == "__main__":
    main()