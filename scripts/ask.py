# ============================================================
# ARGUS — ASK v12 FINAL
# ------------------------------------------------------------
# Ключевой поиск: если в запросе есть точное слово (macd, rsi,
# bollinger, atr, volume, candle, risk, psychology) — сначала
# ищем чанки, где ЭТО СЛОВО встречается в тексте. Ранжируем по
# FAISS score. Только если совпадений нет — fallback на чистую
# семантику.
# ============================================================

import os
import re
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
SCAN_TOP = 500


# ---------- КЛЮЧЕВЫЕ СЛОВА ----------
# Если в запросе есть любое из этих слов — ищем по нему.
# Формат: {ключ_в_запросе: [варианты_в_тексте]}
KEYWORDS = {
    "macd": ["macd", "макд"],
    "rsi": ["rsi", "рси", "уайлдер"],
    "bollinger": ["bollinger", "боллинджер", "боллинджер", "полос"],
    "atr": ["atr", "average true range", "атr"],
    "volume": ["volume", "объём", "объем"],
    "candle": ["candle", "свеч", "поглощен", "доджи", "молот"],
    "risk": ["risk management", "риск-менедж", "1%", "стоп-лосс",
             "risk/reward", "просадк"],
    "psychology": ["психолог", "fomo", "revenge", "эмоци"],
}


def detect_keyword(query):
    q = query.lower()
    for key, variants in KEYWORDS.items():
        for v in variants:
            if v in q:
                return key, variants
    return None, []


# ---------- КАТЕГОРИЯ ----------
TRADING_WORDS = [
    "rsi", "macd", "bollinger", "atr", "volume", "свеч",
    "candle", "risk", "риск", "трейд", "trading", "trade",
    "индикатор", "indicator", "сигнал", "signal",
    "тренд", "trend", "стоп", "stop", "тейк",
    "лонг", "шорт", "long", "short", "позиция", "position",
    "вход", "entry", "выход", "exit", "просадк",
    "плеч", "имбаланс", "пробой", "паттерн", "психолог",
]

PHILO_WORDS = ["бэкон", "философ", "стоик", "морал", "этик"]


def detect_category(q):
    q = q.lower()
    for w in TRADING_WORDS:
        if w in q:
            return "quant"
    for w in PHILO_WORDS:
        if w in q:
            return "philosophy"
    return None


# ---------- ЗАГРУЗКА ----------
with open(CATEGORIES_FILE, encoding="utf-8") as f:
    CATEGORIES = json.load(f)

with open(META_FILE, encoding="utf-8") as f:
    META = json.load(f)

info = {}
if MODEL_INFO_FILE.exists():
    with open(MODEL_INFO_FILE, encoding="utf-8") as f:
        info = json.load(f)

model_path = info.get("model_path", "intfloat/multilingual-e5-small")
use_prefix = bool(info.get("query_prefix", ""))

print("Loading model...", flush=True)
model = SentenceTransformer(model_path)
index = faiss.read_index(str(INDEX_FILE))
print(f"Index: {index.ntotal}", flush=True)


def is_trading(book):
    cat = CATEGORIES.get(book, "")
    return cat in ("trading", "quant", "crypto")


# ---------- ЗАПРОС ----------
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "MACD"
target_cat = detect_category(query)
keyword_key, keyword_variants = detect_keyword(query)

print(f"Query: {query}")
print(f"Category: {target_cat}")
print(f"Keyword: {keyword_key} / {keyword_variants}")


# ---------- FAISS ----------
search_q = f"query: {query}" if use_prefix else query
vec = model.encode([search_q], normalize_embeddings=True).astype("float32")
distances, indices = index.search(vec, k=min(SCAN_TOP, index.ntotal))

# Собираем всех кандидатов с их score
all_candidates = []
for i, idx in enumerate(indices[0]):
    if not (0 <= idx < len(META)):
        continue
    book = META[idx].get("book", "")
    if target_cat == "quant" and not is_trading(book):
        continue
    all_candidates.append({
        "score": float(distances[0][i]),
        "book": book,
        "text": META[idx].get("text", ""),
    })

print(f"After category filter: {len(all_candidates)}")


# ---------- KEYWORD BOOST ----------
if keyword_key and keyword_variants:
    hits = []
    for c in all_candidates:
        t = c["text"].lower()
        b = c["book"].lower()
        if any(v in t for v in keyword_variants):
            hits.append(c)
        elif any(v in b for v in keyword_variants):
            hits.append(c)

    print(f"Keyword hits: {len(hits)}")

    if hits:
        # сортируем совпадения по FAISS score
        hits.sort(key=lambda x: -x["score"])
        results = hits[:TOP_K]
    else:
        results = all_candidates[:TOP_K]
else:
    results = all_candidates[:TOP_K]


print(f"Final results: {len(results)}")
for i, r in enumerate(results, 1):
    print(f"  {i}. {r['score']:.3f} | {r['book']}")


# ---------- ОТВЕТ ----------
if not results:
    answer = f"🔎 Ничего не найдено.\nЗапрос: {query}"
else:
    answer = f"🔎 <b>Результаты</b>\n<i>{query}</i>\n"
    if keyword_key:
        answer += f"<i>keyword: {keyword_key}</i>\n"
    answer += "\n"
    for i, r in enumerate(results, 1):
        b = r["book"].replace(".pdf", "").replace(".md", "")
        t = r["text"][:400]
        s = r["score"] * 100
        answer += f"{i}. 📖 <b>{b}</b> ({s:.0f}%)\n"
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