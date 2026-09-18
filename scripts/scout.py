# ============================================================
# ARGUS — РАЗВЕДЧИК ИСТОЧНИКОВ
# Ищет книги в открытых каталогах по темам
# ============================================================

import os
import json
import requests
from datetime import datetime

# ---------- Темы для поиска ----------
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

# ---------- Файлы ----------
CANDIDATES_FILE = "data/scout_candidates.json"
SEEN_FILE = "data/scout_seen.json"


def load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ARXIV API
# ============================================================
def search_arxiv(topic, limit=5):
    """Ищет статьи на arXiv."""
    results = []

    try:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{topic}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance"
        }
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()

        # Простой парсинг XML
        text = r.text
        entries = text.split("<entry>")[1:]

        for entry in entries:
            try:
                title = entry.split("<title>")[1].split("</title>")[0].strip()
                title = " ".join(title.split())

                link = entry.split("<id>")[1].split("</id>")[0].strip()

                # arXiv id
                arxiv_id = link.split("/abs/")[-1]

                results.append({
                    "title": title,
                    "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "source": "arxiv",
                    "topic": topic,
                    "page_url": link
                })
            except Exception:
                continue

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
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        for hit in data.get("hits", {}).get("hits", []):
            try:
                title = hit.get("metadata", {}).get("title", "?")
                record_id = hit.get("id")

                # Ищем PDF
                pdf_url = None
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
seen = load(SEEN_FILE, {"urls": []})
seen_urls = set(seen["urls"])

candidates = []

print("🔍 ARGUS SCOUT")
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

        # Фильтр по размеру
        if item.get("size_mb", 0) > 25:
            continue

        candidates.append(item)
        seen_urls.add(item["url"])


# ---------- Сохранение ----------
seen["urls"] = list(seen_urls)
save(SEEN_FILE, seen)
save(CANDIDATES_FILE, {
    "generated_at": datetime.utcnow().isoformat(),
    "total": len(candidates),
    "candidates": candidates
})

print("\n" + "=" * 50)
print(f"✅ Новых кандидатов: {len(candidates)}")
print("=" * 50)