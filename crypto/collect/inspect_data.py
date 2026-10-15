import pandas as pd
import glob
import os

def inspect():
    # Ищем parquet везде внутри репозитория
    files = sorted(glob.glob("**/*.parquet", recursive=True))
    if not files:
        print("❌ Ни одного .parquet не найдено")
        print("Текущая папка:", os.getcwd())
        for root, dirs, fs in os.walk("."):
            if ".git" in root:
                continue
            print(f"  {root}: {fs}")
        return

    print(f"Найдено файлов: {len(files)}")
    for f in files:
        print(f"\n{'='*70}")
        print(f"📂 {f}")
        print(f"{'='*70}")
        df = pd.read_parquet(f)
        print(f"Строк: {len(df)}, Колонок: {len(df.columns)}")
        print("\nКОЛОНКИ:")
        print(df.dtypes.to_string())
        print("\nПЕРВЫЕ 3 СТРОКИ:")
        print(df.head(3).to_string())
        print("\nПОСЛЕДНИЕ 3 СТРОКИ:")
        print(df.tail(3).to_string())
        print("\nПРОПУСКИ:")
        print(df.isnull().sum().to_string())

if __name__ == "__main__":
    inspect()