# ============================================================
# ARGUS — БАЗОВЫЙ АДАПТЕР ИСТОЧНИКОВ
# ============================================================

import os
import requests


class Source:
    """Базовый класс для всех источников."""

    name = "base"

    def can_handle(self, url):
        """Может ли этот адаптер обработать URL?"""
        return False

    def extract(self, url):
        """Возвращает список URL на PDF-файлы. Пустой список — если ничего не найдено."""
        return []

    def download(self, url, save_dir="books"):
        """Скачивает PDF по ссылке. Возвращает имя файла или None."""
        try:
            os.makedirs(save_dir, exist_ok=True)

            # Имя файла из URL
            filename = url.split("/")[-1].split("?")[0]
            if not filename.endswith(".pdf"):
                filename = filename + ".pdf"

            # Ограничиваем длину
            if len(filename) > 100:
                filename = filename[:95] + ".pdf"

            filepath = os.path.join(save_dir, filename)

            if os.path.exists(filepath):
                print(f"⏭ Уже есть: {filename}")
                return filename

            # Скачиваем
            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()

            # Проверяем размер
            size = 0
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:  # 25 МБ — лимит GitHub
                        print(f"⚠️ {filename} больше 25 МБ, пропускаю")
                        f.close()
                        os.remove(filepath)
                        return None
                    f.write(chunk)

            print(f"✅ {filename} ({size // 1024} КБ)")
            return filename

        except Exception as e:
            print(f"❌ Ошибка скачивания {url}: {e}")
            return None