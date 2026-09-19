# ============================================================
# ARGUS — RERANKER
# Cross-encoder для точной пересортировки результатов FAISS
# ============================================================

import os

_model = None
MODEL_NAME = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"


def _load():
    global _model
    if _model is None:
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(MODEL_NAME, max_length=512)
    return _model


def rerank(query, candidates, top_k=5):
    """
    query: str — вопрос
    candidates: список dict с полем "text" (и любыми другими)
    top_k: сколько лучших вернуть

    Возвращает: список top_k dict, отсортированных по rerank_score
    """
    if not candidates:
        return []

    model = _load()

    pairs = [[query, c.get("text", "")] for c in candidates]
    scores = model.predict(pairs)

    for c, s in zip(candidates, scores):
        c["rerank_score"] = float(s)

    ranked = sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)
    return ranked[:top_k]