# ============================================================
# ARGUS — ASK v13
# ------------------------------------------------------------
# Если в запросе есть ключевое слово (macd, rsi, ...) —
# сканируем ВЕСЬ индекс на наличие этого слова в тексте.
# FAISS не нужен. Без fallback, только точное совпадение.
# ============================================================

import os
import sys
import json
import faiss
import requests
from pathlib import Path
from sentence_transformers import SentenceTransformer

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"
CATEGORIES_FILE = DATA_DIR / "book_categories.json"

TOP_K = 5


# ---------- КЛЮЧЕВЫЕ СЛОВА ----------
KEYWORDS = {
    "macd": ["macd", "макд", "macd-линия"],
    "rsi": ["rsi", "индекс относительной силы"],
    "bollinger": ["bollinger", "боллинджер", "полосы бол"],
    "atr": ["atr", "average true range"],
    "volume": ["volume", "объём", "объем"],
    "candle": ["candle", "свеч", "доджи", "молот", "поглощен",
               "утренняя звезда", "вечерняя звезда"],
    "risk": ["риск-менедж", "rule of 1", "правило 1%",
             "risk management", "соотношение риск"],
    "psychology": ["психолог", "fomo", "revenge trading",
                   "эмоци", "дисциплин"],
}


def detect_keyword(query):
    q = query.lower()
    for key, variants in KEYWORDS.items():
        for v in variants:
            if v in q:
                return key, variants
    return None, []


# ---------- ЗАГРУЗКА ----------
with open(CATEGORIES_FILE, encoding="utf-8") as f:
    CATEGORIES = json.load(f)

with open(META_FILE, encoding="utf-8") as f:
    META = json.load(f)

info = {}
if MODEL_INFO_FILE.exists():
    with open(MODEL_INFO_FILE, encoding="utf-8") as f:
        info = json.load(f)

use_prefix = bool(info.get("query_prefix", ""))

# ---------- ЗАПРОС ----------
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "MACD"
keyword_key, keyword_variants = detect_keyword(query)

print(f"Query: {query}")
print(f"Keyword: {keyword_key}")
print(f"Total chunks: {len(META)}")


# ============================================================
# КЛЮЧЕВОЙ ПОИСК — по ВСЕМУ индексу
# ============================================================
if keyword_key:
    hits = []

    # 1. Сначала чанки, где ключ есть в ИМЕНИ файла
    for m in META:
        book = m.get("book", "")
        bl = book.lower()
        if any(v in bl for v in keyword_variants):
            hits.append({
                "score": 1.0,
                "book": book,
                "text": m.get("text", ""),
            })

    # 2. Потом чанки, где ключ есть в тексте
    for m in META:
        book = m.get("book", "")
        bl = book.lower()
        if any(v in bl for v in keyword_variants):
            continue  # уже добавили
        text = m.get("text", "").lower()
        if any(v in text for v in keyword_variants):
            hits.append({
                "score": 0.9,
                "book": book,
                "text": m.get("text", ""),
            })

    results = hits[:TOP_K]
    print(f"Keyword hits: {len(hits)}")
else:
    # Без ключа — обычный FAISS
    model_path = info.get(
        "model_path", "intfloat/multilingual-e5-small"
    )
    model = SentenceTransformer(model_path)
    index = faiss.read_index(str(INDEX_FILE))
    search_q = f"query: {query}" if use_prefix else query
    vec = model.encode(
        [search_q], normalize_embeddings=True
    ).astype("float32")
    distances, indices = index.search(vec, k=50)

    results = []
    for i, idx in enumerate(indices[0]):
        if not (0 <= idx < len(META)):
            continue
        results.append({
            "score": float(distances[0][i]),
            "book": META[idx].get("book", ""),
            "text": META[idx].get("text", ""),
        })
        if len(results) >= TOP_K:
            break

    print(f"FAISS results: {len(results)}")


# ---------- ВЫВОД ----------
for i, r in enumerate(results, 1):
    print(f"  {i}. {r['score']:.2f} | {r['book']}")

if not results:
    answer = f"🔎 Ничего не найдено.\nЗапрос: {query}"
else:
    answer = f"🔎 <b>Результаты</b>\n<i>{query}</i>\n"
    if keyword_key:
        answer += f"<i>keyword: {keyword_key}</i>\n"
    answer += "\n"
    for i, r in enumerate(results, 1):
        b = r["book"].replace(".pdf", "").replace(".md", "")
        t = r["text"][:500]
        answer += f"{i}. 📖 <b>{b}</b>\n"
        answer += f"<code>{t}</code>\n\n"
    if len(answer) > 3900:
        answer = answer[:3890] + "\n<i>обрезано</i>"


bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": answer,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    print("Sent")
else:
    print(answer)