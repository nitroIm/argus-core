# ============================================================
# ARGUS — ПЛАНОВЫЙ РАЗВЕДЧИК ИСТОЧНИКОВ (v2)
# v2: pathlib, retry для API, безопасный парсинг, timezone
# ============================================================

import os
import sys
import json
import time
import requests
from datetime import datetime, timezone
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ---------- Темы для планового поиска ----------
TOPICS = [
    "algorithmic trading",
    "machine learning finance",
    "quantitative trading",
    "cryptocurrency analysis",
    "technical analysis",
    "market microstructure",
    "philosophy",
    "quantum mechanics",
]

# ---------- Пути от корня репо ----------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
SEEN_FILE = DATA_DIR / "scout_seen.json"

# ============================================================
# НАДЁЖНЫЙ HTTP КЛИЕНТ С РЕТРАЯМИ
# ============================================================
def get_robust_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ARGUS-Scout/1.0)"})
    return session

SESSION = get_robust_session()

# ============================================================
# УТИЛИТЫ
# ============================================================
def load(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ============================================================
# ARXIV API
# ============================================================
def search_arxiv(topic, limit=5):
    """Ищет статьи на arXiv с защитой от сбоев."""
    results = []
    try:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{topic}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance"
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()

        # Безопасный парсинг XML
        entries = r.text.split("<entry>")[1:]
        for entry in entries:
            try:
                title = entry.split("<title>")[1].split("</title>")[0].strip()
                title = " ".join(title.split()) # нормализация пробелов

                link = entry.split("<id>")[1].split("</id>")[0].strip()
                arxiv_id = link.split("/abs/")[-1]

                results.append({
                    "title": title,
                    "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "source": "arxiv",
                    "topic": topic,
                    "type": "paper",
                    "page_url": link
                })
            except Exception:
                continue # Пропускаем битые записи, не ломаем весь запрос

    except Exception as e:
        print(f"⚠️ arXiv error: {e}")

    return results

# ============================================================
# ZENODO API
# ============================================================
def search_zenodo(topic, limit=5):
    """Ищет записи в Zenodo."""
    results = []
    try:
        url = "https://zenodo.org/api/records"
        params = {
            "q": topic,
            "size": limit,
            "type": "publication",
            "file_type": "pdf"
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        for hit in data.get("hits", {}).get("hits", []):
            try:
                title = hit.get("metadata", {}).get("title", "?")
                record_id = hit.get("id")

                pdf_url = None
                size = 0
                for f in hit.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdf_url = f["links"]["self"]
                        size = f.get("size", 0)
                        break

                if pdf_url:
                    results.append({
                        "title": title,
                        "url": pdf_url,
                        "source": "zenodo",
                        "topic": topic,
                        "type": "book",
                        "size_mb": round(size / 1024 / 1024, 1),
                        "page_url": f"https://zenodo.org/records/{record_id}"
                    })
            except Exception:
                continue

    except Exception as e:
        print(f"⚠️ Zenodo error: {e}")

    return results

# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    seen = load(SEEN_FILE, {"urls": []})
    seen_urls = set(seen["urls"])

    candidates = []

    print("🔍 ARGUS SCOUT (Плановый)")
    print("=" * 50)

    for topic in TOPICS:
        print(f"\n📚 Тема: {topic}")

        # arXiv
        arxiv_results = search_arxiv(topic, limit=3)
        print(f"   arXiv: {len(arxiv_results)}")

        # Zenodo
        zenodo_results = search_zenodo(topic, limit=3)
        print(f"   Zenodo: {len(zenodo_results)}")

        for item in arxiv_results + zenodo_results:
            if item["url"] in seen_urls:
                continue

            # Фильтр по размеру (не скачиваем гигантские файлы)
            if item.get("size_mb", 0) > 25:
                continue

            item["found_at"] = datetime.now(timezone.utc).isoformat()
            candidates.append(item)
            seen_urls.add(item["url"])

    # ---------- Сохранение ----------
    seen["urls"] = list(seen_urls)
    save(SEEN_FILE, seen)
    
    # Добавляем к существующим кандидатам, а не перезаписываем
    existing = load(CANDIDATES_FILE, {"candidates": [], "total": 0})
    existing["candidates"] = existing.get("candidates", []) + candidates
    existing["generated_at"] = datetime.now(timezone.utc).isoformat()
    existing["total"] = len(existing["candidates"])
    save(CANDIDATES_FILE, existing)

    print("\n" + "=" * 50)
    print(f"✅ Найдено новых кандидатов: {len(candidates)}")
    print(f"📊 Всего в очереди на одобрение: {existing['total']}")
    print("=" * 50)

if __name__ == "__main__":
    main()
