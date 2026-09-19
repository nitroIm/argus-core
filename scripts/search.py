# ============================================================
# ARGUS — SEMANTIC SEARCH (v3)
# v3: фикс имени файла метаданных, защита от сбоев, опциональный reranker
# ============================================================

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
# ИСПРАВЛЕНО: имя файла должно совпадать с тем, что создает build_index.py
META_FILE = DATA_DIR / "chunks_metadata.json"  
TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"

# --- Опциональная загрузка Reranker ---
try:
    from reranker import rerank
    RERANKER_AVAILABLE = True
except ImportError:
    RERANKER_AVAILABLE = False
    def rerank(query, candidates, top_k=5):
        return candidates[:top_k]  # Fallback на обычный FAISS


def load_model():
    """Загружает обученную модель или fallback на базовую E5."""
    if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
        print(f"🧠 Используем обученную модель: {TRAINED_MODEL}")
        return SentenceTransformer(str(TRAINED_MODEL)), False
    
    print(f"📦 Используем базовую модель: {BASE_MODEL}")
    return SentenceTransformer(BASE_MODEL), True


def load_index():
    """Безопасная загрузка индекса и метаданных."""
    if not INDEX_FILE.exists() or not META_FILE.exists():
        return None, None
    try:
        index = faiss.read_index(str(INDEX_FILE))
        with open(META_FILE, "r", encoding="utf-8") as f:
            meta = json.load(f)
        return index, meta
    except Exception as e:
        print(f"⚠️ Ошибка загрузки индекса или метаданных: {e}")
        return None, None


def search(query: str, top_k: int = 5, use_prefix: bool = False, model=None, index=None, meta=None):
    """Выполняет семантический поиск с опциональным reranking."""
    if index is None or meta is None:
        return {"error": "Index or metadata not found. Run build_index first."}

    # Добавляем префикс только для базовой модели E5
    text = f"query: {query}" if use_prefix else query

    emb = model.encode(
        [text],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True
    ).astype("float32")

    # Ищем чуть больше кандидатов для reranker'а (если он доступен)
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
            "text": m.get("text", "")
        })

    # Применяем reranker, если он доступен и кандидатов больше, чем нужно
    if RERANKER_AVAILABLE and len(candidates) > top_k:
        try:
            candidates = rerank(query, candidates, top_k=top_k)
        except Exception as e:
            print(f"⚠️ Reranker failed, falling back to FAISS order: {e}")
            candidates = candidates[:top_k]
    else:
        candidates = candidates[:top_k]

    return {"query": query, "top_k": len(candidates), "results": candidates}


def main():
    parser = argparse.ArgumentParser(description="ARGUS semantic search")
    parser.add_argument("query", nargs="?", default="Что такое трейдинг?", help="Search query")
    parser.add_argument("--top", type=int, default=5, help="Number of results")
    parser.add_argument("--json", action="store_true", help="Force JSON output (default)")
    args = parser.parse_args()

    model, use_prefix = load_model()
    index, meta = load_index()

    if index is None:
        error_result = {"error": "FAISS index or metadata not found. Please run build_index.py first."}
        print(json.dumps(error_result, ensure_ascii=False, indent=2))
        sys.exit(1)

    result = search(args.query, args.top, use_prefix, model, index, meta)
    
    # Всегда выводим JSON для удобного парсинга в GitHub Actions или боте
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
