# ============================================================
# ARGUS - SEMANTIC SEARCH (v2)
# Uses FAISS index + trained model (or E5 fallback)
# Output: JSON for bot consumption
# ============================================================

import os
import sys
import json
import argparse
from pathlib import Path
import numpy as np
import faiss
from sentence_transformers import SentenceTransformer

# --- Paths ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_meta.json"
TRAINING_INFO = MODELS_DIR / "argus-embeddings" / "training_info.json"
TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"


def load_model():
    if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
        print("Using trained model:", TRAINED_MODEL)
        return SentenceTransformer(str(TRAINED_MODEL)), False
    print("Using base model:", BASE_MODEL)
    return SentenceTransformer(BASE_MODEL), True


def load_index():
    if not INDEX_FILE.exists():
        return None, None
    index = faiss.read_index(str(INDEX_FILE))
    with open(META_FILE, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return index, meta


def search(query, top_k=5, use_prefix=False, model=None, index=None, meta=None):
    if index is None or meta is None:
        return {"error": "Index not found. Run build_index first."}

    text = query
    if use_prefix:
        text = "query: " + text

    emb = model.encode(
        [text],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True
    ).astype("float32")

    scores, ids = index.search(emb, min(top_k, index.ntotal))

    results = []
    for score, idx in zip(scores[0], ids[0]):
        if idx < 0 or idx >= len(meta):
            continue
        m = meta[idx]
        results.append({
            "score": round(float(score), 4),
            "id": m.get("id", ""),
            "source": m.get("source", ""),
            "book": m.get("book", ""),
            "chunk_index": m.get("chunk_index", 0),
            "text": m.get("text", "")
        })

    return {"query": query, "top_k": top_k, "results": results}


def main():
    parser = argparse.ArgumentParser(description="ARGUS semantic search")
    parser.add_argument("query", nargs="?", default="What is trading?", help="Search query")
    parser.add_argument("--top", type=int, default=5, help="Number of results")
    parser.add_argument("--json", action="store_true", help="Force JSON output")
    args = parser.parse_args()

    model, use_prefix = load_model()
    index, meta = load_index()

    result = search(args.query, args.top, use_prefix, model, index, meta)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
