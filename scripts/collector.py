# ============================================================
# ARGUS — КОЛЛЕКТОР
# Оркестратор: определяет источник, извлекает PDF, скачивает
# ============================================================

import os
import sys

from sources.direct_pdf import DirectPDFSource
from sources.zenodo import ZenodoSource
from sources.arxiv import ArxivSource
from sources.rss import RSSSource


# ---------- Все доступные адаптеры ----------
SOURCES = [
    DirectPDFSource(),
    ZenodoSource(),
    ArxivSource(),
    RSSSource(),
]


# ---------- URL-ы для сбора ----------
# Можно передать списком в файле data/collect_urls.txt
URLS_FILE = "data/collect_urls.txt"


def collect_from_url(url):
    """Обрабатывает один URL."""
    print(f"\n🔍 Обработка: {url}")

    # Находим подходящий адаптер
    handler = None
    for s in SOURCES:
        if s.can_handle(url):
            handler = s
            break

    if not handler:
        print(f"⚠️ Не найден адаптер для {url}")
        return []

    print(f"   📡 Адаптер: {handler.name}")

    # Извлекаем PDF-ссылки
    pdf_urls = handler.extract(url)

    if not pdf_urls:
        print(f"   ⚠️ PDF не найдены")
        return []

    print(f"   📄 Найдено PDF: {len(pdf_urls)}")

    # Скачиваем
    downloaded = []
    for pdf_url in pdf_urls[:20]:   # Не больше 20 за раз
        name = handler.download(pdf_url)
        if name:
            downloaded.append(name)

    return downloaded


# ============================================================
# ЗАПУСК
# ============================================================

if __name__ == "__main__":
    # Получаем список URL
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    elif os.path.exists(URLS_FILE):
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        print("❌ Нет URL. Передай в аргументах или создай data/collect_urls.txt")
        exit(1)

    print(f"📋 URL-ов для обработки: {len(urls)}")

    all_downloaded = []
    for u in urls:
        downloaded = collect_from_url(u)
        all_downloaded.extend(downloaded)

    print("\n" + "=" * 50)
    print(f"✅ Скачано файлов: {len(all_downloaded)}")
    if all_downloaded:
        for name in all_downloaded:
            print(f"   • {name}")
    print("=" * 50)