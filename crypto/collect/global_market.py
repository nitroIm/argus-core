# ============================================================
# ARGUS - GLOBAL MARKETS COLLECTOR (v1.2.0)
# Path: crypto/collect/global_market.py
# ============================================================

import os
import sys
import time
import logging
from datetime import datetime, timezone
import psycopg
import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("global_market")

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()
ALERT_PATH = "crypto/data/temp/global_market.txt"

# Буфер после открытия рынка (в минутах).
# Если рынок открылся меньше OPEN_BUFFER_MIN минут назад, скрипт его пропускает,
# чтобы дать Yahoo время сформировать первую свечу. Заберём на следующем запуске cron.
OPEN_BUFFER_MIN = 30

# Справочник названий и стран
TICKER_META = {
    "^N225": {"name": "Nikkei 225", "country": "Japan"},
    "^HSI": {"name": "Hang Seng", "country": "Hong Kong"},
    "000001.SS": {"name": "Shanghai Composite", "country": "China"},
    "399001.SZ": {"name": "Shenzhen Component", "country": "China"},
    "^KS11": {"name": "KOSPI", "country": "South Korea"},
    "^TWII": {"name": "TAIEX", "country": "Taiwan"},
    "^STI": {"name": "Straits Times", "country": "Singapore"},
    "^JKSE": {"name": "IDX Composite", "country": "Indonesia"},
    "^KLSE": {"name": "KLCI", "country": "Malaysia"},
    "^SET.BK": {"name": "SET Index", "country": "Thailand"},
    "^BSESN": {"name": "BSE Sensex", "country": "India"},
    # ЕВРОПА
    "^GDAXI": {"name": "DAX", "country": "Germany"},
    "^FTSE": {"name": "FTSE 100", "country": "UK"},
    "^FCHI": {"name": "CAC 40", "country": "France"},
    # США
    "^GSPC": {"name": "S&P 500", "country": "US"},
    "^IXIC": {"name": "NASDAQ Composite", "country": "US"},
    "^VIX": {"name": "CBOE Volatility Index", "country": "US"},
    # ГЛОБАЛЬНЫЕ
    "DX-Y.NYB": {"name": "US Dollar Index", "country": "Global"},
    "GC=F": {"name": "Gold Futures", "country": "Global"},
    "CL=F": {"name": "Crude Oil Futures", "country": "Global"},
}


def get_conn():
    if not DB_URL:
        raise RuntimeError("AIVEN_DB_URL not set")
    return psycopg.connect(DB_URL, connect_timeout=30, application_name="argus-global-market")


def time_to_minutes(t):
    """'HH:MM' или datetime.time -> минуты от начала суток."""
    if isinstance(t, str):
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    # datetime.time
    return t.hour * 60 + t.minute


def is_market_open(market_row, now_utc):
    """
    Возвращает (is_open: bool, reason: str|None).
    reason = None (открыт), "weekend", "closed", "buffer", "lunch".
    """
    ticker, region, tz, open_utc, close_utc, lunch_start, lunch_end, trading_days = market_row

    current_day = now_utc.isoweekday()
    if trading_days == "1-5":
        allowed_days = [1, 2, 3, 4, 5]
    elif trading_days == "0-4":
        allowed_days = [7, 1, 2, 3, 4]
    else:
        allowed_days = [1, 2, 3, 4, 5]

    if current_day not in allowed_days:
        return False, "weekend"

    current_time = now_utc.strftime("%H:%M")

    # Грубая проверка попадания в торговое окно (как было раньше)
    if open_utc > close_utc:
        is_time_open = current_time >= open_utc or current_time < close_utc
    else:
        is_time_open = open_utc <= current_time < close_utc

    if not is_time_open:
        return False, "closed"

    # Буфер после открытия: рынок открылся только что -> пропускаем
    cur_min = time_to_minutes(current_time)
    open_min = time_to_minutes(open_utc)
    close_min = time_to_minutes(close_utc)

    if open_min <= close_min:
        # обычный внутридневной рынок
        if open_min <= cur_min < open_min + OPEN_BUFFER_MIN:
            return False, "buffer"
    else:
        # рынок через полночь (open > close)
        if cur_min >= open_min and cur_min < open_min + OPEN_BUFFER_MIN:
            return False, "buffer"
        # буфер, переходящий через 00:00
        if open_min + OPEN_BUFFER_MIN > 1440:
            end_buf = open_min + OPEN_BUFFER_MIN - 1440
            if cur_min < end_buf:
                return False, "buffer"

    if lunch_start and lunch_end:
        if lunch_start <= current_time < lunch_end:
            return False, "lunch"

    return True, None


def classify_error(err_text):
    """Возвращает (категория, рекомендация) по тексту исключения."""
    e = (err_text or "").lower()
    if "empty dataframe" in e or "possibly delisted" in e or "no price data" in e:
        return (
            "Yahoo не отдал данные",
            "Вероятно, момент открытия или сбой Yahoo. Ждать следующий запуск; если повторится 2+ раза — проверить тикер.",
        )
    if "timeout" in e or "connection" in e or "remote" in e or "network" in e:
        return (
            "Сетевая ошибка",
            "Транзиентно. Перезапуск обычно помогает.",
        )
    if "keyerror" in e or "valueerror" in e or "typeerror" in e or "indexerror" in e:
        return (
            "Структурная ошибка данных",
            "Требуется правка кода — само не выровняется.",
        )
    if "db" in e or "psycopg" in e or "sql" in e:
        return (
            "Ошибка БД",
            "Проверить AIVEN_DB_URL и доступность БД.",
        )
    return ("Прочее", "Смотреть лог GitHub Actions.")


def write_alert(errors, checked, opened, saved):
    """Пишет файл алерта ТОЛЬКО если есть ошибки."""
    if not errors:
        return
    try:
        os.makedirs(os.path.dirname(ALERT_PATH), exist_ok=True)
        lines = []
        lines.append(f"⚠️ Global Market: {len(errors)} тикер(ов) без данных")
        lines.append("")
        for ticker, name, err in errors:
            cat, rec = classify_error(err)
            lines.append(f"{ticker} ({name}): {cat}")
            lines.append(f"  → {rec}")
        lines.append("")
        lines.append(f"Всего в списке: {checked}, открыто: {opened}, сохранено: {saved}, упало: {len(errors)}")
        with open(ALERT_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info(f"Alert file written: {ALERT_PATH}")
    except Exception as e:
        log.error(f"Failed to write alert file: {e}")


def main():
    log.info("STARTING GLOBAL MARKET COLLECTOR")
    now_utc = datetime.now(timezone.utc)
    log.info(f"Current UTC time: {now_utc.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Open buffer: {OPEN_BUFFER_MIN} min (skip markets that opened less than this ago)")

    try:
        conn = get_conn()
    except Exception as e:
        log.error(f"DB Connection Error: {e}")
        write_alert([("DB", "Aiven", f"DB Connection Error: {e}")], 0, 0, 0)
        sys.exit(1)

    has_error = False
    errors = []
    checked = 0
    opened = 0
    saved = 0

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticker, region, timezone, open_utc, close_utc, "
                "lunch_start_utc, lunch_end_utc, trading_days FROM market_hours"
            )
            markets = cur.fetchall()

        if not markets:
            log.error("No market hours found in DB!")
            write_alert([("market_hours", "DB", "No market hours found in DB")], 0, 0, 0)
            sys.exit(1)

        checked = len(markets)
        log.info(f"Checking {checked} tickers...")

        for m in markets:
            ticker = m[0]
            is_open, reason = is_market_open(m, now_utc)

            if not is_open:
                if reason == "buffer":
                    log.info(f"  ⏸️ {ticker}: Market just opened ({OPEN_BUFFER_MIN}min buffer). Skipping.")
                elif reason == "weekend":
                    log.info(f"  ⏸️ {ticker}: Weekend. Skipping.")
                elif reason == "lunch":
                    log.info(f"  ⏸️ {ticker}: Lunch break. Skipping.")
                else:
                    log.info(f"  ⏸️ {ticker}: Market closed. Skipping.")
                continue

            opened += 1
            log.info(f"  ⏳ {ticker}: Market open. Fetching...")

            try:
                df = yf.download(ticker, period="1d", interval="1h", progress=False)
                if df.empty:
                    raise Exception("Empty dataframe returned")

                open_price = float(df["Open"].values.flatten()[-1])
                high_price = float(df["High"].values.flatten()[-1])
                low_price = float(df["Low"].values.flatten()[-1])
                close_price = float(df["Close"].values.flatten()[-1])
                volume = float(df["Volume"].values.flatten()[-1]) if "Volume" in df.columns else 0.0

                open_time = df.index[-1].to_pydatetime()
                if open_time.tzinfo is None:
                    open_time = open_time.replace(tzinfo=timezone.utc)
                else:
                    open_time = open_time.astimezone(timezone.utc)

                meta = TICKER_META.get(ticker, {"name": ticker, "country": "Unknown"})

                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO market_indices (ticker, name, region, country, timezone, open_time, open, high, low, close, volume, is_open)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT DO NOTHING
                        """,
                        (ticker, meta["name"], m[1], meta["country"], m[2], open_time, open_price, high_price, low_price, close_price, volume, True),
                    )
                conn.commit()
                saved += 1
                log.info(f"  ✅ {ticker}: Saved (Time: {open_time.strftime('%H:%M')}, Close: {close_price})")

            except Exception as e:
                log.error(f"  ❌ {ticker}: Fetch/Save error - {e}")
                conn.rollback()
                has_error = True
                meta = TICKER_META.get(ticker, {"name": ticker})
                errors.append((ticker, meta["name"], str(e)))

            time.sleep(0.5)

    except Exception as e:
        log.error(f"General Error: {e}")
        has_error = True
        errors.append(("general", "Collector", str(e)))
    finally:
        conn.close()

    if has_error:
        log.error("Collector finished with errors. Triggering failure alert.")
        write_alert(errors, checked, opened, saved)
        sys.exit(1)

    log.info("DONE SUCCESSFULLY.")


if __name__ == "__main__":
    main()