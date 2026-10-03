# ============================================================
# ARGUS — SCOUT v3 [PLANNED SEARCH]
# ------------------------------------------------------------
# v3: 7 источников, чистые темы, лимит 90MB, English logs,
#     pathlib, timezone-aware, retry.
# ------------------------------------------------------------
# Sources: arXiv, Zenodo, Crossref, OpenAlex,
#          Semantic Scholar, CORE, DOAJ
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

# ---------- Topics (only trading/crypto/ML) ----------
TOPICS = [
    "algorithmic trading",
    "machine learning finance",
    "quantitative trading",
    "cryptocurrency analysis",
    "technical analysis",
    "market microstructure",
    "order flow",
    "risk management",
]

MAX_SIZE_MB = 90
PER_SOURCE_LIMIT = 3

# ---------- Paths ----------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"
SEEN_FILE = DATA_DIR / "scout_seen.json"


# ============================================================
# HTTP CLIENT WITH RETRY
# ============================================================
def get_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "ARGUS-Scout/3.0 (research bot)"
    })
    return session


SESSION = get_session()


# ============================================================
# HELPERS
# ============================================================
def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


def load(path, default):
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def is_pdf_size_ok(size_bytes):
    if not size_bytes:
        return True
    mb = size_bytes / 1024 / 1024
    return mb <= MAX_SIZE_MB


def clean(title):
    if not title:
        return ""
    return " ".join(title.split()).strip()


# ============================================================
# ARXIV
# ============================================================
def search_arxiv(topic, limit):
    results = []
    try:
        url = "http://export.arxiv.org/api/query"
        params = {
            "search_query": f"all:{topic}",
            "start": 0,
            "max_results": limit,
            "sortBy": "relevance",
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        entries = r.text.split("<entry>")[1:]
        for entry in entries:
            try:
                t = entry.split("<title>")[1]
                title = clean(t.split("</title>")[0])
                lnk = entry.split("<id>")[1]
                abs_url = lnk.split("</id>")[0].strip()
                aid = abs_url.split("/abs/")[-1]
                results.append({
                    "title": title,
                    "url": f"https://arxiv.org/pdf/{aid}.pdf",
                    "source": "arxiv",
                    "topic": topic,
                    "type": "paper",
                    "page_url": abs_url,
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  arxiv error: {e}")
    return results


# ============================================================
# ZENODO
# ============================================================
def search_zenodo(topic, limit):
    results = []
    try:
        url = "https://zenodo.org/api/records"
        params = {
            "q": topic,
            "size": limit,
            "type": "publication",
            "file_type": "pdf",
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for hit in data.get("hits", {}).get("hits", []):
            try:
                meta = hit.get("metadata", {})
                title = clean(meta.get("title", "?"))
                rec_id = hit.get("id")
                pdf_url = None
                size = 0
                for f in hit.get("files", []):
                    key = f.get("key", "").lower()
                    if key.endswith(".pdf"):
                        pdf_url = f["links"]["self"]
                        size = f.get("size", 0)
                        break
                if not pdf_url:
                    continue
                if not is_pdf_size_ok(size):
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "zenodo",
                    "topic": topic,
                    "type": "book",
                    "size_mb": round(size / 1024 / 1024, 1),
                    "page_url":
                        f"https://zenodo.org/records/{rec_id}",
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  zenodo error: {e}")
    return results


# ============================================================
# CROSSREF
# ============================================================
def search_crossref(topic, limit):
    results = []
    try:
        url = "https://api.crossref.org/works"
        params = {
            "query": topic,
            "rows": limit,
            "filter": "type:journal-article",
            "select": "title,DOI,link,URL",
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for item in data.get("message", {}).get("items", []):
            try:
                title_arr = item.get("title", [])
                if not title_arr:
                    continue
                title = clean(title_arr[0])
                pdf_url = None
                for lnk in item.get("link", []):
                    ct = lnk.get("content-type", "")
                    if "pdf" in ct.lower():
                        pdf_url = lnk.get("URL")
                        break
                if not pdf_url:
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "crossref",
                    "topic": topic,
                    "type": "paper",
                    "page_url":
                        item.get("URL", ""),
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  crossref error: {e}")
    return results


# ============================================================
# OPENALEX
# ============================================================
def search_openalex(topic, limit):
    results = []
    try:
        url = "https://api.openalex.org/works"
        params = {
            "search": topic,
            "per_page": limit,
            "filter": "is_oa:true,type:article",
        }
        r = SESSION.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        for item in data.get("results", []):
            try:
                title = clean(item.get("title", ""))
                if not title:
                    continue
                oa = item.get("open_access", {})
                pdf_url = oa.get("oa_url")
                if not pdf_url:
                    continue
                if not pdf_url.lower().endswith(".pdf"):
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "openalex",
                    "topic": topic,
                    "type": "paper",
                    "page_url":
                        item.get("doi", "") or item.get("id", ""),
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  openalex error: {e}")
    return results


# ============================================================
# SEMANTIC SCHOLAR
# ============================================================
def search_semantic(topic, limit):
    results = []
    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": topic,
            "limit": limit,
            "fields": "title,openAccessPdf,url,abstract",
        }
        r = SESSION.get(url, params=params, timeout=20)
        if r.status_code == 429:
            log("  semantic: rate limited (429)")
            return []
        r.raise_for_status()
        data = r.json()
        for item in data.get("data", []):
            try:
                title = clean(item.get("title", ""))
                oap = item.get("openAccessPdf") or {}
                pdf_url = oap.get("url")
                if not title or not pdf_url:
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "semantic",
                    "topic": topic,
                    "type": "paper",
                    "page_url": item.get("url", ""),
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  semantic error: {e}")
    return results


# ============================================================
# CORE (needs API key)
# ============================================================
def search_core(topic, limit):
    results = []
    api_key = os.getenv("CORE_API_KEY")
    if not api_key:
        log("  core: no CORE_API_KEY, skipped")
        return []
    try:
        url = "https://api.core.ac.uk/v3/search/works"
        headers = {"Authorization": f"Bearer {api_key}"}
        params = {"q": topic, "limit": limit}
        r = SESSION.get(
            url, headers=headers, params=params, timeout=20
        )
        r.raise_for_status()
        data = r.json()
        for item in data.get("results", []):
            try:
                title = clean(item.get("title", ""))
                pdf_url = item.get("downloadUrl")
                if not title or not pdf_url:
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "core",
                    "topic": topic,
                    "type": "paper",
                    "page_url": item.get("doi", ""),
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  core error: {e}")
    return results


# ============================================================
# DOAJ
# ============================================================
def search_doaj(topic, limit):
    results = []
    try:
        url = "https://doaj.org/api/search/articles"
        safe = topic.replace(" ", "%20")
        full = f"{url}/{safe}?pageSize={limit}"
        r = SESSION.get(full, timeout=20)
        r.raise_for_status()
        data = r.json()
        for item in data.get("results", []):
            try:
                bib = item.get("bibjson", {})
                title = clean(bib.get("title", ""))
                if not title:
                    continue
                pdf_url = None
                for lnk in bib.get("link", []):
                    if lnk.get("type") == "fulltext":
                        pdf_url = lnk.get("url")
                        break
                if not pdf_url:
                    continue
                results.append({
                    "title": title,
                    "url": pdf_url,
                    "source": "doaj",
                    "topic": topic,
                    "type": "paper",
                    "page_url": pdf_url,
                })
            except Exception:
                continue
    except Exception as e:
        log(f"  doaj error: {e}")
    return results


# ============================================================
# SOURCE REGISTRY
# ============================================================
SOURCES = [
    ("arxiv", search_arxiv),
    ("zenodo", search_zenodo),
    ("crossref", search_crossref),
    ("openalex", search_openalex),
    ("semantic", search_semantic),
    ("core", search_core),
    ("doaj", search_doaj),
]


# ============================================================
# MAIN
# ============================================================
def main():
    seen_data = load(SEEN_FILE, {"urls": []})
    seen_urls = set(seen_data.get("urls", []))

    candidates = []

    log("ARGUS SCOUT v3 (planned)")
    log("=" * 50)

    for topic in TOPICS:
        log(f"\nTopic: {topic}")

        for name, fn in SOURCES:
            try:
                items = fn(topic, PER_SOURCE_LIMIT)
                log(f"  {name}: {len(items)}")
            except Exception as e:
                log(f"  {name} fatal: {e}")
                items = []

            for item in items:
                url = item.get("url", "")
                if not url or url in seen_urls:
                    continue
                item["found_at"] = utc_now()
                candidates.append(item)
                seen_urls.add(url)

    # ---------- Save seen ----------
    seen_data["urls"] = list(seen_urls)
    save(SEEN_FILE, seen_data)

    # ---------- Append to candidates ----------
    existing = load(
        CANDIDATES_FILE,
        {"candidates": [], "total": 0},
    )
    existing["candidates"] = (
        existing.get("candidates", []) + candidates
    )
    existing["generated_at"] = utc_now()
    existing["total"] = len(existing["candidates"])
    save(CANDIDATES_FILE, existing)

    log("\n" + "=" * 50)
    log(f"New candidates: {len(candidates)}")
    log(f"Total in queue: {existing['total']}")


if __name__ == "__main__":
    main()