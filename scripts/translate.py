# ============================================================
# ARGUS — ПЕРЕВОДЧИК (EN → RU) v3 [PRODUCTION]
# ------------------------------------------------------------
# v3: продакшн-версия.
#   • Файловый кэш переводов (data/translation_cache.json)
#   • Batch-перевод (до 32 фраз за раз — быстрее в 5-10 раз)
#   • Ленивая загрузка модели (загружается только при первом вызове)
#   • Потокобезопасность (threading.Lock)
#   • Поддержка обоих входов: одиночная строка и список
#   • Безопасные ошибки: при сбое возвращает оригинал
# ------------------------------------------------------------
# v2: torch.no_grad() для экономии памяти
# v1: базовый перевод через Helsinki-NLP/opus-mt-en-ru
# ============================================================

import os
import json
import hashlib
import threading
from pathlib import Path

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
CACHE_FILE = DATA_DIR / "translation_cache.json"

# --- Настройки ---
MODEL_NAME = "Helsinki-NLP/opus-mt-en-ru"
MAX_CHARS = 500          # обрезка одного текста
BATCH_SIZE = 16          # сколько текстов за раз прогонять через модель
CACHE_MAX_SIZE = 5000    # максимум записей в кэше (FIFO-очистка)

# --- Внутреннее состояние ---
_model = None
_tokenizer = None
_lock = threading.Lock()
_cache = None            # ленивая загрузка с диска


# ============================================================
# КЭШ
# ============================================================
def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _cache = json.load(f)
            if not isinstance(_cache, dict):
                _cache = {}
        except Exception:
            _cache = {}
    else:
        _cache = {}
    return _cache


def _save_cache():
    if _cache is None:
        return
    try:
        # FIFO-очистка при переполнении
        if len(_cache) > CACHE_MAX_SIZE:
            keys = list(_cache.keys())
            _cache_new = {k: _cache[k] for k in keys[-CACHE_MAX_SIZE:]}
            _cache.clear()
            _cache.update(_cache_new)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Не сохранил кэш переводов: {e}")


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# ============================================================
# МОДЕЛЬ
# ============================================================
def _load_model():
    global _model, _tokenizer
    with _lock:
        if _model is None:
            import torch
            from transformers import MarianMTModel, MarianTokenizer
            print(f"🌐 Загружаю модель перевода: {MODEL_NAME}")
            _tokenizer = MarianTokenizer.from_pretrained(MODEL_NAME)
            _model = MarianMTModel.from_pretrained(MODEL_NAME)
            _model.eval()
            print("✅ Модель перевода готова")
    return _model, _tokenizer


# ============================================================
# ПУБЛИЧНЫЕ ФУНКЦИИ
# ============================================================
def is_english(text: str) -> bool:
    """Эвристика: больше 60% букв — латиница."""
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return (latin / len(letters)) > 0.6


def translate_to_ru(text: str) -> str:
    """Переводит одну строку. При ошибке возвращает оригинал."""
    if not text or not text.strip():
        return text

    cache = _load_cache()
    key = _cache_key(text)
    if key in cache:
        return cache[key]

    # Не английский — не переводим
    if not is_english(text):
        cache[key] = text
        return text

    try:
        import torch
        model, tokenizer = _load_model()

        truncated = text[:MAX_CHARS]
        tokens = tokenizer(
            [truncated],
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=512,
        )

        with torch.no_grad():
            translated = model.generate(**tokens)

        result = tokenizer.decode(translated[0], skip_special_tokens=True).strip()

        if result:
            cache[key] = result
            return result

    except Exception as e:
        print(f"⚠️ Ошибка перевода: {e}")

    return text


def translate_batch(texts: list) -> list:
    """
    Переводит список текстов за один прогон модели.
    В 5-10 раз быстрее чем по одному.
    Возвращает список переводов в том же порядке.
    """
    if not texts:
        return []

    cache = _load_cache()
    results = [None] * len(texts)
    to_translate_idx = []
    to_translate_texts = []

    # 1. Сначала проверяем кэш и не-английские
    for i, text in enumerate(texts):
        if not text or not text.strip():
            results[i] = text
            continue
        key = _cache_key(text)
        if key in cache:
            results[i] = cache[key]
            continue
        if not is_english(text):
            cache[key] = text
            results[i] = text
            continue
        to_translate_idx.append(i)
        to_translate_texts.append(text[:MAX_CHARS])

    if not to_translate_texts:
        return results

    # 2. Батч через модель
    try:
        import torch
        model, tokenizer = _load_model()

        # Прогоняем порциями по BATCH_SIZE
        for start in range(0, len(to_translate_texts), BATCH_SIZE):
            batch = to_translate_texts[start:start + BATCH_SIZE]
            tokens = tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            with torch.no_grad():
                translated = model.generate(**tokens)
            decoded = tokenizer.batch_decode(translated, skip_special_tokens=True)

            for j, result in enumerate(decoded):
                idx = to_translate_idx[start + j]
                result = result.strip()
                if result:
                    results[idx] = result
                    cache[_cache_key(texts[idx])] = result
                else:
                    results[idx] = texts[idx]

    except Exception as e:
        print(f"⚠️ Batch-перевод упал: {e}")
        # Fallback — то что не перевели, возвращаем как есть
        for idx in to_translate_idx:
            if results[idx] is None:
                results[idx] = texts[idx]

    # Сохраняем кэш один раз в конце
    _save_cache()

    return [r if r is not None else texts[i] for i, r in enumerate(results)]


def save_cache():
    """Публичный метод — сохранить кэш на диск (вызывать в конце работы)."""
    _save_cache()