# ============================================================
# ARGUS — БАЗОВЫЙ АДАПТЕР ИСТОЧНИКОВ
# ============================================================

import os
import requests

# Корень репозитория
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BOOKS_DIR = os.path.join(REPO_ROOT, "books")


class Source:
    name = "base"

    def can_handle(self, url):
        return False

    def extract(self, url):
        return []

    def download(self, url, save_dir=None):
        if save_dir is None:
            save_dir = BOOKS_DIR

        try:
            os.makedirs(save_dir, exist_ok=True)

            filename = url.split("/")[-1].split("?")[0]
            if not filename.endswith(".pdf"):
                filename = filename + ".pdf"
            if len(filename) > 100:
                filename = filename[:95] + ".pdf"

            filepath = os.path.join(save_dir, filename)

            if os.path.exists(filepath):
                print(f"⏭ Уже есть: {filename}")
                return filename

            r = requests.get(url, timeout=60, stream=True)
            r.raise_for_status()

            size = 0
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    size += len(chunk)
                    if size > 25 * 1024 * 1024:
                        print(f"⚠️ {filename} больше 25 МБ, пропускаю")
                        f.close()
                        os.remove(filepath)
                        return None
                    f.write(chunk)

            print(f"✅ {filename} → {filepath} ({size // 1024} КБ)")
            return filename

        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return None