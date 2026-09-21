# ============================================================
# ARGUS — ЛИЧНЫЕ АУДИОКНИГИ
# ------------------------------------------------------------
# Из PDF в personal_books/ делает mp3 и отправляет в Telegram.
# Разбивает на сегменты по 45 минут (лимит Telegram 50 МБ).
# Временные mp3 удаляются после отправки.
# ARGUS не трогает (books/ не касается).
# ------------------------------------------------------------
# Требования: pip install gTTS PyPDF2
#             apt install ffmpeg
# ============================================================

import os
import sys
import re
import json
import shutil
import subprocess
import requests
from pathlib import Path
from datetime import datetime, timezone

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PERSONAL_DIR = REPO_ROOT / "personal_books"
TEMP_DIR = Path("/tmp/argus_audio")

PERSONAL_DIR.mkdir(parents=True, exist_ok=True)
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# --- Настройки ---
SEGMENT_MINUTES = 45          # длительность одного сегмента
GTTS_BATCH_CHARS = 1800       # символов за один вызов gTTS
TELEGRAM_LIMIT_MB = 48        # с запасом от 50

# --- Telegram ---
BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("CHAT_ID")
    or ""
).strip()


# ============================================================
# ПРОВЕРКА ЗАВИСИМОСТЕЙ
# ============================================================
def check_deps():
    errors = []

    try:
        from gtts import gTTS
        _ = gTTS
    except ImportError:
        errors.append("gTTS (pip install gTTS)")

    try:
        import PyPDF2
        _ = PyPDF2
    except ImportError:
        errors.append("PyPDF2 (pip install PyPDF2)")

    if not shutil.which("ffmpeg"):
        errors.append("ffmpeg (apt install ffmpeg)")

    return errors


# ============================================================
# TELEGRAM
# ============================================================
def notify(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[no telegram] " + text[:200])
        return
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendMessage"
        requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
            },
            timeout=15,
        )
    except Exception as e:
        print("tg: " + str(e))


def send_audio(path, caption=""):
    if not BOT_TOKEN or not CHAT_ID:
        return False
    if not path.exists():
        return False
    size_mb = path.stat().st_size / 1024 / 1024
    if size_mb > 50:
        print("  файл > 50 МБ, не отправится")
        return False
    try:
        url = "https://api.telegram.org/bot"
        url += BOT_TOKEN + "/sendAudio"
        with open(path, "rb") as f:
            files = {"audio": f}
            data = {
                "chat_id": CHAT_ID,
                "caption": caption[:1000],
                "parse_mode": "HTML",
                "title": path.stem,
            }
            r = requests.post(
                url, data=data, files=files, timeout=600,
            )
        if r.status_code == 200:
            print("  ✅ отправлено: " + path.name)
            return True
        print("  tg " + str(r.status_code))
        return False
    except Exception as e:
        print("  send_audio: " + str(e))
        return False


# ============================================================
# PDF → ТЕКСТ
# ============================================================
def extract_text(pdf_path):
    import PyPDF2
    try:
        parts = []
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            total = len(reader.pages)
            print("  страниц: " + str(total))
            for i, page in enumerate(reader.pages):
                if i % 20 == 0:
                    print("  ..." + str(i) + "/" + str(total))
                text = page.extract_text() or ""
                parts.append(text)
        full = "\n\n".join(parts)
        print("  символов: " + str(len(full)))
        return full
    except Exception as e:
        print("  pdf error: " + str(e))
        return ""


# ============================================================
# РАЗБИВКА НА СЕГМЕНТЫ
# ============================================================
def split_into_segments(text, segment_chars):
    """
    Режем текст на сегменты по segment_chars символов,
    стараясь не рвать предложения.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    segments = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= segment_chars:
            current = current + " " + sent if current else sent
        else:
            if current:
                segments.append(current)
            current = sent
    if current:
        segments.append(current)
    return segments


def split_for_gtts(text, batch_chars):
    """Мелкие куски для gTTS — чтобы не таймаутил."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    batches = []
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) + 1 <= batch_chars:
            current = current + " " + sent if current else sent
        else:
            if current:
                batches.append(current)
            current = sent
    if current:
        batches.append(current)
    return batches


# ============================================================
# TTS + СКЛЕЙКА
# ============================================================
def tts_batch_to_mp3(text, out_path):
    """Один кусок → один mp3."""
    from gtts import gTTS
    try:
        tts = gTTS(text=text, lang="ru", slow=False)
        tts.save(str(out_path))
        return True
    except Exception as e:
        print("  gtts: " + str(e))
        return False


def concat_mp3(files, out_path):
    """Склейка mp3 через ffmpeg concat demuxer."""
    if not files:
        return False
    if len(files) == 1:
        shutil.copy(str(files[0]), str(out_path))
        return True

    list_file = TEMP_DIR / "concat_list.txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for p in files:
            f.write("file '" + str(p) + "'\n")

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", str(list_file),
                "-c:a", "libmp3lame",
                "-q:a", "4",
                str(out_path),
            ],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if result.returncode != 0:
            print("  ffmpeg error:")
            print(result.stderr[-300:])
            return False
        return True
    except Exception as e:
        print("  concat: " + str(e))
        return False


def segment_to_audio(segment_text, out_mp3):
    """Сегмент → mp3 (через временные части + склейку)."""
    batches = split_for_gtts(segment_text, GTTS_BATCH_CHARS)
    print("  кусков для gTTS: " + str(len(batches)))

    temp_parts = []
    for i, batch in enumerate(batches):
        part_path = TEMP_DIR / ("part_" + str(i).zfill(4) + ".mp3")
        if not tts_batch_to_mp3(batch, part_path):
            print("  провал на куске " + str(i))
            continue
        temp_parts.append(part_path)
        if i % 10 == 0:
            print("  ...озвучено " + str(i + 1) + "/" + str(len(batches)))

    if not temp_parts:
        return False

    print("  склейка " + str(len(temp_parts)) + " частей...")
    ok = concat_mp3(temp_parts, out_mp3)

    for p in temp_parts:
        try:
            p.unlink()
        except Exception:
            pass
    return ok


# ============================================================
# СКАНИРОВАНИЕ personal_books/
# ============================================================
def list_books():
    """Возвращает список пар (pdf_ru, pdf_en) — только с префиксом."""
    if not PERSONAL_DIR.exists():
        return []

    files = sorted(PERSONAL_DIR.glob("*.pdf"))
    # Группируем по имени без _RU
    books = {}
    for f in files:
        name = f.stem
        if name.endswith("_RU"):
            base = name[:-3]
            books.setdefault(base, {})["ru"] = f
        else:
            books.setdefault(name, {})["en"] = f
    return books


# ============================================================
# ОСНОВНАЯ РАБОТА
# ============================================================
def make_audio(base_name, prefer="ru", keep=False):
    """
    Делает аудио для книги.
    prefer: 'ru' или 'en' — что озвучивать.
    keep: сохранять ли mp3 после отправки.
    """
    books = list_books()
    if base_name not in books:
        print("❌ книга не найдена: " + base_name)
        return False

    variants = books[base_name]
    if prefer == "ru" and "ru" in variants:
        pdf_path = variants["ru"]
    elif "en" in variants:
        pdf_path = variants["en"]
    elif "ru" in variants:
        pdf_path = variants["ru"]
    else:
        print("❌ нет PDF в книге")
        return False

    print("=" * 60)
    print("📖 " + base_name)
    print("   файл: " + pdf_path.name)
    print("=" * 60)

    notify("🎧 <b>Аудиокнига</b>\n" + pdf_path.name +
           "\n\nГотовлю mp3, это займёт время...")

    # 1. Извлечь текст
    text = extract_text(pdf_path)
    if not text.strip():
        notify("❌ Не удалось извлечь текст (возможно, скан)")
        return False

    # 2. Разбить на сегменты (по времени)
    chars_per_minute = 900  # оценка для русского
    segment_chars = SEGMENT_MINUTES * chars_per_minute
    segments = split_into_segments(text, segment_chars)
    print("📊 сегментов: " + str(len(segments)))

    # 3. Обработать каждый сегмент
    total = len(segments)
    sent_count = 0

    for idx, seg in enumerate(segments, 1):
        print("")
        print("🎬 сегмент " + str(idx) + "/" + str(total))
        print("   символов: " + str(len(seg)))

        out_mp3 = TEMP_DIR / (
            base_name[:50] + "_part" + str(idx).zfill(2) + ".mp3"
        )

        if not segment_to_audio(seg, out_mp3):
            print("   ❌ пропускаю")
            continue

        size_mb = out_mp3.stat().st_size / 1024 / 1024
        print("   размер: " + str(round(size_mb, 1)) + " МБ")

        caption = (
            "🎧 <b>" + base_name + "</b>\n"
            "Часть " + str(idx) + "/" + str(total) + "\n"
            + str(round(size_mb, 1)) + " МБ · ~"
            + str(SEGMENT_MINUTES) + " мин"
        )

        if send_audio(out_mp3, caption):
            sent_count += 1
        else:
            print("   ❌ не отправилось")

        if not keep:
            try:
                out_mp3.unlink()
                print("   🗑 удалено")
            except Exception:
                pass

    print("")
    print("=" * 60)
    print("Готово: " + str(sent_count) + "/" + str(total) + " частей")
    print("=" * 60)

    notify("✅ <b>Аудиокнига готова</b>\n" +
           base_name + "\n" +
           str(sent_count) + " частей отправлено")
    return True


# ============================================================
# MAIN
# ============================================================
def main():
    errs = check_deps()
    if errs:
        print("❌ Отсутствуют зависимости:")
        for e in errs:
            print("   - " + e)
        sys.exit(1)

    args = sys.argv[1:]

    if not args:
        # Показать список
        books = list_books()
        if not books:
            print("📭 В personal_books/ нет книг")
            print("Сначала используй /find и /download")
            return
        print("📚 Книги в personal_books/:")
        for i, (name, variants) in enumerate(books.items(), 1):
            flags = []
            if "ru" in variants:
                flags.append("RU")
            if "en" in variants:
                flags.append("EN")
            print("  " + str(i) + ". " + name +
                  "  [" + "+".join(flags) + "]")
        print("")
        print("Использование:")
        print("  python personal_audio.py <имя>")
        print("  python personal_audio.py <имя> --en")
        print("  python personal_audio.py <имя> --keep")
        return

    base_name = args[0]
    prefer = "ru"
    keep = False

    if "--en" in args:
        prefer = "en"
    if "--keep" in args:
        keep = True

    make_audio(base_name, prefer=prefer, keep=keep)


if __name__ == "__main__":
    main()