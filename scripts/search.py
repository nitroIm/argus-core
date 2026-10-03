# ============================================================
# ARGUS — SEARCH v2.1 [FINAL]
# ------------------------------------------------------------
# Автоопределение темы по имени файла и заголовку.
# Новые гайды подхватываются автоматически.
# ============================================================

import os
import re
import sys
import json
import argparse
import warnings

warnings.filterwarnings("ignore")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

try:
    from translate import is_english, translate_to_ru
    TRANSLATE_OK = True
except ImportError:
    TRANSLATE_OK = False

    def is_english(text):
        return False

    def translate_to_ru(text):
        return text

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
    RERANKER_OK = True
except ImportError:
    RERANKER_OK = False

    def rerank(query, candidates, top_k=5):
        return candidates[:top_k]


STOP = {
    "что", "такое", "как", "это", "для", "без",
    "при", "над", "под", "или", "все", "чем",
    "кто", "где", "когда", "зачем", "почему",
    "который", "которая", "которые", "они",
    "она", "он", "мы", "вы", "the", "a", "an",
    "of", "for", "to", "in", "on", "at", "is",
    "are", "was", "were", "be", "been", "and",
    "or", "not", "мне", "мы", "есть", "был",
    "была", "были", "будет", "работает",
    "работают", "работать", "вот", "тут",
    "там", "них", "нее", "меня", "тебе",
    "тебя", "свой", "своя", "такие", "такой",
}


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


def tokenize(text):
    words = re.findall(r"[a-zа-яё]{3,}", (text or "").lower())
    return {w for w in words if w not in STOP}


def build_index(meta):
    by_book = {}
    for i, m in enumerate(meta):
        b = m.get("book", "")
        if not b:
            continue
        by_book.setdefault(b, []).append((i, m))

    book_tokens = {}
    book_chunks = {}

    for book, chunks in by_book.items():
        name = book.rsplit(".", 1)[0]
        parts = re.split(r"[\d_\-\.\s]+", name.lower())
        tokens = set()
        for p in parts:
            if len(p) >= 3 and p not in STOP:
                tokens.add(p)
        for _, m in chunks[:3]:
            t = m.get("text", "")[:800]
            tokens |= tokenize(t)
        book_tokens[book] = tokens
        book_chunks[book] = chunks

    return book_tokens, book_chunks


def load_model():
    if TRAINED_MODEL.exists():
        cfg = TRAINED_MODEL / "config.json"
        if cfg.exists():
            log(f"Model: {TRAINED_MODEL}")
            return SentenceTransformer(str(TRAINED_MODEL)), False
    log(f"Model: {BASE_MODEL}")
    return SentenceTransformer(BASE_MODEL), True


def load_index():
    if not INDEX_FILE.exists() or not META_FILE.exists():
        log("Missing files")
        return None, None
    try:
        index = faiss.read_index(str(INDEX_FILE))
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, list) or not meta:
            log("Meta empty")
            return None, None
        return index, meta
    except Exception as e:
        log(f"Load error: {e}")
        return None, None


def translate_text(text):
    if not text or not TRANSLATE_OK:
        return text
    try:
        if is_english(text):
            return translate_to_ru(text)
    except Exception as e:
        log(f"Translate error: {e}")
    return text


def make_hit(m, score):
    return {
        "score": score,
        "id": m.get("id", ""),
        "book": m.get("book", ""),
        "text": m.get("text", ""),
    }


def topic_search(query, top_k, book_tokens, book_chunks):
    q_tokens = tokenize(query)
    if not q_tokens:
        return []

    scored = {}
    for book, tokens in book_tokens.items():
        overlap = len(q_tokens & tokens)
        if overlap > 0:
            scored[book] = overlap

    if not scored:
        return []

    ranked = sorted(scored.items(), key=lambda x: -x[1])
    hits = []
    for book, _ in ranked[:top_k]:
        chunks = book_chunks.get(book, [])
        if not chunks:
            continue
        _, m = chunks[0]
        hits.append(make_hit(m, 1.0))

    return hits


def faiss_search(query, top_k, use_prefix,
                 model, index, meta):
    text = f"query: {query}" if use_prefix else query
    emb = model.encode(
        [text], normalize_embeddings=True,
        show_progress_bar=False, convert_to_numpy=True,
    ).astype("float32")
    k = min(top_k * 3, index.ntotal)
    scores, ids = index.search(emb, k)
    candidates = []
    for score, idx in zip(scores[0], ids[0]):
        if 0 <= idx < len(meta):
            s = round(float(score), 4)
            candidates.append(make_hit(meta[idx], s))
    if RERANKER_OK and len(candidates) > top_k:
        try:
            candidates = rerank(query, candidates,
                                top_k=top_k)
        except Exception:
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]
    return candidates


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?",
                        default="Что такое трейдинг?")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result_file = os.environ.get("SEARCH_RESULT_FILE")

    index, meta = load_index()
    if index is None or meta is None:
        result = {"error": "Index or metadata not found."}
    else:
        log(f"Chunks: {len(meta)}")
        book_tokens, book_chunks = build_index(meta)
        log(f"Books: {len(book_tokens)}")

        results = topic_search(
            args.query, args.top,
            book_tokens, book_chunks,
        )
        log(f"Topic hits: {len(results)}")

        if not results:
            log("Fallback FAISS")
            model = None
            use_prefix = False
            try:
                model, use_prefix = load_model()
            except Exception as e:
                log(f"Model error: {e}")
            if model is not None:
                results = faiss_search(
                    args.query, args.top,
                    use_prefix, model, index, meta,
                )
            log(f"FAISS hits: {len(results)}")

        for r in results:
            r["text"] = translate_text(r["text"])

        result = {
            "query": args.query,
            "results": results,
        }

    payload = json.dumps(result, ensure_ascii=False,
                         indent=2)

    if result_file:
        with open(result_file, "w", encoding="utf-8") as f:
            f.write(payload)
        log(f"Written: {result_file}")
    else:
        sys.__stdout__.write(payload + "\n")
        sys.__stdout__.flush()


if __name__ == "__main__":
    main()