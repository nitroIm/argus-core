# ============================================================
# ARGUS — АДАПТЕР: RSS / ATOM
# Ищет PDF в фидах
# ============================================================

import re
import requests
from .base import Source


class RSSSource(Source):
    name = "rss"

    def can_handle(self, url):
        return url.endswith(".rss") or "/feed" in url or "/rss" in url

    def extract(self, url):
        """Ищет PDF-ссылки в содержимом фида."""
        pdfs = []

        try:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            text = r.text

            # Простая регулярка: ищем ссылки на PDF
            found = re.findall(r'https?://[^\s<>"\']+\.pdf', text)

            # Убираем дубликаты
            seen = set()
            for p in found:
                if p not in seen:
                    pdfs.append(p)
                    seen.add(p)

        except Exception as e:
            print(f"⚠️ RSS error: {e}")

        return pdfs