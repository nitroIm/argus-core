import os
import psycopg2

DATABASE_URL = os.environ.get("AIVEN_DB_URL")

def cleanup():
    if not DATABASE_URL:
        print("❌ Ошибка: переменная AIVEN_DB_URL не найдена!")
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # Удаляем таблицу healthcheck, если она есть
        cur.execute("DROP TABLE IF EXISTS healthcheck;")
        conn.commit()
        print("✅ Таблица 'healthcheck' удалена (если существовала).")
        cur.close()
        conn.close()
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    cleanup()