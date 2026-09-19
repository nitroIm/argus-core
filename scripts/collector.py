# ============================================================
# ARGUS — КОЛЛЕКТОР (v3)
# v3: защита от отсутствующих адаптеров, pathlib, валидация URL
# ============================================================

import os
import sys
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
BOOKS_DIR = REPO_ROOT / "books"
URLS_FILE = REPO_ROOT / "data" / "collect_urls.txt"

BOOKS_DIR.mkdir(parents=True, exist_ok=True)

# --- Безопасная загрузка адаптеров ---
# Если какого-то адаптера нет в sources/, просто пропускаем его
SOURCES = []

adapter_modules = [
    ("sources.direct_pdf", "DirectPDFSource"),
    ("sources.zenodo", "ZenodoSource"),
    ("sources.arxiv", "ArxivSource"),
    ("sources.rss", "RSSSource"),
]

for module_name, class_name in adapter_modules:
    try:
        module = __import__(module_name, fromlist=[class_name])
        cls = getattr(module, class_name)
        SOURCES.append(cls())
    except ImportError as e:
        print(f"⚠️ Адаптер {module_name} недоступен: {e}")
    except Exception as e:
        print(f"️ Ошибка загрузки {module_name}: {e}")

if not SOURCES:
    print("❌ Нет доступных адаптеров. Проверь папку sources/")
    sys.exit(1)

print(f"📡 Загружено адаптеров: {len(SOURCES)}")


def clean_url(url):
    """Убирает мусор в конце URL: точку, запятую, скобку, пробел."""
    url = url.strip()
    while url and url[-1] in ".,;:!?)]}>\"'":
        url = url[:-1]
    return url


def is_valid_url(url):
    """Простая проверка: URL должен начинаться с http:// или https://"""
    return url.startswith("http://") or url.startswith("https://")


def collect_from_url(url):
    url = clean_url(url)
    
    if not is_valid_url(url):
        print(f"\n⚠️ Невалидный URL: {url}")
        return []
    
    print(f"\n🔍 Обработка: {url}")

    handler = None
    for s in SOURCES:
        if s.can_handle(url):
            handler = s
            break

    if not handler:
        print(f"   ⚠️ Не найден адаптер для этого URL")
        return []

    print(f"   📡 Адаптер: {handler.name}")

    try:
        pdf_urls = handler.extract(url)
    except Exception as e:
        print(f"   ❌ Ошибка извлечения: {e}")
        return []
    
    if not pdf_urls:
        print(f"   ⚠️ PDF не найдены")
        return []

    print(f"   📄 Найдено PDF: {len(pdf_urls)}")

    downloaded = []
    for pdf_url in pdf_urls[:20]:  # Лимит 20 файлов за раз
        try:
            name = handler.download(pdf_url)
            if name:
                downloaded.append(name)
        except Exception as e:
            print(f"   ❌ Ошибка скачивания {pdf_url}: {e}")

    return downloaded


if __name__ == "__main__":
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    elif URLS_FILE.exists():
        with open(URLS_FILE, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    else:
        print("❌ Нет URL. Передай URL как аргумент или создай data/collect_urls.txt")
        sys.exit(1)

    print(f"📋 URL-ов для обработки: {len(urls)}")

    all_downloaded = []
    for u in urls:
        all_downloaded.extend(collect_from_url(u))

    print("\n" + "=" * 50)
    print(f"✅ Скачано: {len(all_downloaded)}")
    for name in all_downloaded:
        print(f"   • {name}")
    print("=" * 50)
