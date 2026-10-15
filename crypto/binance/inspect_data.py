import pandas as pd
import os
import glob

# 📁 УКАЖИ ЗДЕСЬ ПУТЬ К ПАПКЕ С ФАЙЛАМИ
# Если они лежат в корне репозитория, напиши "."
# Если в папке crypto/binance/data, напиши "crypto/binance/data"
DATA_DIR = "crypto/binance/data" 

def inspect_parquet_files():
    # Ищем все файлы .parquet в указанной папке
    search_path = os.path.join(DATA_DIR, "*.parquet")
    files = glob.glob(search_path)
    
    if not files:
        print(f"❌ Не найдено .parquet файлов в папке: {DATA_DIR}")
        print("Проверь переменную DATA_DIR в скрипте.")
        return
        
    for file_path in files:
        print(f"\n{'='*60}")
        print(f"📂 ФАЙЛ: {os.path.basename(file_path)}")
        print(f"{'='*60}")
        
        try:
            # Читаем Parquet
            df = pd.read_parquet(file_path)
            
            # Основная инфа
            print(f"✅ Размер: {df.shape[0]} строк, {df.shape[1]} колонок")
            
            print("\n📊 Колонки и типы данных:")
            print(df.dtypes.to_string())
            
            print("\n🔍 Первые 5 строк:")
            print(df.head().to_string())
            
            print("\n🔍 Последние 5 строк:")
            print(df.tail().to_string())
            
            # Проверка на пропуски
            print("\n📉 Пропущенные значения (NaN):")
            print(df.isnull().sum().to_string())
            
            # Если есть числовые колонки, покажем базовую статистику
            print("\n📈 Статистика по числовым колонкам:")
            print(df.describe().T.to_string())
            
        except Exception as e:
            print(f"❌ Ошибка при чтении {file_path}: {e}")

if __name__ == "__main__":
    inspect_parquet_files()