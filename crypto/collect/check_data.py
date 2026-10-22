import pandas as pd
import os

# Путь к папке с данными относительно корня репозитория
DATA_DIR = "crypto/collect/vision_out"
FILE_SPOT = os.path.join(DATA_DIR, "BTCUSDT_spot_1h.parquet")
FILE_FUT = os.path.join(DATA_DIR, "BTCUSDT_fut_1h.parquet")

def inspect_dataframe(filepath):
    filename = os.path.basename(filepath)
    print(f"\n{'='*50}")
    print(f"📊 АНАЛИЗ ФАЙЛА: {filename}")
    print(f"{'='*50}")
    
    if not os.path.exists(filepath):
        print(f"❌ ОШИБКА: Файл {filepath} не найден!")
        return

    try:
        # Читаем Parquet
        df = pd.read_parquet(filepath)
        
        # 1. Размерность
        print(f"✅ Файл успешно прочитан.")
        print(f"Размерность: {df.shape[0]} строк, {df.shape[1]} колонок")
        
        # 2. Колонки
        print(f"\nКолонки: {df.columns.tolist()}")
        
        # 3. Поиск и сортировка по времени
        time_col = None
        for col in ['time', 'timestamp', 'open_time', 'date', 'Date']:
            if col in df.columns:
                time_col = col
                break
                
        if time_col:
            print(f"\nНайдена колонка времени: '{time_col}'. Конвертируем в datetime...")
            df[time_col] = pd.to_datetime(df[time_col], unit='ms')
            # Сортируем от старых к новым
            df = df.sort_values(by=time_col).reset_index(drop=True)
        else:
            print("\n⚠️ Колонка со временем не найдена! Проверь названия колонок.")

        # 4. Первые 3 строки (самые старые данные)
        print("\n--- Первые 3 строки (Начало истории) ---")
        print(df.head(3).to_string())
        
        # 5. Последние 3 строки (самые свежие данные)
        print("\n--- Последние 3 строки (Конец истории) ---")
        print(df.tail(3).to_string())
        
        # 6. Проверка на пропуски
        print("\n--- Проверка на пропуски (NaN) ---")
        missing = df.isnull().sum()
        if missing.sum() == 0:
            print("✅ Пропусков нет. Данные полные.")
        else:
            print("⚠️ Найдены пропуски:")
            print(missing[missing > 0])
            
    except Exception as e:
        print(f"❌ ОШИБКА при чтении файла: {e}")

if __name__ == "__main__":
    print("🚀 ЗАПУСК ПРОВЕРКИ ИСТОРИЧЕСКИХ ДАННЫХ")
    inspect_dataframe(FILE_SPOT)
    # Раскомментируй строку ниже, если захочешь проверить и фьючерсы
    # inspect_dataframe(FILE_FUT)