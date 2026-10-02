# ============================================================
# ARGUS — БАЗОВЫЙ АДАПТЕР ИСТОЧНИКОВ (v4)
# v4: + User-Agent, + проверка Content-Type,
#     + санитайз имени файла
# ============================================================

import re
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPTS_DIR.parent
BOOKS_DIR = REPO_ROOT / "books"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}

MAX_SIZE_MB = 25
TIMEOUT = 60

FILENAME_BAD = re.compile(r"[^\w.\-]+")


def safe_name(raw):
    if not raw:
        raw = "book.pdf"
    if not raw.lower().endswith(".pdf"):
        raw = raw + ".pdf"
    name = FILENAME_BAD.sub("_", raw)
    if len(name) > 95:
        name = name[:90] + ".pdf"
    return name


class Source:
    name = "base"

    def can_handle(self, url):
        return False

    def extract(self, url):
        return []

    def download(self, url, save_dir=None):
        if save_dir is None:
            save_dir = BOOKS_DIR
        else:
            save_dir = Path(save_dir)

        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            raw = url.split("/")[-1].split("?")[0]
            filename = safe_name(raw)
            filepath = save_dir / filename

            if filepath.exists():
                print(f"⏭ Уже есть: {filename}")
                return filename

            r = requests.get(
                url,
                headers=HEADERS,
                timeout=TIMEOUT,
                stream=True,
                allow_redirects=True,
            )
            r.raise_for_status()

            ctype = r.headers.get(
                "Content-Type", ""
            ).lower()
            if "text/html" in ctype:
                print(
                    f"⚠️ {filename}: сервер отдал HTML, "
                    f"не PDF (Content-Type: {ctype})"
                )
                return None

            size = 0
            max_bytes = MAX_SIZE_MB * 1024 * 1024
            with open(filepath, "wb") as f:
                for chunk in r.iter_content(
                    chunk_size=8192
                ):
                    size += len(chunk)
                    if size > max_bytes:
                        print(
                            f"⚠️ {filename} "
                            f"больше {MAX_SIZE_MB} МБ"
                        )
                        f.close()
                        if filepath.exists():
                            filepath.unlink()
                        return None
                    f.write(chunk)

            print(
                f"✅ {filename} → "
                f"{filepath} ({size // 1024} КБ)"
            )
            return filename

        except Exception as e:
            print(f"❌ Ошибка: {e}")
            return None