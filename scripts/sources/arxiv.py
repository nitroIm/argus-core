# ============================================================
# ARGUS — АДАПТЕР: ARXIV
# Возвращает PDF научных статей
# ============================================================

import re
import requests
from .base import Source


class ArxivSource(Source):
    name = "arxiv"

    def can_handle(self, url):
        return "arxiv.org" in url

    def extract(self, url):
        """
        Поддерживает:
        - arxiv.org/abs/<id> — страница статьи
        - arxiv.org/pdf/<id> — прямая ссылка
        """
        pdfs = []

        try:
            # Прямая ссылка на PDF
            if "/pdf/" in url:
                if not url.endswith(".pdf"):
                    url = url + ".pdf"
                pdfs.append(url)
                return pdfs

            # Страница abs — конвертируем в PDF
            if "/abs/" in url:
                arxiv_id = url.split("/abs/")[1].split("?")[0]
                pdfs.append(f"https://arxiv.org/pdf/{arxiv_id}.pdf")

        except Exception as e:
            print(f"⚠️ arXiv error: {e}")

        return pdfs