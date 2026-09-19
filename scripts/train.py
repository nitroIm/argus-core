# ============================================================
# ARGUS — ЗАПУСК ОБУЧЕНИЯ (wrapper)
# Запускает всю цепочку: ingest → train → build_index
# ============================================================

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

def run_script(script_name: str):
    """Запускает скрипт и проверяет код возврата."""
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        print(f"❌ Скрипт не найден: {script_path}")
        return False
    
    print(f"\n{'=' * 60}")
    print(f"🚀 Запуск: {script_name}")
    print('=' * 60)
    
    result = subprocess.run(
        [sys.executable, str(script_path)],
        capture_output=False,  # Вывод в реальном времени
        text=True
    )
    
    if result.returncode != 0:
        print(f" {script_name} завершился с ошибкой (код {result.returncode})")
        return False
    
    print(f"✅ {script_name} завершён успешно")
    return True


def main():
    print("🧠 ARGUS — Полное обучение модели")
    print("=" * 60)
    
    # Шаг 1: Парсинг книг
    if not run_script("ingest.py"):
        print(" Остановка: ingest.py упал")
        sys.exit(1)
    
    # Шаг 2: Обучение эмбеддингов
    if not run_script("train_embeddings.py"):
        print(" Остановка: train_embeddings.py упал")
        sys.exit(1)
    
    # Шаг 3: Построение индекса
    if not run_script("build_index.py"):
        print("⛔ Остановка: build_index.py упал")
        sys.exit(1)
    
    print("\n" + "=" * 60)
    print("🎉 ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО!")
    print("=" * 60)
    print("💡 Теперь можно использовать /ask в Telegram боте")


if __name__ == "__main__":
    main()
