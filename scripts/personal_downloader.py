# ============================================================
# ARGUS - СКАЧИВАНИЕ ЛИЧНОЙ КНИГИ В GITHUB
# ------------------------------------------------------------
# Принимает URL, скачивает PDF, коммитит в personal_books/.
# Запускается из workflow personal_download.yml.
# ============================================================

import os
import re
import sys
import html
import requests
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
PERSONAL_DIR = REPO_ROOT / "personal_books"
PERSONAL_DIR.mkdir(parents=True, exist_ok=True)

URL = (os.getenv("BOOK_URL") or "").strip()
TITLE = (os.getenv("BOOK_TITLE") or "book").strip()

BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("BOT_TOKEN")
    or ""
).strip()
CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or ""
).strip()

GITHUB_HARD_LIMIT_MB = 100


def notify(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("[no tg] " + text[:200])
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
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
    except Exception as e:
        print("tg: " + str(e))


def safe_filename(title):
    name = re.sub(r"[^\w\s-]", "", title.lower())
    name = re.sub(r"\s+", "_", name)
    return name[:80] or "document"


def main():
    if not URL:
        print("ERR: BOOK_URL not set")
        notify("❌ Нет URL для скачивания")
        sys.exit(1)

    print("Title: " + TITLE)
    print("URL: " + URL)

    filename = safe_filename(TITLE) + ".pdf"
    dest = PERSONAL_DIR / filename

    if dest.exists():
        print("Already exists: " + filename)
        notify(
            "⏭ <b>Уже есть</b>\n\n"
            + html.escape(TITLE[:150])
        )
        return

    print("Downloading...")
    try:
        r = requests.get(URL, timeout=300, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
    except Exception as e:
        print("Download failed: " + str(e))
        notify(
            "❌ <b>Не скачалось</b>\n\n"
            + html.escape(TITLE[:150])
            + "\n\n<code>" + str(e)[:150] + "</code>"
        )
        sys.exit(1)

    size_mb = dest.stat().st_size / 1024 / 1024
    print("Size: " + format(size_mb, ".1f") + " MB")

    if size_mb > GITHUB_HARD_LIMIT_MB:
        dest.unlink()
        print("Too big for GitHub")
        notify(
            "⚠️ <b>Файл > 100 МБ</b>\n\n"
            + html.escape(TITLE[:150])
            + "\n\nСкачай вручную:\n" + URL
        )
        return

    print("Saved: " + filename)
    notify(
        "✅ <b>Скачано</b>\n\n"
        + html.escape(TITLE[:150])
        + "\n\n📁 " + filename
        + "\n📏 " + format(size_mb, ".1f") + " МБ"
        + "\n\nМожно сделать аудио (меню 🎵)"
    )


if __name__ == "__main__":
    main()