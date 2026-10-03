# ============================================================
# ARGUS — SEMANTIC SEARCH (v9)
# v9: word boundary только для латинских аббревиатур (rsi, macd, atr).
#     Для русских корней — обычный substring.
#     + KEYWORDS для trend, sr, orderflow, crypto, exchange
#     + перевод EN→RU, dedup по книге, файл-вывод.
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
except ImportError:
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
    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    def rerank(query, candidates, top_k=5):
        return candidates[:top_k]


# ---------- КЛЮЧЕВЫЕ СЛОВА ----------
KEYWORDS = {
    "macd": ["macd", "макд"],
    "rsi": ["rsi"],
    "bollinger": ["bollinger", "боллинджер", "полосы бол"],
    "atr": ["atr", "average true range"],
    "volume": ["volume", "объём", "объем"],
    "candle": ["candle", "свеч", "доджи", "молот",
               "поглощен", "утренняя звезда", "вечерняя звезда"],
    "risk": ["риск-менедж", "правило 1%", "risk management",
             "соотношение риск", "стоп-лосс", "просадк",
             "управление риск"],
    "psychology": ["психолог", "fomo", "revenge trading",
                   "эмоци", "дисциплин", "тильт"],
    "trend": ["тренд", "trend", "восходящ",
              "нисходящ", "разворот тренд", "смена тренд"],
    "sr": ["уровен", "поддержк", "сопротивл", "support",
           "resistance", "ретест", "пробой уровн"],
    "orderflow": ["имбаланс", "order flow", "дельта",
                  "стакан", "ликвидн", "stop hunting",
                  "ликвидац", "поглощен", "taker",
                  "orderflow"],
    "crypto": ["funding", "фандинг", "плеч", "leverage",
               "крипт", "crypto", "биткоин", "bitcoin",
               "btc", "ethereum", "eth "],
    "exchange": ["биржа", "exchange", "ордер", "order ",
                 "mexc", "как открыть", "как торговать",
                 "комисс", "ликвидац", "плечо", "плеча",
                 "cross margin", "isolated"],
}


def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


def detect_keyword(query):
    q = query.lower()
    for key, variants in KEYWORDS.items():
        for v in variants:
            if v in q:
                return key, variants
    return None, []


def make_word_regex(variants):
    """
    Word boundary только для коротких латинских аббревиатур
    (rsi, macd, atr). Для русских корней и длинных слов —
    обычный substring, чтобы находить словоформы.
    """
    parts = []
    for v in variants:
        is_latin_short = (
            re.match(r"^[a-z0-9]{1,5}$", v) is not None
        )
        if is_latin_short:
            parts.append(
                r"(?<![a-z0-9])"
                + re.escape(v)
                + r"(?![a-z0-9])"
            )
        else:
            parts.append(re.escape(v))
    return re.compile("|".join(parts), re.IGNORECASE)


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
        log("Missing index files")
        return None, None
    try:
        index = faiss.read_index(str(INDEX_FILE))
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return index, meta
    except Exception as e:
        log(f"Load error: {e}")
        return None, None


def translate_text(text):
    if not text:
        return text
    try:
        if is_english(text):
            return translate_to_ru(text)
    except Exception as e:
        log(f"Translate error: {e}")
    return text


def search(query, top_k=5, use_prefix=False,
           model=None, index=None, meta=None):
    if index is None or meta is None:
        return {"error": "Index or metadata not found."}

    keyword_key, keyword_variants = detect_keyword(query)
    log(f"Keyword: {keyword_key}")

    if keyword_key and keyword_variants:
        regex = make_word_regex(keyword_variants)
        hits = []
        seen_books = set()

        # 1. Совпадение в имени файла
        for m in meta:
            book = m.get("book", "")
            if regex.search(book.lower()):
                if book in seen_books:
                    continue
                seen_books.add(book)
                hits.append({
                    "score": 1.0,
                    "id": m.get("id", ""),
                    "source": m.get("source", ""),
                    "book": book,
                    "chunk_index": m.get("chunk_index", 0),
                    "text": m.get("text", ""),
                })

        # 2. Совпадение в тексте
        for m in meta:
            if len(hits) >= top_k:
                break
            book = m.get("book", "")
            if book in seen_books:
                continue
            text = m.get("text", "")
            if regex.search(text):
                seen_books.add(book)
                hits.append({
                    "score": 0.9,
                    "id": m.get("id", ""),
                    "source": m.get("source", ""),
                    "book": book,
                    "chunk_index": m.get("chunk_index", 0),
                    "text": text,
                })

        results = hits[:top_k]
        for r in results:
            r["text"] = translate_text(r["text"])
        log(f"Keyword hits: {len(hits)}")
        return {"query": query, "top_k": len(results),
                "results": results}

    # ---- FAISS fallback ----
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
            log(f"Reranker failed: {e}")
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    for c in candidates:
        c["text"] = translate_text(c["text"])

    return {"query": query, "top_k": len(candidates),
            "results": candidates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?",
                        default="Что такое трейдинг?")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result_file = os.environ.get("SEARCH_RESULT_FILE")

    try:
        model, use_prefix = load_model()
        index, meta = load_index()

        if index is None:
            result = {"error": "FAISS index not found."}
        else:
            result = search(args.query, args.top, use_prefix,
                            model, index, meta)
    except Exception as e:
        result = {"error": f"Search failed: {e}"}

    payload = json.dumps(result, ensure_ascii=False, indent=2)

    if result_file:
        with open(result_file, "w", encoding="utf-8") as f:
            f.write(payload)
        log(f"Result written to {result_file}")
    else:
        sys.__stdout__.write(payload + "\n")


if __name__ == "__main__":
    main()