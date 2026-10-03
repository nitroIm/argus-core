# ============================================================
# ARGUS — ASK v10.4
# ------------------------------------------------------------
# v10.4: фильтр по категориям включён.
#        Реранкер отключён (упрощение).
#        RERANK_MIN = 0.0 (отключён).
# ============================================================

import os
import re
import sys
import json
import time
import hashlib
import faiss
import requests
from pathlib import Path
from sentence_transformers import SentenceTransformer

try:
    from logger import log_action
except ImportError:
    def log_action(*args, **kwargs):
        pass

try:
    from translate import is_english, translate_to_ru
except ImportError:
    def is_english(text):
        return False

    def translate_to_ru(text):
        return text

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"
CATEGORIES_FILE = DATA_DIR / "book_categories.json"

FAISS_TOP_K = 100
FINAL_TOP_K = 5
RERANK_MIN = 0.0

start_time = time.time()


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S", time.gmtime())
    print(f"[{ts}] [{level}] {msg}", flush=True)


def normalize_for_dedup(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = text.strip(' "\'«»""„“”*—-,.;')
    return text.strip().lower()


def dedup_key(text):
    norm = normalize_for_dedup(text)
    return hashlib.md5(norm[:300].encode("utf-8")).hexdigest()


CATEGORIES = {}
if CATEGORIES_FILE.exists():
    try:
        with open(CATEGORIES_FILE, encoding="utf-8") as f:
            CATEGORIES = json.load(f)
        log(f"Loaded categories: {len(CATEGORIES)}")
    except Exception as e:
        log(f"categories error: {e}", "WARN")

CATEGORY_KEYWORDS = {
    "quant": [
        "rsi", "macd", "trend", "стратег", "trading",
        "trade", "signal", "сигнал", "индикатор",
        "indicator", "moving average", "bollinger",
        "atr", "volume", "объем", "volatility",
        "волатильн", "backtest", "momentum",
        "mean reversion", "sharpe", "drawdown",
        "risk", "риск", "stop loss", "стоп",
        "take profit", "тейк", "position", "позиция",
        "long", "short", "лонг", "шорт",
        "entry", "вход", "exit", "выход", "support",
        "resistance", "поддержк", "сопротивл",
        "candle", "свеч", "price action", "паттерн",
        "pattern", "breakout", "пробой", "timeframe",
        "таргет", "target", "profit", "прибыл",
    ],
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth",
        "crypto", "крипт", "blockchain", "блокчейн",
        "altcoin", "defi", "web3", "stablecoin",
        "стейбл", "btcusdt", "ethusdt", "binance",
        "mexc", "exchange", "биржа",
    ],
    "philosophy": [
        "философ", "philosophy", "этик", "морал",
        "бэкон", "стоик", "seneca", "эпиктет",
        "психология", "психолог",
    ],
}


def detect_category(query):
    q = query.lower()
    scores = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        s = sum(1 for kw in kws if kw in q)
        if s > 0:
            scores[cat] = s
    if not scores:
        return None
    return max(scores, key=scores.get)


def book_allowed(book_name, target_category):
    if not CATEGORIES or not target_category:
        return True
    cat = CATEGORIES.get(book_name)
    if cat is None:
        return True
    if target_category == "quant":
        return cat in ("quant", "crypto", "trading")
    if target_category == "crypto":
        return cat in ("quant", "crypto", "trading")
    if target_category == "philosophy":
        return cat in ("philosophy", "quant", "psychology")
    return True


if not INDEX_FILE.exists():
    log_action("ask", error="faiss.index missing")
    print("FAISS index not found.")
    sys.exit(1)

meta_missing = not META_FILE.exists()
meta_empty = META_FILE.stat().st_size == 0
if meta_missing or meta_empty:
    print("Metadata not found.")
    sys.exit(1)


TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"
use_prefix = False
model_path = BASE_MODEL

if MODEL_INFO_FILE.exists():
    try:
        with open(MODEL_INFO_FILE, encoding="utf-8") as f:
            info = json.load(f)
        model_path = info.get("model_path", BASE_MODEL)
        prefix = info.get("query_prefix", "")
        use_prefix = bool(prefix)
    except Exception:
        pass
elif TRAINED_MODEL.exists():
    if (TRAINED_MODEL / "config.json").exists():
        model_path = str(TRAINED_MODEL)
        use_prefix = False
    else:
        use_prefix = True
else:
    use_prefix = True


log("Loading model...")
model = SentenceTransformer(model_path)

log("Loading index...")
index = faiss.read_index(str(INDEX_FILE))

with open(META_FILE, encoding="utf-8") as f:
    meta_chunks = json.load(f)

log(f"Index: {index.ntotal}, meta: {len(meta_chunks)}")


default_q = "Что такое имбаланс?"
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or default_q
log(f"Query: {query}")

target_cat = detect_category(query)
log(f"Query category: {target_cat}")

search_query = f"query: {query}" if use_prefix else query
query_vec = model.encode(
    [search_query], normalize_embeddings=True
).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

# DEBUG top10 raw
log("=== TOP-10 raw ===")
for i in range(min(10, len(indices[0]))):
    idx = int(indices[0][i])
    score = float(distances[0][i])
    if 0 <= idx < len(meta_chunks):
        b = meta_chunks[idx].get("book", "?")
        c = CATEGORIES.get(b, "N/A")
        log(f"  {i+1}. {score:.3f} | {b} | cat={c}")

candidates = []
skipped_cat = 0
for i, idx in enumerate(indices[0]):
    if not (0 <= idx < len(meta_chunks)):
        continue
    meta = meta_chunks[idx]
    book = meta.get("book", "") or meta.get("source", "")
    if not book_allowed(book, target_cat):
        skipped_cat += 1
        continue
    candidates.append({
        "score": float(distances[0][i]),
        "meta": meta,
        "index": int(idx),
    })

log(f"FAISS: {len(indices[0])} -> after filter: {len(candidates)} "
    f"(skipped: {skipped_cat})")

if not candidates:
    answer = "🔎 Ничего не найдено (после фильтра категорий)."
    log_action("ask", query=query, found_chunks=0)
else:
    top = candidates[:FINAL_TOP_K]

    log("=== TOP-5 after filter ===")
    for i, c in enumerate(top):
        b = c["meta"].get("book", "?")
        s = c["score"]
        log(f"  {i+1}. {s:.3f} | {b}")

    final_top = []
    for r in top:
        text = r["meta"].get("text", "")
        book = r["meta"].get("book", "") or ""
        if not book:
            book = r["meta"].get("source", "Unknown")
        if is_english(text):
            try:
                text = translate_to_ru(text)
            except Exception:
                pass
        if len(text) > 500:
            text = text[:497] + "..."
        book_clean = book.replace(".pdf", "")
        book_clean = book_clean.replace(".txt", "")
        book_clean = book_clean.replace(".md", "")
        book_clean = book_clean.strip()
        final_top.append({
            "score": r["score"],
            "book": book_clean,
            "text": text,
        })

    answer = "🔎 <b>Результаты:</b>\n"
    answer += f"<i>{query}</i>\n"
    answer += f"<i>category={target_cat}</i>\n\n"
    for i, r in enumerate(final_top, 1):
        base_pct = r["score"] * 100
        answer += f"{i}. 📖 <b>{r['book']}</b>\n"
        answer += f"   <i>score {base_pct:.0f}%</i>\n"
        answer += f"<code>{r['text']}</code>\n\n"
    if len(answer) > 3900:
        answer = answer[:3890] + "\n\n<i>обрезано</i>"

    elapsed_ms = int((time.time() - start_time) * 1000)
    log_action(
        "ask", query=query, found_chunks=len(top),
        response_time_ms=elapsed_ms,
        extra={"category": target_cat, "skipped": skipped_cat},
    )


bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
if not bot_token:
    bot_token = os.getenv("BOT_TOKEN")
chat_id = os.getenv("CHAT_ID")
if not chat_id:
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

if bot_token and chat_id:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": answer,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if r.status_code == 200:
            log("Sent")
        else:
            log(f"TG {r.status_code}", "WARN")
    except Exception as e:
        log(f"TG error: {e}", "WARN")
else:
    print(answer)