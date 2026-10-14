import os
import psycopg2

DATABASE_URL = os.environ.get("AIVEN_DB_URL")

def test_connection():
    if not DATABASE_URL:
        print("❌ Ошибка: переменная AIVEN_DB_URL не найдена!")
        return

    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        # 1. Проверяем, что база отвечает
        cur.execute("SELECT version();")
        version = cur.fetchone()
        print(f"✅ Подключение успешно! Версия: {version[0][:50]}...")

        # 2. Создаём простую тестовую таблицу (не candles!)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS healthcheck (
                id SERIAL PRIMARY KEY,
                status TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        conn.commit()
        print("✅ Таблица 'healthcheck' создана (или уже есть).")

        # 3. Записываем туда одну строку
        cur.execute("INSERT INTO healthcheck (status) VALUES (%s);", ("OK",))
        conn.commit()
        print("✅ Запись в базу выполнена.")

        # 4. Читаем последнюю запись
        cur.execute("SELECT id, status, created_at FROM healthcheck ORDER BY id DESC LIMIT 1;")
        row = cur.fetchone()
        print(f"✅ Последняя запись: id={row[0]}, status={row[1]}, time={row[2]}")

        cur.close()
        conn.close()
        print("✅ Все проверки пройдены.")

    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == "__main__":
    test_connection()