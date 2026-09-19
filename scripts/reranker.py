# ============================================================
# ARGUS — RERANKER (v2)
# v2: защита от сбоев загрузки, отключение прогресс-бара, типизация, безопасное копирование
# ============================================================

import os
from typing import List, Dict, Any

_model = None
# Отличная легкая мультиязычная модель для переранжирования
MODEL_NAME = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MAX_LENGTH = 512


def _load():
    """Ленивая загрузка модели с защитой от сбоев."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import CrossEncoder
            # show_progress_bar=False критически важен, чтобы не засорять логи при каждом запросе
            _model = CrossEncoder(MODEL_NAME, max_length=MAX_LENGTH)
            print(f"✅ Reranker модель загружена: {MODEL_NAME}")
        except ImportError:
            print("⚠️ Reranker: библиотека sentence_transformers не найдена. Reranking отключен.")
            return None
        except Exception as e:
            print(f"⚠️ Reranker: ошибка загрузки модели {MODEL_NAME}: {e}")
            return None
    return _model


def rerank(query: str, candidates: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
    """
    Пересортировывает кандидаты с помощью Cross-encoder.
    
    :param query: поисковый запрос
    :param candidates: список словарей, каждый должен содержать ключ "text"
    :param top_k: сколько лучших результатов вернуть
    :return: отсортированный список топ-k кандидатов с добавленным полем "rerank_score"
    """
    if not candidates:
        return []

    model = _load()
    
    # Если модель не загрузилась, возвращаем исходный список (fallback на FAISS)
    if model is None:
        print("⚠️ Reranker недоступен, использую исходный порядок FAISS")
        return candidates[:top_k]

    try:
        # Гарантируем, что текст является строкой
        pairs = [[query, str(c.get("text", ""))] for c in candidates]
        
        # predict с отключенным прогресс-баром и небольшим батчем для скорости
        scores = model.predict(pairs, show_progress_bar=False, batch_size=8)

        # Создаем новые словари, чтобы не мутировать исходные данные (best practice)
        ranked = []
        for c, s in zip(candidates, scores):
            new_c = c.copy()
            new_c["rerank_score"] = float(s)
            ranked.append(new_c)

        # Сортируем по убыванию скорa
        ranked.sort(key=lambda x: x["rerank_score"], reverse=True)
        
        return ranked[:top_k]

    except Exception as e:
        print(f"⚠️ Reranker: ошибка при предсказании: {e}. Возвращаю исходный порядок.")
        # В случае ошибки вычислений возвращаем хотя бы топ-k из исходного списка
        return candidates[:top_k]
