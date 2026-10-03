# ============================================================
# ARGUS — SEARCH v1.0 [PRODUCTION]
# ------------------------------------------------------------
# Финальная версия. Не требует правок при добавлении гайдов.
#
# Логика:
#   1. Определяем ключевое слово в запросе.
#   2. Если ключ есть — ищем по ВСЕМУ индексу (имя файла + текст).
#   3. Если ключа нет — FAISS semantic search.
#   4. Перевод EN→RU.
#   5. Dedup по книге.
#   6. JSON в файл или stdout.
#
# Word boundary только для латинских аббревиатур (rsi, macd, atr).
# Для русских корней — substring (находит словоформы).
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


# ============================================================
# KEYWORDS
# ------------------------------------------------------------
# Формат: ключ -> список подстрок для поиска.
# Для русских корней пиши без гласной на конце (уровн, дивергенц).
# Для аббревиатур латиницей — слово целиком (rsi, macd).
# ============================================================
KEYWORDS = {
    # --- Индикаторы ---
    "macd": ["macd", "макд"],
    "rsi": ["rsi"],
    "bollinger": ["bollinger", "боллинджер", "полосы бол"],
    "atr": ["atr", "average true range"],
    "volume": ["volume", "объём", "объем"],

    # --- Свечи и паттерны ---
    "candle": ["candle", "свеч", "доджи", "молот",
               "поглощен", "утренняя звезда",
               "вечерняя звезда", "пинцет"],
    "patterns": ["паттерн", "pattern", "голова и плечи",
                 "треугольник", "флаг", "клин",
                 "двойн", "тройн", "чашк"],

    # --- Тренд и уровни ---
    "trend": ["тренд", "trend", "восходящ", "нисходящ",
              "разворот тренд", "смена тренд"],
    "sr": ["уровн", "поддержк", "сопротивл", "support",
           "resistance", "ретест", "пробой уровн"],
    "fibonacci": ["фибоначчи", "фибо", "fibonacci",
                  "золотое сечение", "retracement",
                  "extension"],

    # --- Аналитика ---
    "divergence": ["дивергенц", "divergence", "расхожден"],
    "orderflow": ["имбаланс", "order flow", "дельта",
                  "стакан", "ликвидн", "stop hunting",
                  "ликвидац", "taker", "orderflow",
                  "iceberg", "spoofing", "абсорбц"],

    # --- Риск и психология ---
    "risk": ["риск-менедж", "правило 1%", "risk management",
             "соотношение риск", "стоп-лосс",
             "просадк", "управление риск"],
    "psychology": ["психолог", "fomo", "revenge trading",
                   "эмоци", "дисциплин", "тильт"],
    "money_management": ["управление капитал",
                         " "positionmoney management",
                         "размер sizing позиц",",
                         "мартингейл", "пирамид"],

    # --- Крипта и биржи ---
    "crypto": ["funding", "фандинг", "плеч", "leverage",
               "крипт", "crypto", "биткоин", "bitcoin",
               "btc", "ethereum", "eth ", "стейбл",
               "stablecoin"],
    "exchange": ["биржа", "exchange", "ордер", "order ",
                 "mexc", "binance", "bybit", "okx",
                 "kucoin", "bitget", "gate", "htx",
                 "как открыть", "как торговать",
                 "комисс", "ликвидац", "cross margin",
                 "isolated", "maker", "taker"],
}


# ============================================================
# ЛОГГЕР
# ============================================================
def log(msg):
    sys.stderr.write(str(msg) + "\n")
    sys.stderr.flush()


# ============================================================
# KEYWORD DETECTION
# ============================================================
def detect_keyword(query):
    q = (query or "").lower()
    for key, variants in KEYWORDS.items():
        for v in variants:
            if v in q:
                return key, variants
    return None, []


def make_word_regex(variants):
    """
    Word boundary только для коротких латинских аббревиатур.
    Для русских корней — обычный substring.
    """
    parts = []
    for v in variants:
        if not v:
            continue
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
    if not parts:
        return None
    return re.compile("|".join(parts), re.IGNORECASE)


# ============================================================
# ЗАГРУЗКА
# ============================================================
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
        log("Missing index or metadata")
        return None, None
    try:
        index = faiss.read_index(str(INDEX_FILE))
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if not isinstance(meta, list) or not meta:
            log("Meta is empty or wrong format")
            return None, None
        return index, meta
    except Exception as e:
        log(f"Load error: {e}")
        return None, None


# ============================================================
# ПЕРЕВОД
# ============================================================
def translate_text(text):
    if not text or not TRANSLATE_OK:
        return text
    try:
        if is_english(text):
            return translate_to_ru(text)
    except Exception as e:
        log(f"Translate error: {e}")
    return text


# ============================================================
# ХИТ
# ============================================================
def make_hit(m, score):
    return {
        "score": score,
        "id": m.get("id", ""),
        "source": m.get("source", ""),
        "book": m.get("book", ""),
        "chunk_index": m.get("chunk_index", 0),
        "text": m.get("text", ""),
    }


# ============================================================
# ПОИСК
# ============================================================
def keyword_search(variants, top_k, meta):
    regex = make_word_regex(variants)
    if regex is None:
        return []

    hits = []
    seen = set()

    # 1. Имя файла
    for m in meta:
        book = (m.get("book") or "").lower()
        if not book:
            continue
        if regex.search(book):
            if book in seen:
                continue
            seen.add(book)
            hits.append(make_hit(m, 1.0))

    # 2. Текст
    for m in meta:
        if len(hits) >= top_k:
            break
        book = (m.get("book") or "").lower()
        if book in seen:
            continue
        text = (m.get("text") or "")
        if regex.search(text):
            seen.add(book)
            hits.append(make_hit(m, 0.9))

    return hits[:top_k]


def faiss_search(query, top_k, use_prefix, model, index, meta):
    text = f"query: {query}" if use_prefix else query
    try:
        emb = model.encode(
            [text], normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        ).astype("float32")
    except Exception as e:
        log(f"Encode error: {e}")
        return []

    search_k = top_k * 3 if RERANKER_OK else top_k
    search_k = min(search_k, index.ntotal)
    try:
        scores, ids = index.search(emb, search_k)
    except Exception as e:
        log(f"FAISS error: {e}")
        return []

    candidates = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0 or idx >= len(meta):
            continue
        candidates.append(make_hit(meta[idx], round(float(score), 4)))

    if RERANKER_OK and len(candidates) > top_k:
        try:
            candidates = rerank(query, candidates, top_k=top_k)
        except Exception as e:
            log(f"Reranker failed: {e}")
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    return candidates


def search(query, top_k=5, use_prefix=False,
           model=None, index=None, meta=None):
    if not meta:
        return {"error": "No metadata available."}

    keyword_key, keyword_variants = detect_keyword(query)
    log(f"Keyword: {keyword_key}")

    # --- Keyword search ---
    if keyword_key and keyword_variants:
        results = keyword_search(keyword_variants, top_k, meta)
        if results:
            for r in results:
                r["text"] = translate_text(r["text"])
            log(f"Keyword hits: {len(results)}")
            return {
                "query": query,
                "keyword": keyword_key,
                "top_k": len(results),
                "results": results,
            }
        log("Keyword found but no hits, fallback to FAISS")

    # --- FAISS fallback ---
    if model is None or index is None:
        return {"error": "Model or index not loaded."}

    results = faiss_search(
        query, top_k, use_prefix, model, index, meta
    )
    for r in results:
        r["text"] = translate_text(r["text"])
    log(f"FAISS hits: {len(results)}")
    return {
        "query": query,
        "keyword": keyword_key,
        "top_k": len(results),
        "results": results,
    }


# ============================================================
# MAIN
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?",
                        default="Что такое трейдинг?")
    parser.add_argument("--top", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result_file = os.environ.get("SEARCH_RESULT_FILE")
    model = None
    use_prefix = False

    # Пробуем подгрузить индекс и модель
    # (для keyword-поиска модель не нужна)
    index, meta = load_index()

    if index is None or meta is None:
        result = {"error": "FAISS index or metadata not found."}
    else:
        # Определяем, нужна ли модель
        keyword_key, _ = detect_keyword(args.query)

        # Загружаем модель только если keyword не найден
        # или если keyword-поиск не даст результатов.
        # Для простоты — грузим всегда, но в try.
        if not keyword_key:
            try:
                model, use_prefix = load_model()
            except Exception as e:
                log(f"Model load error: {e}")
                model = None

        try:
            result = search(
                args.query, args.top, use_prefix,
                model, index, meta,
            )

            # Если keyword-поиск дал пустой результат
            # и модель не загружена — грузим и ищем через FAISS
            if (keyword_key and not result.get("results")
                    and model is None):
                try:
                    model, use_prefix = load_model()
                    result = search(
                        args.query, args.top, use_prefix,
                        model, index, meta,
                    )
                except Exception as e:
                    log(f"Retry error: {e}")

        except Exception as e:
            result = {"error": f"Search failed: {e}"}

    payload = json.dumps(result, ensure_ascii=False, indent=2)

    if result_file:
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                f.write(payload)
            log(f"Written: {result_file}")
        except Exception as e:
            log(f"Write error: {e}")
    else:
        sys.__stdout__.write(payload + "\n")
        sys.__stdout__.flush()


if __name__ == "__main__":
    main()