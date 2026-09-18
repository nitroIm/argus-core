# ============================================================
# ARGUS — АДАПТЕР: ПРЯМЫЕ PDF-ССЫЛКИ
# ============================================================

from .base import Source


class DirectPDFSource(Source):
    name = "direct_pdf"

    def can_handle(self, url):
        return url.lower().endswith(".pdf")

    def extract(self, url):
        # Прямая ссылка — она сама и есть PDF
        return [url]