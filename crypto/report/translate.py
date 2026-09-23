# ============================================================
# ARGUS - TRANSLATE (any -> RU) v1 [PRODUCTION]
# ------------------------------------------------------------
# NLLB-200: 200+ языков -> русский напрямую.
# Для news + отчётов crypto.
# ------------------------------------------------------------
# Требования:
#   pip install transformers==4.41.2 sentencepiece
#               torch==2.2.0 langdetect
# ============================================================

import json
import hashlib
import logging
import threading
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
CACHE_FILE = DATA_DIR / "news_translate_cache.json"

MODEL_NAME = "facebook/nllb-200-distilled-600M"
TGT_LANG = "rus_Cyrl"
MAX_CHARS = 500
CACHE_MAX_SIZE = 5000
MIN_TRANSLATE_CHARS = 8
BROKEN_RATIO = 0.5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.translate")

LANG_MAP = {
    "en": "eng_Latn",
    "es": "spa_Latn",
    "de": "deu_Latn",
    "fr": "fra_Latn",
    "it": "ita_Latn",
    "pt": "por_Latn",
    "nl": "nld_Latn",
    "pl": "pol_Latn",
    "ru": "rus_Cyrl",
    "uk": "ukr_Cyrl",
    "zh-cn": "zho_Hans",
    "zh-tw": "zho_Hant",
    "ja": "jpn_Jpan",
    "ko": "kor_Hang",
    "ar": "arb_Arab",
    "tr": "tur_Latn",
    "vi": "vie_Latn",
    "hi": "hin_Deva",
    "cs": "ces_Latn",
    "sv": "swe_Latn",
    "da": "dan_Latn",
    "fi": "fin_Latn",
    "no": "nob_Latn",
    "el": "ell_Grek",
    "he": "heb_Hebr",
    "hu": "hun_Latn",
    "ro": "ron_Latn",
    "bg": "bul_Cyrl",
}

_model = None
_tokenizer = None
_lock = threading.Lock()
_cache = None


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


def _save_cache() -> None:
    if _cache is None:
        return
    try:
        if len(_cache) > CACHE_MAX_SIZE:
            keys = list(_cache.keys())
            new_cache = {}
            for k in keys[-CACHE_MAX_SIZE:]:
                new_cache[k] = _cache[k]
            _cache.clear()
            _cache.update(new_cache)

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(
                _cache, f,
                ensure_ascii=False, indent=2,
            )
    except Exception as e:
        log.warning("cache save: " + str(e))


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def detect_lang(text: str) -> str:
    try:
        from langdetect import detect
        return detect(text[:500])
    except Exception:
        return "en"


def get_nllb_code(lang: str) -> str:
    lang = lang.lower()
    if lang in LANG_MAP:
        return LANG_MAP[lang]
    short = lang[:2]
    if short in LANG_MAP:
        return LANG_MAP[short]
    return "eng_Latn"


def is_russian(text: str) -> bool:
    if not text:
        return False
    sample = text[:3000]
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return False
    cyr = 0
    for c in letters:
        if "\u0400" <= c <= "\u04ff":
            cyr += 1
    return (cyr / len(letters)) > 0.5


def is_english(text: str) -> bool:
    if not text:
        return False
    try:
        return detect_lang(text) == "en"
    except Exception:
        return False


def _should_skip(text: str) -> bool:
    t = text.strip()
    if len(t) < MIN_TRANSLATE_CHARS:
        return True
    letters = sum(1 for c in t if c.isalpha())
    if letters < 3:
        return True
    return False


def looks_broken(text: str) -> bool:
    if not text:
        return False
    words = text.split()
    if len(words) < 5:
        return False
    from collections import Counter
    counts = Counter(words)
    top = counts.most_common(1)[0]
    if top[1] / len(words) > BROKEN_RATIO:
        return True
    if len(words) >= 3:
        if words[-1] == words[-2] == words[-3]:
            return True
    return False


def _load_model():
    global _model, _tokenizer
    with _lock:
        if _model is None:
            import torch
            from transformers import (
                AutoModelForSeq2SeqLM,
                AutoTokenizer,
            )
            log.info("loading NLLB: " + MODEL_NAME)
            _tokenizer = AutoTokenizer.from_pretrained(
                MODEL_NAME, src_lang="eng_Latn",
            )
            _model = AutoModelForSeq2SeqLM.from_pretrained(
                MODEL_NAME
            )
            _model.eval()
            log.info("NLLB ready")
    return _model, _tokenizer


def _generate(model, tokenizer, texts):
    import torch
    tokens = tokenizer(
        texts,
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=512,
    )
    with torch.no_grad():
        out = model.generate(
            **tokens,
            forced_bos_token_id=(
                tokenizer.convert_tokens_to_ids(TGT_LANG)
            ),
            max_new_tokens=400,
            num_beams=1,
            no_repeat_ngram_size=4,
            repetition_penalty=1.3,
            length_penalty=1.0,
        )
    return tokenizer.batch_decode(
        out, skip_special_tokens=True
    )


def translate_to_ru(text: str) -> str:
    if not text or not text.strip():
        return text

    cache = _load_cache()
    key = _cache_key(text)
    if key in cache:
        return cache[key]

    if is_russian(text):
        cache[key] = text
        return text

    if _should_skip(text):
        cache[key] = text
        return text

    src_lang = detect_lang(text)
    nllb_src = get_nllb_code(src_lang)

    try:
        model, tokenizer = _load_model()
        tokenizer.src_lang = nllb_src
        truncated = text[:MAX_CHARS]
        decoded = _generate(
            model, tokenizer, [truncated],
        )
        result = decoded[0].strip() if decoded else ""

        if result and not looks_broken(result):
            cache[key] = result
            return result
        log.warning("broken, keep original")
    except Exception as e:
        log.error("translate err: " + str(e))

    cache[key] = text
    return text


def translate_batch(texts: list) -> list:
    if not texts:
        return []
    return [translate_to_ru(t) for t in texts]


def save_cache() -> None:
    _save_cache()