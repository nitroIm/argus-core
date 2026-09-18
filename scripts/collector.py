# ============================================================
# ARGUS — КОЛЛЕКТОР (v2)
# ============================================================

import os
import sys

from sources.direct_pdf import DirectPDFSource
from sources.zenodo import ZenodoSource
from sources.arxiv import ArxivSource
from sources.rss import RSSSource


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOOKS_DIR = os.path.join(REPO_ROOT, "books")
URLS_FILE = os.path.join(REPO_ROOT, "data", "collect_urls.txt")


SOURCES = [
    DirectPDFSource(),
    ZenodoSource(),
    ArxivSource(),
    RSSSource(),
]


def clean_url(url):
    """Убирает мусор в конце URL: точку, запятую, скобку, пробел."""
    url = url.strip()
    while url and url[-1] in ".,;:!?)]}>\"'":
        url = url[:-1]
    return url


def collect_from_url(url):
    url = clean_url(url)
    print(f"\n🔍 Обработка: {url}")

    handler = None
    for s in SOURCES:
        if s.can_handle(url):
            handler = s
            break

    if not handler:
        print(f"⚠️ Не найден адаптер")
        return []

    print(f"   📡 Адаптер: {handler.name}")

    pdf_urls = handler.extract(url)
    if not pdf_urls:
        print(f"   ⚠️ PDF не найдены")
        return []

    print(f"   📄 Найдено PDF: {len(pdf_urls)}")

    downloaded = []
    for pdf_url in pdf_urls[:20]:
        name = handler.download(pdf_url)
        if name:
            downloaded.append(name)

    return downloaded


if __name__ == "__main__":
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    elif os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        print("❌ Нет URL.")
        exit(1)

    print(f"📋 URL-ов: {len(urls)}")

    all_downloaded = []
    for u in urls:
        all_downloaded.extend(collect_from_url(u))

    print("\n" + "=" * 50)
    print(f"✅ Скачано: {len(all_downloaded)}")
    for name in all_downloaded:
        print(f"   • {name}")
    print("=" * 50)