# ============================================================
# ARGUS — АВТОНОМНЫЙ ИССЛЕДОВАТЕЛЬ
# Сам решает, что искать. Сам ищет. Тебе — на одобрение.
# ============================================================

import os
import json
import random
import requests
from datetime import datetime


# ============================================================
# КОНФИГ
# ============================================================
CONFIG = {
    "max_topics_per_run": 5,
    "max_candidates_per_topic": 4,
    "max_size_mb": 25,
    "weights": {
        "gaps": 0.6,      # пробелы в знаниях
        "random": 0.3,    # случайные темы
        "trends": 0.1,    # новые тренды
    },
    "base_topics": [
        "algorithmic trading",
        "quantitative finance",
        "machine learning",
        "cryptocurrency",
        "technical analysis",
        "market microstructure",
        "philosophy",
        "quantum mechanics",
        "psychology",
        "economics",
    ],
}


# ============================================================
# ПУТИ
# ============================================================
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

OBS_FILE = os.path.join(DATA_DIR, "observation.json")
EXPLORE_LOG = os.path.join(DATA_DIR, "explore_log.json")
SEEN_FILE = os.path.join(DATA_DIR, "explore_seen.json")
CANDIDATES_FILE = os.path.join(DATA_DIR, "scout_candidates.json")


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
# РЕШЕНИЕ ТЕМ
# ============================================================
def decide_topics():
    """Выбирает темы для поиска в этой итерации."""
    obs = load(OBS_FILE, {})
    seen = load(SEEN_FILE, {"urls": [], "topics": {}})

    topics = []

    # ---------- 1. ПРОБЕЛЫ В ЗНАНИЯХ ----------
    failed_words = obs.get("top_failed_words", [])
    gaps_count = int(CONFIG["max_topics_per_run"] * CONFIG["weights"]["gaps"])

    for word, count in failed_words[:gaps_count]:
        if count >= 2:
            topics.append({"topic": word, "reason": f"пробел: {count} запросов"})

    # ---------- 2. СЛУЧАЙНЫЕ ----------
    random_count = int(CONFIG["max_topics_per_run"] * CONFIG["weights"]["random"])
    random_topics = random.sample(CONFIG["base_topics"], min(random_count, len(CONFIG["base_topics"])))

    for t in random_topics:
        topics.append({"topic": t, "reason": "случайная тема"})

    # ---------- 3. ТРЕНДЫ (пока — из тех же базовых) ----------
    trend_count = CONFIG["max_topics_per_run"] - len(topics)
    if trend_count > 0:
        trends = random.sample(CONFIG["base_topics"], min(trend_count, len(CONFIG["base_topics"])))
        for t in trends:
            topics.append({"topic": t, "reason": "тренд"})

    # Убираем дубликаты
    unique = []
    seen_names = set()
    for t in topics:
        if t["topic"] not in seen_names:
            unique.append(t)
            seen_names.add(t["topic"])

    return unique[:CONFIG["max_topics_per_run"]]


# ============================================================
# ИСТОЧНИКИ (API каталогов)
# ============================================================

def search_arxiv(topic, limit=3):
    """arXiv — научные статьи."""
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

        entries = r.text.split("<entry>")[1:]
        for entry in entries:
            try:
                title = entry.split("<title>")[1].split("</title>")[0].strip()
                title = " ".join(title.split())
                link = entry.split("<id>")[1].split("</id>")[0].strip()
                arxiv_id = link.split("/abs/")[-1]

                results.append({
                    "title": title,
                    "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "source": "arxiv",
                    "topic": topic,
                    "type": "paper"
                })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ arXiv: {e}")
    return results


def search_zenodo(topic, limit=3):
    """Zenodo — открытые публикации."""
    results = []
    try:
        url = "https://zenodo.org/api/records"
        params = {"q": topic, "size": limit, "type": "publication", "file_type": "pdf"}
        r = requests.get(url, params=params, timeout=20)
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
                        "size_mb": round(size / 1024 / 1024, 1),
                        "page_url": f"https://zenodo.org/records/{record_id}",
                        "type": "book"
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Zenodo: {e}")
    return results


def search_crossref(topic, limit=3):
    """Crossref — научные статьи, DOI."""
    results = []
    try:
        url = "https://api.crossref.org/works"
        params = {
            "query": topic,
            "rows": limit,
            "filter": "type:journal-article,has-full-text:true,license.url:*"
        }
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        for item in data.get("message", {}).get("items", []):
            try:
                title = item.get("title", ["?"])[0]
                link = item.get("link", [])
                pdf_url = None
                for l in link:
                    if l.get("content-type") == "application/pdf":
                        pdf_url = l.get("URL")
                        break

                if pdf_url:
                    results.append({
                        "title": title,
                        "url": pdf_url,
                        "source": "crossref",
                        "topic": topic,
                        "type": "paper"
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Crossref: {e}")
    return results


def search_openlibrary(topic, limit=3):
    """OpenLibrary — книги (но только метаданные, PDF редко)."""
    results = []
    try:
        url = "https://openlibrary.org/search.json"
        params = {"q": topic, "limit": limit}
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        for doc in data.get("docs", []):
            # OpenLibrary не отдаёт PDF напрямую
            # Но через Internet Archive иногда можно
            ia_id = None
            if doc.get("ia"):
                ia_id = doc["ia"][0]

            if ia_id:
                results.append({
                    "title": doc.get("title", "?"),
                    "url": f"https://archive.org/download/{ia_id}/{ia_id}.pdf",
                    "source": "openlibrary",
                    "topic": topic,
                    "page_url": f"https://archive.org/details/{ia_id}",
                    "type": "book"
                })
    except Exception as e:
        print(f"   ⚠️ OpenLibrary: {e}")
    return results


def search_semantic_scholar(topic, limit=3):
    """Semantic Scholar — научные статьи с открытым доступом."""
    results = []
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": topic,
            "limit": limit,
            "fields": "title,openAccessPdf"
        }
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        for item in data.get("data", []):
            pdf = item.get("openAccessPdf")
            if pdf and pdf.get("url"):
                results.append({
                    "title": item.get("title", "?"),
                    "url": pdf["url"],
                    "source": "semantic_scholar",
                    "topic": topic,
                    "type": "paper"
                })
    except Exception as e:
        print(f"   ⚠️ Semantic Scholar: {e}")
    return results


# Все источники
SOURCES = [
    ("arXiv", search_arxiv),
    ("Zenodo", search_zenodo),
    ("Crossref", search_crossref),
    ("OpenLibrary", search_openlibrary),
    ("SemanticScholar", search_semantic_scholar),
]


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    print("🧭 ARGUS EXPLORER")
    print("=" * 50)

    topics = decide_topics()
    print(f"\n📋 Тем для исследования: {len(topics)}\n")
    for t in topics:
        print(f"   • {t['topic']} ({t['reason']})")

    seen = load(SEEN_FILE, {"urls": [], "topics": {}})
    seen_urls = set(seen["urls"])

    candidates = []

    for topic_data in topics:
        topic = topic_data["topic"]
        print(f"\n🔍 Тема: {topic}")

        for source_name, search_fn in SOURCES:
            results = search_fn(topic, limit=CONFIG["max_candidates_per_topic"])
            if results:
                print(f"   📡 {source_name}: {len(results)}")

            for r in results:
                if r["url"] in seen_urls:
                    continue
                if r.get("size_mb", 0) > CONFIG["max_size_mb"]:
                    continue

                r["found_at"] = datetime.utcnow().isoformat()
                r["reason"] = topic_data["reason"]
                candidates.append(r)
                seen_urls.add(r["url"])

    # ---------- Сохранение ----------
    seen["urls"] = list(seen_urls)

    # Отмечаем, что тема обработана
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if "topics" not in seen:
        seen["topics"] = {}
    for t in topics:
        seen["topics"][t["topic"]] = seen["topics"].get(t["topic"], [])
        seen["topics"][t["topic"]].append(today)

    save(SEEN_FILE, seen)

    # ---------- Добавляем к существующим кандидатам ----------
    existing = load(CANDIDATES_FILE, {"candidates": [], "pending": {}})
    existing["candidates"] = existing.get("candidates", []) + candidates
    existing["generated_at"] = datetime.utcnow().isoformat()
    existing["total"] = len(existing["candidates"])
    save(CANDIDATES_FILE, existing)

    # ---------- Лог ----------
    log = load(EXPLORE_LOG, {"runs": []})
    log["runs"].append({
        "time": datetime.utcnow().isoformat(),
        "topics": [t["topic"] for t in topics],
        "candidates_found": len(candidates)
    })
    if len(log["runs"]) > 100:
        log["runs"] = log["runs"][-100:]
    save(EXPLORE_LOG, log)

    print("\n" + "=" * 50)
    print(f"✅ Новых кандидатов: {len(candidates)}")
    print(f"📊 Всего в очереди: {existing['total']}")
    print("=" * 50)


if __name__ == "__main__":
    main()