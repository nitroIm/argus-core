# ============================================================
# ARGUS — ПЕРЕВОДЧИК (EN → RU) (v2)
# v2: добавлен torch.no_grad() для экономии памяти при массовом переводе
# Локальная модель Helsinki-NLP/opus-mt-en-ru
# ============================================================

import torch

_model = None
_tokenizer = None


def _load_model():
    global _model, _tokenizer
    if _model is None:
        from transformers import MarianMTModel, MarianTokenizer
        MODEL_NAME = "Helsinki-NLP/opus-mt-en-ru"
        _tokenizer = MarianTokenizer.from_pretrained(MODEL_NAME)
        _model = MarianMTModel.from_pretrained(MODEL_NAME)
    return _model, _tokenizer


def is_english(text):
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) > 0.6


def translate_to_ru(text):
    if not text or not text.strip():
        return text

    model, tokenizer = _load_model()
    text = text[:500]  # Ограничение длины для стабильности модели

    tokens = tokenizer(
        [text],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512
    )
    
    # Отключаем расчёт градиентов, чтобы не забивать оперативную память 
    # при массовом переводе новостей или чанков
    with torch.no_grad():
        translated = model.generate(**tokens)
        
    return tokenizer.decode(translated[0], skip_special_tokens=True)
