# ============================================================
# ARGUS — SEMANTIC SEARCH (v4)
# v4: META_FILE = chunks_for_index.json (было неправильно)
#     + keyword boost по всему индексу
# ============================================================

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"
TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"

try:
    from reranker import rerank
    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    def rerank(query, candidates, top_k=5):
        return candidates[:top_k]


# ---------- КЛЮЧЕВЫЕ СЛОВА ----------
KEYWORDS = {
    "macd": ["macd", "макд", "macd-линия"],
    "rsi": ["rsi", "индекс относительной силы"],
    "bollinger": ["bollinger", "боллинджер", "полосы бол"],
    "atr": ["atr", "average true range"],
    "volume": ["volume", "объём", "объем"],
    "candle": ["candle", "свеч", "доджи", "молот", "поглощен",
               "утренняя звезда", "вечерняя звезда"],
    "risk": ["риск-менедж", "правило 1%", "risk management",
             "соотношение риск", "стоп-лосс", "просадк"],
    "psychology": ["психолог", "fomo", "revenge trading",
                   "эмоци", "дисциплин"],
}


def detect_keyword(query):
    q = query.lower()
    for key, variants in KEYWORDS.items():
        for v in variants:
            if v in q:
                return key, variants
    return None, []


def load_model():
    if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
        print(f"🧠 Model: {TRAINED_MODEL}", file=sys.stderr)
        return SentenceTransformer(str(TRAINED_MODEL)), False
    print(f"📦 Model: {BASE_MODEL}", file=sys.stderr)
    return SentenceTransformer(BASE_MODEL), True


def load_index():
    if not INDEX_FILE.exists() or not META_FILE.exists():
        print(f"⚠️ Missing: {INDEX_FILE} or {META_FILE}", file=sys.stderr)
        return None, None
    try:
        index = faiss.read_index(str(INDEX_FILE))
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return index, meta
    except Exception as e:
        print(f"⚠️ Load error: {e}", file=sys.stderr)
        return None, None


def search(query, top_k=5, use_prefix=False, model=None, index=None, meta=None):
    if index is None or meta is None:
        return {"error": "Index or metadata not found."}

    keyword_key, keyword_variants = detect_keyword(query)
    print(f"Keyword: {keyword_key}", file=sys.stderr)

    # ---- KEYWORD SEARCH (по всему индексу) ----
    if keyword_key and keyword_variants:
        hits = []
        for m in meta:
            book = m.get("book", "")
            bl = book.lower()
            if any(v in bl for v in keyword_variants):
                hits.append({
                    "score": 1.0,
                    "id": m.get("id", ""),
                    "source": m.get("source", ""),
                    "book": book,
                    "chunk_index": m.get("chunk_index", 0),
                    "text": m.get("text", ""),
                })
        for m in meta:
            book = m.get("book", "")
            bl = book.lower()
            if any(v in bl for v in keyword_variants):
                continue
            text = m.get("text", "").lower()
            if any(v in text for v in keyword_variants):
                hits.append({
                    "score": 0.9,
                    "id": m.get("id", ""),
                    "source": m.get("source", ""),
                    "book": book,
                    "chunk_index": m.get("chunk_index", 0),
                    "text": m.get("text", ""),
                })
        results = hits[:top_k]
        print(f"Keyword hits: {len(hits)}", file=sys.stderr)
        return {"query": query, "top_k": len(results), "results": results}

    # ---- FAISS SEARCH (без ключа) ----
    text = f"query: {query}" if use_prefix else query
    emb = model.encode(
        [text], normalize_embeddings=True,
        show_progress_bar=False, convert_to_numpy=True,
    ).astype("float32")

    search_k = top_k * 3 if RERANKER_AVAILABLE else top_k
    scores, ids = index.search(emb, min(search_k, index.ntotal))

    candidates = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0 or idx >= len(meta):
            continue
        m = meta[idx]
        candidates.append({
            "score": round(float(score), 4),
            "id": m.get("id", ""),
            "source": m.get("source", ""),
            "book": m.get("book", ""),
            "chunk_index": m.get("chunk_index", 0),
            "text": m.get("text", ""),
        })

    if RERANKER_AVAILABLE and len(candidates) > top_k:
        try:
            candidates = rerank(query, candidates, top_k=top_k)
        except Exception as e:
            print(f"⚠️ Reranker failed: {e}", file=sys.stderr)
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    return {"query": query, "top_k": len(candidates), "results": candidates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?", default="Что такое трейдинг?")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    model, use_prefix = load_model()
    index, meta = load_index()

    if index is None:
        err = {"error": "FAISS index or metadata not found."}
        print(json.dumps(err, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = search(args.query, args.top, use_prefix, model, index, meta)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()