# ============================================================
# ARGUS — АВТОНОМНЫЙ ИССЛЕДОВАТЕЛЬ (v3)
# v3: pathlib, фикс datetime, защита от раздувания seen-файла
# ============================================================

import os
import sys
import json
import random
import requests
from datetime import datetime, timezone
from pathlib import Path

CONFIG = {
    "max_topics_per_run": 5,
    "max_candidates_per_topic": 4,
    "max_size_mb": 25,
    "weights": {"gaps": 0.6, "random": 0.3, "trends": 0.1},
    "base_topics": [
        "algorithmic trading", "quantitative finance", "machine learning",
        "cryptocurrency", "technical analysis", "market microstructure",
        "philosophy", "quantum mechanics", "psychology", "economics",
    ],
}

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

OBS_FILE = DATA_DIR / "observation.json"
EXPLORE_LOG = DATA_DIR / "explore_log.json"
SEEN_FILE = DATA_DIR / "explore_seen.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ВХОДНАЯ ТЕМА (от Actor)
# ============================================================
REQUESTED_TOPIC = None
if len(sys.argv) > 1:
    REQUESTED_TOPIC = sys.argv[1].strip()
if not REQUESTED_TOPIC:
    REQUESTED_TOPIC = (os.environ.get("EXPLORE_TOPIC") or "").strip() or None


def decide_topics():
    obs = load_json(OBS_FILE, {})
    seen = load_json(SEEN_FILE, {"urls": [], "topics": {}})

    if REQUESTED_TOPIC:
        print(f"🎯 Запрошена тема от Actor: {REQUESTED_TOPIC}")
        return [{"topic": REQUESTED_TOPIC, "reason": "запрос Actor"}]

    topics = []

    # 1. ПРОБЕЛЫ
    failed_words = obs.get("top_failed_words", [])
    gaps_count = int(CONFIG["max_topics_per_run"] * CONFIG["weights"]["gaps"])
    for item in failed_words[:gaps_count]:
        # Поддержка формата [word, count] или {"word": count}
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            word, count = item[0], item[1]
        elif isinstance(item, dict):
            continue # Пропускаем словари, если вдруг формат другой
        else:
            continue
            
        if count >= 2:
            topics.append({"topic": word, "reason": f"пробел: {count} запросов"})

    # 2. СЛУЧАЙНЫЕ
    random_count = int(CONFIG["max_topics_per_run"] * CONFIG["weights"]["random"])
    for t in random.sample(CONFIG["base_topics"], min(random_count, len(CONFIG["base_topics"]))):
        topics.append({"topic": t, "reason": "случайная тема"})

    # 3. ТРЕНДЫ
    trend_count = CONFIG["max_topics_per_run"] - len(topics)
    if trend_count > 0:
        for t in random.sample(CONFIG["base_topics"], min(trend_count, len(CONFIG["base_topics"]))):
            topics.append({"topic": t, "reason": "тренд"})

    unique = []
    names = set()
    for t in topics:
        if t["topic"] not in names:
            unique.append(t)
            names.add(t["topic"])
            
    return unique[:CONFIG["max_topics_per_run"]]


# ============================================================
# ИСТОЧНИКИ
# ============================================================
def search_arxiv(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{topic}", "start": 0,
                    "max_results": limit, "sortBy": "relevance"},
            timeout=20,
        )
        r.raise_for_status()
        for entry in r.text.split("<entry>")[1:]:
            try:
                title = entry.split("<title>")[1].split("</title>")[0].strip()
                title = " ".join(title.split())
                link = entry.split("<id>")[1].split("</id>")[0].strip()
                arxiv_id = link.split("/abs/")[-1]
                results.append({
                    "title": title, "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "source": "arxiv", "topic": topic, "type": "paper",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ arXiv: {e}")
    return results


def search_zenodo(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://zenodo.org/api/records",
            params={"q": topic, "size": limit, "type": "publication", "file_type": "pdf"},
            timeout=20,
        )
        r.raise_for_status()
        for hit in r.json().get("hits", {}).get("hits", []):
            try:
                title = hit.get("metadata", {}).get("title", "?")
                record_id = hit.get("id")
                pdf_url, size = None, 0
                for f in hit.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdf_url = f["links"]["self"]
                        size = f.get("size", 0)
                        break
                if pdf_url:
                    results.append({
                        "title": title, "url": pdf_url,
                        "source": "zenodo", "topic": topic,
                        "size_mb": round(size / 1024 / 1024, 1),
                        "page_url": f"https://zenodo.org/records/{record_id}",
                        "type": "book",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Zenodo: {e}")
    return results


def search_crossref(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={"query": topic, "rows": limit,
                    "filter": "type:journal-article,has-full-text:true"},
            timeout=20,
        )
        r.raise_for_status()
        for item in r.json().get("message", {}).get("items", []):
            try:
                title = item.get("title", ["?"])[0]
                pdf_url = None
                for l in item.get("link", []):
                    if l.get("content-type") == "application/pdf":
                        pdf_url = l.get("URL")
                        break
                if pdf_url:
                    results.append({
                        "title": title, "url": pdf_url,
                        "source": "crossref", "topic": topic, "type": "paper",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Crossref: {e}")
    return results


def search_semantic_scholar(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": topic, "limit": limit, "fields": "title,openAccessPdf"},
            timeout=20,
        )
        r.raise_for_status()
        for item in r.json().get("data", []):
            pdf = item.get("openAccessPdf")
            if pdf and pdf.get("url"):
                results.append({
                    "title": item.get("title", "?"), "url": pdf["url"],
                    "source": "semantic_scholar", "topic": topic, "type": "paper",
                })
    except Exception as e:
        print(f"   ⚠️ Semantic Scholar: {e}")
    return results


SOURCES = [
    ("arXiv", search_arxiv),
    ("Zenodo", search_zenodo),
    ("Crossref", search_crossref),
    ("SemanticScholar", search_semantic_scholar),
]


# ============================================================
# ГЛАВНОЕ
# ============================================================
def main():
    print("🧭 ARGUS EXPLORER")
    print("=" * 50)

    topics = decide_topics()
    print(f"\n📋 Тем: {len(topics)}")
    for t in topics:
        print(f"   • {t['topic']} ({t['reason']})")

    seen = load_json(SEEN_FILE, {"urls": [], "topics": {}})
    seen_urls = set(seen.get("urls", []))

    candidates = []
    run_seen_urls = set() # Защита от дублей внутри одного запуска

    for topic_data in topics:
        topic = topic_data["topic"]
        print(f"\n🔍 Тема: {topic}")
        for source_name, search_fn in SOURCES:
            results = search_fn(topic, limit=CONFIG["max_candidates_per_topic"])
            if results:
                print(f"   📡 {source_name}: {len(results)}")
            for r in results:
                url = r["url"]
                if url in seen_urls or url in run_seen_urls:
                    continue
                if r.get("size_mb", 0) > CONFIG["max_size_mb"]:
                    continue
                
                r["found_at"] = datetime.now(timezone.utc).isoformat()
                r["reason"] = topic_data["reason"]
                candidates.append(r)
                seen_urls.add(url)
                run_seen_urls.add(url)

    # Сохраняем seen (topics храним как список последних 10 дат, чтобы не раздувать файл)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen["urls"] = list(seen_urls)
    seen.setdefault("topics", {})
    for t in topics:
        topic_name = t["topic"]
        dates = set(seen["topics"].get(topic_name, []))
        dates.add(today)
        # Храним только последние 10 дат для каждой темы
        seen["topics"][topic_name] = sorted(list(dates))[-10:]
        
    save_json(SEEN_FILE, seen)

    # Добавляем новых кандидатов
    existing = load_json(CANDIDATES_FILE, {"candidates": [], "pending": {}})
    existing["candidates"] = existing.get("candidates", []) + candidates
    existing["generated_at"] = datetime.now(timezone.utc).isoformat()
    existing["total"] = len(existing["candidates"])
    save_json(CANDIDATES_FILE, existing)

    log = load_json(EXPLORE_LOG, {"runs": []})
    log["runs"].append({
        "time": datetime.now(timezone.utc).isoformat(),
        "topics": [t["topic"] for t in topics],
        "candidates_found": len(candidates),
    })
    if len(log["runs"]) > 100:
        log["runs"] = log["runs"][-100:]
    save_json(EXPLORE_LOG, log)

    print("\n" + "=" * 50)
    print(f"✅ Новых кандидатов: {len(candidates)}")
    print(f"📊 Всего в очереди: {existing['total']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
