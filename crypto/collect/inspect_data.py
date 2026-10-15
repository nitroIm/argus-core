import pandas as pd
import glob

def inspect():
    files = sorted(glob.glob("vision_out/crypto/collect/*.parquet"))
    if not files:
        print("❌ Нет .parquet в vision_out/crypto/collect/")
        return

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