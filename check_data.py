import os
import sys
import psycopg

DB_URL = (os.getenv("AIVEN_DB_URL") or "").strip()

def main():
    if not DB_URL:
        print("ERR: AIVEN_DB_URL not set")
        sys.exit(1)

    try:
        # Подключаемся
        with psycopg.connect(DB_URL, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                # 1. Считаем общее количество строк
                cur.execute("SELECT COUNT(*) FROM candles;")
                count = cur.fetchone()[0]
                print(f"✅ Всего строк в таблице 'candles': {count}")

                # 2. Если данные есть, смотрим последнюю свечу
                if count > 0:
                    cur.execute("""
                        SELECT symbol, market_type, timeframe, open_time, close, created_at 
                        FROM candles 
                        ORDER BY open_time DESC 
                        LIMIT 1;
                    """)
                    row = cur.fetchone()
                    print("\n📊 Последние данные (только чтение):")
                    print(f"  Символ: {row[0]}")
                    print(f"  Рынок: {row[1]}")
                    print(f"  Таймфрейм: {row[2]}")
                    print(f"  Время свечи: {row[3]}")
                    print(f"  Цена закрытия (close): {row[4]}")
                    print(f"  Когда записано в БД: {row[5]}")
                else:
                    print("⚠️ Таблица пуста. Данные еще не поступают.")
                    
    except Exception as e:
        print(f"❌ Ошибка подключения/запроса: {e}")

if __name__ == "__main__":
    main()