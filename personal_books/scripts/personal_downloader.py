# ============================================================
# ARGUS — СКАЧИВАНИЕ ЛИЧНОЙ КНИГИ (v1)
# Скачивает PDF + переводит первые страницы в TXT
# ============================================================

import os
import sys
import json
import re
import requests
from pathlib import Path
from datetime import datetime, timezone

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
PERSONAL_DIR = REPO_ROOT / "personal_books"
CANDIDATES_FILE = DATA_DIR / "personal_candidates.json"

PERSONAL_DIR.mkdir(parents=True, exist_ok=True)

# --- Telegram ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- Переводчик ---
try:
    from translate import translate_to_ru
    TRANSLATE_AVAILABLE = True
except Exception:
    TRANSLATE_AVAILABLE = False
    def translate_to_ru(t): return t

# --- Извлечение текста из PDF ---
try:
    import PyPDF2
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception:
        pass


def safe_filename(title: str) -> str:
    """Делает безопасное имя файла из заголовка."""
    name = re.sub(r'[^\w\s-]', '', title.lower())
    name = re.sub(r'[\s]+', '_', name)
    return name[:80] or "document"


def download_pdf(url: str, dest: Path) -> bool:
    """Скачивает PDF файл."""
    try:
        r = requests.get(url, timeout=60, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ Ошибка скачивания: {e}")
        return False


def extract_text_from_pdf(pdf_path: Path, max_pages: int = 5) -> str:
    """Извлекает текст из первых N страниц PDF."""
    if not PDF_AVAILABLE:
        return ""
    try:
        text_parts = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for i, page in enumerate(reader.pages[:max_pages]):
                text = page.extract_text() or ""
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception as e:
        print(f"⚠️ Ошибка чтения PDF: {e}")
        return ""


def translate_text(text: str) -> str:
    """Переводит текст кусками (модель имеет лимит ~500 символов)."""
    if not text.strip():
        return ""
    
    # Разбиваем на предложения
    sentences = re.split(r'(?<=[.!?])\s+', text)
    translated_parts = []
    current_chunk = ""
    
    for sent in sentences:
        if len(current_chunk) + len(sent) < 450:
            current_chunk += sent + " "
        else:
            if current_chunk:
                translated_parts.append(translate_to_ru(current_chunk.strip()))
            current_chunk = sent + " "
    
    if current_chunk:
        translated_parts.append(translate_to_ru(current_chunk.strip()))
    
    return "\n\n".join(p for p in translated_parts if p)


def main():
    if len(sys.argv) < 2:
        print("Использование: python personal_downloader.py <индекс>")
        sys.exit(1)
    
    try:
        index = int(sys.argv[1])
    except ValueError:
        print("❌ Индекс должен быть числом")
        sys.exit(1)
    
    # Читаем список кандидатов
    if not CANDIDATES_FILE.exists():
        print("❌ Нет списка кандидатов. Сначала запусти personal_finder.py")
        sys.exit(1)
    
    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    
    items = candidates.get("items", [])
    if index < 0 or index >= len(items):
        print(f"❌ Индекс {index} вне диапазона (0-{len(items)-1})")
        sys.exit(1)
    
    item = items[index]
    title = item.get("title", "document")
    url = item.get("url")
    
    if not url:
        print("❌ Нет URL для скачивания")
        sys.exit(1)
    
    print(f"📥 Скачиваю: {title}")
    print(f"   URL: {url}")
    
    # Имя файла
    filename = safe_filename(title)
    pdf_path = PERSONAL_DIR / f"{filename}.pdf"
    txt_path = PERSONAL_DIR / f"{filename}.txt"
    
    # Скачиваем PDF
    if not download_pdf(url, pdf_path):
        notify(f"❌ Не удалось скачать: {title}")
        sys.exit(1)
    
    size_mb = pdf_path.stat().st_size / 1024 / 1024
    print(f"✅ PDF скачан: {pdf_path.name} ({size_mb:.1f} МБ)")
    
    # Извлекаем и переводим текст
    if PDF_AVAILABLE and TRANSLATE_AVAILABLE:
        print("📖 Извлекаю текст из первых 5 страниц...")
        original_text = extract_text_from_pdf(pdf_path, max_pages=5)
        
        if original_text.strip():
            print("🌐 Перевожу текст...")
            translated_text = translate_text(original_text[:3000])  # Лимит для скорости
            
            # Сохраняем перевод
            header = f"=== ПЕРЕВОД ПЕРВЫХ СТРАНИЦ ===\n"
            header += f"Оригинал: {item.get('title_original', title)}\n"
            header += f"Источник: {item.get('page_url', url)}\n"
            header += f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
            header += "=" * 50 + "\n\n"
            
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(header + translated_text)
            
            print(f"✅ Перевод сохранён: {txt_path.name}")
        else:
            print("⚠️ Не удалось извлечь текст (PDF может быть сканом)")
    else:
        if not PDF_AVAILABLE:
            print("⚠️ PyPDF2 не установлен — пропуск извлечения текста")
        if not TRANSLATE_AVAILABLE:
            print("⚠️ Переводчик недоступен — пропуск перевода")
    
    # Уведомление в Telegram
    msg = (
        f"✅ <b>Скачано в личную библиотеку</b>\n\n"
        f"📚 <b>{title}</b>\n"
        f"📄 {pdf_path.name} ({size_mb:.1f} МБ)\n"
    )
    if txt_path.exists():
        msg += f"🌐 Перевод: {txt_path.name}\n"
    msg += f"\n🔗 <a href=\"{item.get('page_url', url)}\">Открыть источник</a>"
    
    notify(msg)
    print("\n" + "=" * 60)
    print(f"🎉 Готово! Файлы в: {PERSONAL_DIR}")


if __name__ == "__main__":
    main()
