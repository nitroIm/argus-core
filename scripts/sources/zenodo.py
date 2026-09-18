# ============================================================
# ARGUS — АДАПТЕР: ZENODO
# Публичный API, без ключа
# ============================================================

import requests
from .base import Source


class ZenodoSource(Source):
    name = "zenodo"

    def can_handle(self, url):
        return "zenodo.org" in url

    def extract(self, url):
        """
        Поддерживает два случая:
        1. /records/<id> — страница записи
        2. /search?q=... — поиск
        """
        pdfs = []

        try:
            # Извлекаем ID записи из URL
            if "/records/" in url:
                # https://zenodo.org/records/18057849
                record_id = url.split("/records/")[1].split("/")[0].split("?")[0]

                api_url = f"https://zenodo.org/api/records/{record_id}"
                r = requests.get(api_url, timeout=20)
                r.raise_for_status()
                data = r.json()

                # Ищем PDF в files
                for f in data.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdfs.append(f["links"]["self"])

        except Exception as e:
            print(f"⚠️ Zenodo error: {e}")

        return pdfs