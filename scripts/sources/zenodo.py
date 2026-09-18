# ============================================================
# ARGUS — АДАПТЕР: ZENODO
# Публичный API, без ключа
# v3: если URL уже прямой (/files/.../content) — не звать API
# ============================================================

import os
import re
import urllib.parse
import requests
from .base import Source, BOOKS_DIR


class ZenodoSource(Source):
    name = "zenodo"

    def can_handle(self, url):
        return "zenodo.org" in url

    def extract(self, url):
        pdfs = []

        # 1) Если URL — уже прямой линк на PDF, API не нужен
        if "/files/" in url and url.rstrip("/").endswith("/content"):
            return [url]
        # также ловим случай с download=1
        if "/files/" in url and "download=1" in url:
            return [url]

        # 2) Иначе — тянем метаданные записи
        try:
            if "/records/" in url:
                record_id = url.split("/records/")[1].split("/")[0].split("?")[0]
                api_url = f"https://zenodo.org/api/records/{record_id}"
                r = requests.get(api_url, timeout=60)
                r.raise_for_status()
                data = r.json()

                for f in data.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdfs.append(f["links"]["self"])
        except Exception as e:
            print(f"⚠️ Zenodo error: {e}")

        return pdfs

    # --------------------------------------------------------
    # Правильное имя файла
    # --------------------------------------------------------
    def _filename_from_url(self, url):
        record_id = None
        m = re.search(r"/records/(\d+)", url)
        if m:
            record_id = m.group(1)

        name = None
        m = re.search(r"/files/([^/]+)/content", url)
        if m:
            name = urllib.parse.unquote(m.group(1))
        else:
            m = re.search(r"/files/([^/?#]+)", url)
            if m:
                name = urllib.parse.unquote(m.group(1))

        if not name:
            name = url.split("/")[-1].split("?")[0] or "zenodo_file"

        if not name.lower().endswith(".pdf"):
            name += ".pdf"

        name = re.sub(r'[<>:"/\\|?*]', "_", name)

        if record_id:
            return f"{record_id}_{name}"
        return name

    def download(self, url, save_dir=None):
        if save_dir is None:
            save_dir = BOOKS_DIR

        try:
            os.makedirs(save_dir, exist_ok=True)

            filename = self._filename_from_url(url)
            if len(filename) > 100:
                filename = filename[:95] + ".pdf"

            filepath = os.path.join(save_dir, filename)

            if os.path.exists(filepath):
                print(f"⏭ Уже есть: {filename}")
                return filename

            # retry: 3 попытки с таймаутом 120 сек
            last_err = None
            for attempt in range(3):
                try:
                    r = requests.get(url, timeout=120, stream=True)
                    r.raise_for_status()
                    break
                except Exception as e:
                    last_err = e
                    print(f"⚠️ Попытка {attempt+1}/3: {e}")

            if last_err and 'r' not in locals():
                print(f"❌ Все попытки провалились: {last_err}")
                return None

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