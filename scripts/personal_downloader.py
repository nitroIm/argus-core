# ============================================================
# ARGUS — СКАЧИВАНИЕ ЛИЧНОЙ КНИГИ (v4 — финал)
# v4: Скачивает до 100 МБ, переводит полностью, удаляет оригинал
#     В репо остаётся только PDF перевода (экономия места)
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

# Жёсткий лимит GitHub на один файл — 100 МБ
GITHUB_HARD_LIMIT_MB = 100

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

# --- Создание PDF перевода ---
try:
    from fpdf import FPDF
    FPDF_AVAILABLE = True
except ImportError:
    FPDF_AVAILABLE = False


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
    except Exception:
        pass


def safe_filename(title: str) -> str:
    name = re.sub(r'[^\w\s-]', '', title.lower())
    name = re.sub(r'[\s]+', '_', name)
    return name[:80] or "document"


def download_pdf(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, timeout=300, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        print(f"❌ Ошибка скачивания: {e}")
        return False


def extract_full_text(pdf_path: Path) -> str:
    if not PDF_AVAILABLE:
        return ""
    try:
        text_parts = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            total_pages = len(reader.pages)
            print(f"   📖 Всего страниц: {total_pages}")
            
            for i, page in enumerate(reader.pages):
                if i % 10 == 0:
                    print(f"   ...обработано {i}/{total_pages} страниц")
                text = page.extract_text() or ""
                text_parts.append(text)
        
        full_text = "\n\n".join(text_parts)
        print(f"   ✅ Извлечено символов: {len(full_text)}")
        return full_text
    except Exception as e:
        print(f"⚠️ Ошибка чтения PDF: {e}")
        return ""


def split_into_chunks(text: str, max_chunk_size: int = 400) -> list:
    if not text:
        return []
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks = []
    current_chunk = ""
    
    for sent in sentences:
        if len(sent) > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            chunks.append(sent)
            continue
        
        if len(current_chunk) + len(sent) < max_chunk_size:
            current_chunk += sent + " "
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sent + " "
    
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    return chunks


def translate_full_text(text: str) -> str:
    if not text.strip():
        return ""
    
    chunks = split_into_chunks(text, max_chunk_size=400)
    print(f"   🌐 Переводим {len(chunks)} частей...")
    
    translated_parts = []
    for i, chunk in enumerate(chunks):
        if i % 20 == 0:
            print(f"   ...переведено {i}/{len(chunks)} частей")
        
        translated = translate_to_ru(chunk)
        if translated:
            translated_parts.append(translated)
    
    return "\n\n".join(translated_parts)


def create_translation_pdf(translated_text: str, title: str, source_url: str, dest_path: Path) -> bool:
    if not FPDF_AVAILABLE:
        txt_path = dest_path.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(f"=== ПОЛНЫЙ ПЕРЕВОД ===\n")
            f.write(f"Оригинал: {title}\n")
            f.write(f"Источник: {source_url}\n")
            f.write(f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n")
            f.write("=" * 50 + "\n\n")
            f.write(translated_text)
        print(f"⚠️ fpdf2 не установлен, сохранено как TXT: {txt_path.name}")
        return False
    
    try:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)
        pdf.add_page()
        
        # Ищем шрифт с поддержкой кириллицы
        font_path = None
        possible_fonts = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for fp in possible_fonts:
            if Path(fp).exists():
                font_path = fp
                break
        
        if font_path:
            pdf.add_font("Unicode", "", font_path, uni=True)
            pdf.set_font("Unicode", size=12)
            print(f"   ✅ Шрифт: {font_path}")
        else:
            pdf.set_font("Helvetica", size=12)
            print("   ⚠️ Шрифт с кириллицей не найден")
        
        # Заголовок
        pdf.set_font_size(16)
        pdf.multi_cell(0, 10, title, align="C")
        pdf.ln(5)
        
        # Метаданные
        pdf.set_font_size(9)
        pdf.set_text_color(100, 100, 100)
        pdf.multi_cell(0, 5, f"Перевод с английского | {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        pdf.multi_cell(0, 5, f"Источник: {source_url}")
        pdf.ln(5)
        
        # Основной текст
        pdf.set_text_color(0, 0, 0)
        pdf.set_font_size(11)
        
        paragraphs = translated_text.split("\n\n")
        for para in paragraphs:
            para = para.strip()
            if para:
                pdf.multi_cell(0, 6, para)
                pdf.ln(2)
        
        pdf.output(str(dest_path))
        print(f"   ✅ PDF перевода создан: {dest_path.name}")
        return True
        
    except Exception as e:
        print(f"⚠️ Ошибка создания PDF: {e}")
        txt_path = dest_path.with_suffix(".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(translated_text)
        return False


def main():
    if len(sys.argv) < 2:
        print("Использование: python personal_downloader.py <индекс>")
        sys.exit(1)
    
    try:
        index = int(sys.argv[1])
    except ValueError:
        print("❌ Индекс должен быть числом")
        sys.exit(1)
    
    if not CANDIDATES_FILE.exists():
        print("❌ Нет списка кандидатов")
        sys.exit(1)
    
    with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)
    
    items = candidates.get("items", [])
    if index < 0 or index >= len(items):
        print(f"❌ Индекс {index} вне диапазона")
        sys.exit(1)
    
    item = items[index]
    title = item.get("title", "document")
    url = item.get("url")
    page_url = item.get("page_url", url)
    
    if not url:
        print("❌ Нет URL")
        sys.exit(1)
    
    print(f"📥 Обрабатываю: {title}")
    print(f"   URL: {url}")
    
    filename = safe_filename(title)
    temp_pdf = PERSONAL_DIR / f"{filename}_temp.pdf"
    translation_pdf_path = PERSONAL_DIR / f"{filename}_RU.pdf"
    
    # Скачиваем во временный файл
    if not download_pdf(url, temp_pdf):
        notify(f"❌ Не удалось скачать: {title}")
        sys.exit(1)
    
    size_mb = temp_pdf.stat().st_size / 1024 / 1024
    print(f"📏 Размер PDF: {size_mb:.1f} МБ")
    
    # Проверяем лимит GitHub
    fits_in_github = size_mb <= GITHUB_HARD_LIMIT_MB
    
    if not fits_in_github:
        print(f"⚠️ PDF > 100 МБ — не влезет в GitHub, будет только ссылка")
    
    # Извлекаем и переводим ВЕСЬ текст (для любого размера)
    translation_created = False
    
    if PDF_AVAILABLE and TRANSLATE_AVAILABLE:
        print("📖 Извлекаю полный текст...")
        original_text = extract_full_text(temp_pdf)
        
        if original_text.strip():
            print("🌐 Перевожу весь текст...")
            full_translation = translate_full_text(original_text)
            
            if full_translation:
                print("📄 Создаю PDF перевода...")
                translation_created = create_translation_pdf(
                    full_translation, title, page_url, translation_pdf_path
                )
        else:
            print("⚠️ Не удалось извлечь текст (скан?)")
    else:
        print("⚠️ PyPDF2 или переводчик недоступны")
    
    # Удаляем временный оригинал — он больше не нужен
    if temp_pdf.exists():
        temp_pdf.unlink()
        print("🗑️ Оригинальный PDF удалён (экономим место в репо)")
    
    # Уведомление в Telegram
    msg = (
        f"✅ <b>Готово!</b>\n\n"
        f"📚 <b>{title}</b>\n"
        f"📏 Размер оригинала: {size_mb:.1f} МБ\n"
    )
    
    if translation_created:
        msg += f"🌐 <b>Полный перевод:</b> {translation_pdf_path.name}\n"
    
    if fits_in_github and not translation_created:
        # Редкий случай: не удалось перевести, но файл маленький — сохраняем оригинал
        final_pdf = PERSONAL_DIR / f"{filename}.pdf"
        # Нужно заново скачать, т.к. мы уже удалили temp
        if download_pdf(url, final_pdf):
            msg += f"📄 Сохранён оригинал (перевод не удался)\n"
    
    if not fits_in_github:
        msg += f"\n📥 <a href=\"{url}\">Скачать PDF на телефон</a>\n"
        msg += f"<i>(оригинал слишком большой для GitHub)</i>\n"
    
    msg += f"\n🔗 <a href=\"{page_url}\">Страница источника</a>"
    
    notify(msg)
    
    print("\n" + "=" * 60)
    print(f"🎉 Готово!")
    if translation_created:
        print(f"🌐 Перевод: {translation_pdf_path}")
    if not fits_in_github:
        print(f"📥 Ссылка на оригинал: {url}")
    print("=" * 60)


if __name__ == "__main__":
    main()
