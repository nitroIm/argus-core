# ============================================================
# ARGUS — ПЕРЕВОДЧИК (EN → RU)
# Локальная модель Helsinki-NLP/opus-mt-en-ru
# ============================================================

# Загружаем модель лениво (только когда понадобится)
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
    """Проверяет, английский ли текст (больше 60% латиницы)."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    latin = sum(1 for c in letters if c.isascii())
    return latin / len(letters) > 0.6


def translate_to_ru(text):
    """Переводит английский текст на русский."""
    if not text or not text.strip():
        return text

    model, tokenizer = _load_model()

    # Обрезаем до 500 символов (лимит модели)
    text = text[:500]

    tokens = tokenizer(
        [text],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512
    )

    translated = model.generate(**tokens)
    result = tokenizer.decode(translated[0], skip_special_tokens=True)

    return result


# ---------- Тест ----------
if __name__ == "__main__":
    text = "Algorithmic trading is the use of computer programs to execute trades."
    print(f"EN: {text}")
    print(f"RU: {translate_to_ru(text)}")
    print(f"Это английский? {is_english(text)}")