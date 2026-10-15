import pandas as pd
import os
import glob

# Путь берем из переменной окружения, по умолчанию — vision_out
DATA_DIR = os.environ.get("DATA_DIR", "vision_out/crypto/collect")

def inspect_parquet_files():
    search_path = os.path.join(DATA_DIR, "*.parquet")
    files = glob.glob(search_path)
    
    if not files:
        print(f"❌ Не найдено .parquet в: {DATA_DIR}")
        print(f"Текущая папка: {os.getcwd()}")
        print("Содержимое vision_out/crypto/collect:")
        try:
            print(os.listdir(DATA_DIR))
        except Exception as e:
            print(f"Ошибка: {e}")
        return
        
    for file_path in files:
        file_name = os.path.basename(file_path)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)
        
        print(f"\n{'='*60}")
        print(f"📂 ФАЙЛ: {file_name}")
        print(f"💾 Размер: {file_size_mb:.2f} МБ")
        print(f"{'='*60}")
        
        try:
            df = pd.read_parquet(file_path)
            print(f"✅ Строк: {df.shape[0]}, Колонок: {df.shape[1]}")
            print("\n📊 КОЛОНКИ И ТИПЫ:")
            print(df.dtypes.to_string())
            print("\n🔍 ПЕРВЫЕ 3 СТРОКИ:")
            print(df.head(3).to_string())
            print("\n🔍 ПОСЛЕДНИЕ 3 СТРОКИ:")
            print(df.tail(3).to_string())
            print("\n📉 ПРОПУСКИ (NaN):")
            print(df.isnull().sum().to_string())
            
            time_cols = [c for c in df.columns if 'time' in c.lower() or 'date' in c.lower()]
            if time_cols:
                for col in time_cols:
                    try:
                        print(f"\n⏰ ДИАПАЗОН ({col}):")
                        print(f"   От: {df[col].min()}")
                        print(f"   До: {df[col].max()}")
                    except Exception:
                        pass
        except Exception as e:
            print(f"❌ Ошибка при чтении {file_name}: {e}")

if __name__ == "__main__":
    inspect_parquet_files()