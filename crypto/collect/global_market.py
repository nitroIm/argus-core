# ============================================================
# ARGUS - GLOBAL MARKETS TEST (YAHOO FINANCE) FIX
# Path: crypto/collect/global_market.py
# ============================================================

import sys
import logging
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("test_yahoo")

TICKERS = [
    "^N225", "^HSI", "000001.SS", "399001.SZ", "^KS11", "^TWII",
    "^STI", "^JKSE", "^KLSE", "^SET.BK", "^VNINDEX", "^BSESN",
    "^GSPC", "^IXIC", "^VIX", "DX-Y.NYB", "GC=F", "CL=F"
]

def test_ticker(ticker):
    try:
        # Берем данные за последний день
        data = yf.download(ticker, period="1d", progress=False)
        if data.empty:
            log.warning(f"⚠️ {ticker}: Нет данных (пустой ответ)")
            return
        
        # ИСПРАВЛЕНИЕ: Принудительно превращаем таблицу в одномерный массив чисел
        close_values = data['Close'].values.flatten()
        close_price = float(close_values[-1])
        
        # Объем может быть не у всех (например, у индексов)
        volume = 0
        if 'Volume' in data.columns:
            volume_values = data['Volume'].values.flatten()
            volume = float(volume_values[-1])
        
        date = data.index[-1].strftime('%Y-%m-%d %H:%M')
        
        log.info(f"✅ {ticker}: Цена {close_price:.2f} | Объем {volume:.0f} | Время {date}")
    except Exception as e:
        log.error(f"❌ {ticker}: Ошибка - {str(e)}")

def main():
    log.info("STARTING YAHOO FINANCE TEST")
    log.info(f"Проверяем {len(TICKERS)} тикеров...")
    
    for ticker in TICKERS:
        test_ticker(ticker)
        
    log.info("TEST FINISHED")

if __name__ == "__main__":
    main()