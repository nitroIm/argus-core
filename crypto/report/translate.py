# ============================================================
# ARGUS - TRANSLATE (any -> RU) v6 [PRODUCTION]
# ------------------------------------------------------------
# v6: MyMemory как основной, Google как fallback.
# v5: пост-проверка непереведённых
# v4: словарь замен
# ============================================================

import os
import re
import json
import time
import hashlib
import logging
import requests
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
CACHE_FILE = DATA_DIR / "news_translate_cache.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("crypto.translate")

# --- MyMemory ---
MM_URL = "https://api.mymemory.translated.net/get"
MM_TIMEOUT = 20

# --- Google (fallback) ---
GT_URL = "https://translate.googleapis.com/translate_a/single"
GT_TIMEOUT = 20

DELAY_BETWEEN = 0.5
CACHE_MAX_SIZE = 5000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

REPLACEMENTS = [
    (r"\bБикотинск\w*", "Bitcoin"),
    (r"\bбикотинск\w*", "Bitcoin"),
    (r"\bБиткойн\b", "Bitcoin"),
    (r"\bбиткойн\b", "Bitcoin"),
    (r"\bБиткоин\b", "Bitcoin"),
    (r"\bбиткоин\b", "Bitcoin"),
    (r"\bЭфириум\b", "Ethereum"),
    (r"\bэфириум\b", "Ethereum"),
    (r"\bСолана\b", "Solana"),
    (r"\bсолана\b", "Solana"),
    (r"\bКЦБ\b", "SEC"),
    (r"\bкцб\b", "SEC"),
    (r"\bКФТК\b", "CFTC"),
    (r"\bкфтк\b", "CFTC"),
    (r"\bККДТ\b", "CFTC"),
    (r"\bккдт\b", "CFTC"),
    (r"\bПопрос\b", "Спрос"),
    (r"\bпопрос\b", "спрос"),
    (r"\bЕТФ\b", "ETF"),
]

_cache = None


def _load_cache():
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


def _key(text):
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _postprocess(text):
    if not text:
        return text
    for pattern, repl in REPLACEMENTS:
        text = re.sub(pattern, repl, text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_russian(text):
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


def is_english(text):
    if not text:
        return False
    try:
        letters = [c for c in text if c.isalpha()]
        if not letters:
            return False
        latin = sum(1 for c in letters if c.isascii())
        return (latin / len(letters)) > 0.6
    except Exception:
        return False


def _should_skip(text):
    t = text.strip()
    if len(t) < 8:
        return True
    letters = sum(1 for c in t if c.isalpha())
    if letters < 3:
        return True
    return False


def _looks_untranslated(original, translated):
    if not translated:
        return True
    o = original.strip().lower()
    t = translated.strip().lower()
    if o == t:
        return True
    if not re.search(r"[а-яА-ЯёЁ]", translated):
        return True
    return False


# ============================================================
# MYMEMORY
# ============================================================
def _mm_translate(text):
    """MyMemory — основной переводчик."""
    if not text or not text.strip():
        return None
    if len(text) > 500:
        text = text[:500]

    params = {
        "q": text,
        "langpair": "en|ru",
    }

    try:
        r = requests.get(
            MM_URL,
            params=params,
            headers=HEADERS,
            timeout=MM_TIMEOUT,
        )
        if r.status_code != 200:
            log.warning(
                "mm %d: %s",
                r.status_code, r.text[:150],
            )
            return None

        data = r.json()
        if data.get("responseStatus") != 200:
            log.warning(
                "mm status: %s",
                data.get("responseStatus"),
            )
            return None

        result = data.get("responseData", {}).get(
            "translatedText", ""
        )
        result = result.strip()
        if result and not _looks_untranslated(text, result):
            return _postprocess(result)
        return None
    except Exception as e:
        log.warning("mm err: " + str(e))
        return None


# ============================================================
# GOOGLE (fallback)
# ============================================================
def _gt_translate(text):
    if not text or not text.strip():
        return None
    if len(text) > 2000:
        text = text[:2000]

    params = {
        "client": "gtx",
        "sl": "auto",
        "tl": "ru",
        "dt": "t",
        "q": text,
    }

    try:
        r = requests.get(
            GT_URL,
            params=params,
            headers=HEADERS,
            timeout=GT_TIMEOUT,
        )
        if r.status_code != 200:
            return None

        data = r.json()
        if not data or not isinstance(data, list):
            return None
        if not data[0]:
            return None

        pieces = []
        for chunk in data[0]:
            if chunk and len(chunk) > 0 and chunk[0]:
                pieces.append(chunk[0])
        result = "".join(pieces).strip()
        if result and not _looks_untranslated(text, result):
            return _postprocess(result)
        return None
    except Exception as e:
        log.warning("gt err: " + str(e))
        return None


# ============================================================
# PUBLIC
# ============================================================
def _translate_one(text):
    """MyMemory → Google → оригинал."""
    # 1. MyMemory
    r1 = _mm_translate(text)
    if r1:
        return r1

    time.sleep(0.3)

    # 2. Google
    r2 = _gt_translate(text)
    if r2:
        return r2

    # 3. Оригинал
    return text


def translate_batch(texts):
    if not texts:
        return []

    cache = _load_cache()
    results = [None] * len(texts)
    to_translate_idx = []
    to_translate_texts = []

    for i, text in enumerate(texts):
        if not text or not text.strip():
            results[i] = text
            continue
        if is_russian(text):
            results[i] = text
            continue
        if _should_skip(text):
            results[i] = text
            continue

        key = _key(text)
        if key in cache:
            results[i] = cache[key]
            continue

        to_translate_idx.append(i)
        to_translate_texts.append(text)

    if not to_translate_texts:
        return results

    log.info(
        "translating %d texts (MyMemory)",
        len(to_translate_texts),
    )

    for k, text in enumerate(to_translate_texts):
        idx = to_translate_idx[k]
        result = _translate_one(text)
        results[idx] = result
        cache[_key(text)] = result
        if k < len(to_translate_texts) - 1:
            time.sleep(DELAY_BETWEEN)

    _save_cache()
    return [
        r if r is not None else texts[i]
        for i, r in enumerate(results)
    ]


def translate_to_ru(text):
    if not text:
        return text
    return translate_batch([text])[0]


def save_cache():
    _save_cache()