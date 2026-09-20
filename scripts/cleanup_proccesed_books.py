# ============================================================
# ARGUS — ОЧИСТКА ОБУЧЕННЫХ КНИГ (v1)
# Безопасно удаляет из папки books/ только те файлы, которые 
# были успешно обработаны, проиндексированы и сохранены.
# ============================================================

import os
import sys
import json
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BOOKS_DIR = REPO_ROOT / "books"
SUMMARY_FILE = REPO_ROOT / "data" / "summary.json"
INDEX_FILE = REPO_ROOT / "data" / "faiss.index"
MODEL_DIR = REPO_ROOT / "models" / "argus-embeddings"

def main():
    print("🧹 ARGUS Cleanup: Проверка безопасности...")

    # 1. ПРОВЕРКА: Убедимся, что обучение действительно прошло успешно
    if not INDEX_FILE.exists():
        print("❌ Отмена: faiss.index не найден. Обучение не завершено.")
        sys.exit(1)
    
    if not (MODEL_DIR / "config.json").exists():
        print("❌ Отмена: модель argus-embeddings не найдена. Обучение не завершено.")
        sys.exit(1)

    if not SUMMARY_FILE.exists():
        print("❌ Отмена: data/summary.json не найден. Нечего очищать.")
        sys.exit(1)

    print("✅ Обучение подтверждено. Читаем список обработанных книг...")

    # 2. ЧТЕНИЕ: Берем точный список файлов, которые были обработаны
    try:
        with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
            summary = json.load(f)
        
        processed_books = summary.get("books", [])
        if not processed_books:
            print("ℹ️ Список обработанных книг пуст. Нечего удалять.")
            sys.exit(0)
            
    except Exception as e:
        print(f"❌ Ошибка чтения summary.json: {e}")
        sys.exit(1)

    # 3. УДАЛЕНИЕ: Удаляем только проверенные файлы
    deleted_count = 0
    for book_info in processed_books:
        filename = book_info.get("file")
        if not filename:
            continue
            
        file_path = BOOKS_DIR / filename
        
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"   🗑️ Удалено: {filename}")
                deleted_count += 1
            except Exception as e:
                print(f"   ⚠️ Не удалось удалить {filename}: {e}")
        else:
            print(f"   ⏭️ Пропущено (уже отсутствует): {filename}")

    # 4. ИТОГ
    print("=" * 50)
    print(f"✅ Очистка завершена. Удалено файлов: {deleted_count}")
    
    # Важно: мы НЕ удаляем last_train_fingerprint.txt здесь. 
    # GitHub Action сам пересоздаст его на основе нового (пустого или обновленного) состояния папки books/
    print("💡 Папка books/ готова к приему новых книг.")
    print("=" * 50)

if __name__ == "__main__":
    main()
