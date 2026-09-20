# ============================================================
# ARGUS — ОЧИСТКА ОБУЧЕННЫХ КНИГ
# Удаляет из books/ только те файлы, что успешно обработаны
# ============================================================

import sys
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BOOKS_DIR = REPO_ROOT / "books"
SUMMARY_FILE = REPO_ROOT / "data" / "summary.json"
INDEX_FILE = REPO_ROOT / "data" / "faiss.index"


def main():
    print("🧹 Проверка безопасности перед очисткой...")

    if not INDEX_FILE.exists():
        print("❌ Отмена: faiss.index не найден — обучение не завершено.")
        sys.exit(0)

    if not SUMMARY_FILE.exists():
        print("❌ Отмена: summary.json не найден.")
        sys.exit(0)

    try:
        with open(SUMMARY_FILE, "r", encoding="utf-8") as f:
            summary = json.load(f)
        processed = summary.get("books", [])
    except Exception as e:
        print(f"❌ Ошибка чтения summary.json: {e}")
        sys.exit(0)

    if not processed:
        print("ℹ️ Список обработанных книг пуст.")
        sys.exit(0)

    print(f"✅ Обучение подтверждено. Удаляю {len(processed)} файлов из books/...")

    deleted = 0
    for book_info in processed:
        filename = book_info.get("file")
        if not filename:
            continue
        file_path = BOOKS_DIR / filename
        if file_path.exists():
            try:
                file_path.unlink()
                print(f"   🗑️ Удалено: {filename}")
                deleted += 1
            except Exception as e:
                print(f"   ⚠️ Не удалось удалить {filename}: {e}")
        else:
            print(f"   ⏭️ Уже отсутствует: {filename}")

    print(f"✅ Очистка завершена. Удалено: {deleted}")


if __name__ == "__main__":
    main()
