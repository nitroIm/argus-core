# ============================================================
# ARGUS — ASK v10.1
# ------------------------------------------------------------
# v10.1: + RERANK_MIN = 0.3 (фильтр релевантности).
#        + Логи переведены на английский.
#        + Строки приведены к <=55 символов.
# ============================================================

import os
import re
import sys
import json
import time
import math
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
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
CATEGORIES_FILE = DATA_DIR / "book_categories.json"

FAISS_TOP_K = 100
FINAL_TOP_K = 5
RERANK_MIN = 0.3

start_time = time.time()


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S", time.gmtime())
    print(f"[{ts}] [{level}] {msg}", flush=True)


def normalize_for_dedup(text):
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = text.strip(' "\'«»""„“”*—-,.;')
    return text.strip().lower()


def dedup_key(text):
    norm = normalize_for_dedup(text)
    raw = norm[:300].encode("utf-8")
    return hashlib.md5(raw).hexdigest()


# ============================================================
# CATEGORY FILTER
# ============================================================
CATEGORIES = {}
if CATEGORIES_FILE.exists():
    try:
        with open(
            CATEGORIES_FILE, "r", encoding="utf-8"
        ) as f:
            CATEGORIES = json.load(f)
        log(f"Loaded categories: {len(CATEGORIES)}")
    except Exception as e:
        log(f"book_categories.json error: {e}", "WARN")

CATEGORY_KEYWORDS = {
    "quant": [
        "rsi", "macd", "trend", "стратег", "trading",
        "trade", "signal", "сигнал", "индикатор",
        "indicator", "moving average", "ма",
        "bollinger", "atr", "volume", "объем",
        "volatility", "волатильн", "backtest",
        "momentum", "mean reversion", "sharpe",
        "drawdown", "risk", "риск", "stop loss",
        "стоп", "take profit", "тейк", "position",
        "позиция", "long", "short", "лонг", "шорт",
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
        return cat in ("quant", "crypto")
    if target_category == "crypto":
        return cat in ("quant", "crypto")
    if target_category == "philosophy":
        return cat in ("philosophy", "quant")
    return True


# ============================================================
# CHECKS
# ============================================================
if not INDEX_FILE.exists():
    err_msg = "faiss.index not found"
    log_action("ask", error=err_msg)
    print("FAISS index not found.")
    sys.exit(1)

meta_missing = not META_FILE.exists()
meta_empty = META_FILE.stat().st_size == 0
if meta_missing or meta_empty:
    print("Chunks metadata file not found.")
    sys.exit(1)


TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"

use_prefix = False
model_path = BASE_MODEL

if MODEL_INFO_FILE.exists():
    try:
        with open(
            MODEL_INFO_FILE, encoding="utf-8"
        ) as f:
            info = json.load(f)
        model_path = info.get("model_path", BASE_MODEL)
        prefix = info.get("query_prefix", "")
        use_prefix = bool(prefix)
        log(f"model_info: {info.get('model_label', '?')}")
    except Exception as e:
        log(f"model_info.json broken: {e}", "WARN")
elif TRAINED_MODEL.exists():
    has_config = (TRAINED_MODEL / "config.json").exists()
    if has_config:
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

with open(META_FILE, "r", encoding="utf-8") as f:
    raw_meta = json.load(f)

if not isinstance(raw_meta, list) or not raw_meta:
    print("chunks_for_index.json is empty.")
    sys.exit(1)


meta_chunks = []
if isinstance(raw_meta[0], str):
    log("LEGACY format", "WARN")
    kn_chunks = []
    if KNOWLEDGE_FILE.exists():
        try:
            with open(
                KNOWLEDGE_FILE, encoding="utf-8"
            ) as f:
                kn = json.load(f)
            kn_chunks = kn.get("chunks", [])
        except Exception:
            pass
    for i, text in enumerate(raw_meta):
        if i < len(kn_chunks):
            c = kn_chunks[i]
            raw_hash = hashlib.md5(
                text.encode()
            ).hexdigest()[:12]
            cid = c.get("id") or f"legacy#{raw_hash}"
            meta_chunks.append({
                "id": cid,
                "source": c.get("source", ""),
                "book": c.get("book", ""),
                "text": text,
            })
        else:
            raw_hash = hashlib.md5(
                text.encode()
            ).hexdigest()[:12]
            cid = f"legacy#{raw_hash}"
            meta_chunks.append({
                "id": cid,
                "source": "",
                "book": "",
                "text": text,
            })
else:
    meta_chunks = raw_meta
    log("New format")


log(f"Index: {index.ntotal} vectors, meta: {len(meta_chunks)}")


rerank_fn = None
try:
    from reranker import rerank as rerank_fn
    log("Reranker connected")
except Exception as e:
    log(f"Reranker unavailable: {e}", "WARN")


default_q = "Что такое имбаланс?"
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or default_q
log(f"Query: {query}")

target_cat = detect_category(query)
log(f"Query category: {target_cat}")


if use_prefix:
    search_query = f"query: {query}"
else:
    search_query = query

query_vec = model.encode(
    [search_query], normalize_embeddings=True
).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

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

msg = f"FAISS: {len(indices[0])} -> "
msg += f"after filter: {len(candidates)} "
msg += f"(skipped: {skipped_cat})"
log(msg)


if not candidates:
    answer = "🔎 По запросу ничего релевантного не найдено."
    log_action("ask", query=query, found_chunks=0)
    print(answer)
else:
    if rerank_fn:
        try:
            rerank_input = []
            for c in candidates:
                item = dict(c["meta"])
                item["_orig_index"] = c["index"]
                item["_faiss_score"] = c["score"]
                rerank_input.append(item)

            top_k_val = FINAL_TOP_K * 3
            reranked = rerank_fn(
                query, rerank_input, top_k=top_k_val
            )

            ranked_all = []
            for r in reranked:
                orig_idx = r.get("_orig_index")
                if orig_idx is None:
                    continue
                if orig_idx >= len(meta_chunks):
                    continue
                raw_rr = r.get("rerank_score")
                if raw_rr is None:
                    rr_norm = None
                else:
                    try:
                        rr_norm = 1 / (
                            1 + math.exp(-float(raw_rr))
                        )
                    except Exception:
                        rr_norm = None
                ranked_all.append({
                    "score": r.get("_faiss_score", 0.0),
                    "rerank_score": raw_rr,
                    "rerank_norm": rr_norm,
                    "meta": meta_chunks[orig_idx],
                })
            log(f"Reranker: {len(candidates)} -> {len(ranked_all)}")
        except Exception as e:
            log(f"Reranker failed: {e}, fallback", "WARN")
            ranked_all = [
                {
                    "score": c["score"],
                    "rerank_score": None,
                    "rerank_norm": None,
                    "meta": c["meta"],
                }
                for c in candidates
            ]
    else:
        ranked_all = [
            {
                "score": c["score"],
                "rerank_score": None,
                "rerank_norm": None,
                "meta": c["meta"],
            }
            for c in candidates
        ]

    # Apply RERANK_MIN threshold
    filtered_ranked = []
    for r in ranked_all:
        if r.get("rerank_norm") is not None:
            if r["rerank_norm"] >= RERANK_MIN:
                filtered_ranked.append(r)
        else:
            if r["score"] >= RERANK_MIN:
                filtered_ranked.append(r)

    if not filtered_ranked:
        answer = "🔎 По запросу ничего релевантного не найдено."
        log_action("ask", query=query, found_chunks=0)
        print(answer)
    else:
        top_raw = filtered_ranked[:FINAL_TOP_K * 2]

        seen_keys = set()
        deduped_top = []
        for r in top_raw:
            text = r["meta"].get("text", "").strip()
            key = dedup_key(text)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            deduped_top.append(r)
            if len(deduped_top) >= FINAL_TOP_K:
                break

        top = deduped_top

        translated_count = 0
        final_top = []
        for r in top:
            text = r["meta"].get("text", "")
            book = r["meta"].get("book", "") or ""
            if not book:
                book = r["meta"].get("source", "Unknown")
            if is_english(text):
                try:
                    text = translate_to_ru(text)
                    translated_count += 1
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
                "rerank_norm": r.get("rerank_norm"),
                "book": book_clean,
                "text": text,
            })

        answer = "🔎 <b>Результаты для:</b>\n"
        answer += f"<i>{query}</i>\n\n"
        for i, r in enumerate(final_top, 1):
            base_pct = r["score"] * 100
            if r["rerank_norm"] is not None:
                rerank_pct = r["rerank_norm"] * 100
                metrics = f"base {base_pct:.0f}%"
                metrics += f" · релевантность {rerank_pct:.0f}%"
            else:
                metrics = f"base {base_pct:.0f}%"
            answer += f"{i}. 📖 <b>{r['book']}</b>\n"
            answer += f"   <i>{metrics}</i>\n"
            answer += f"<code>{r['text']}</code>\n\n"
        if len(answer) > 3900:
            answer = answer[:3890] + "\n\n<i>... (обрезано)</i>"

        elapsed_ms = int((time.time() - start_time) * 1000)
        log_action(
            "ask",
            query=query,
            found_chunks=len(top),
            response_time_ms=elapsed_ms,
            extra={
                "category": target_cat,
                "skipped_by_cat": skipped_cat,
            },
        )
        log(f"Found: {len(top)}, category: {target_cat}")


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
            log("Sent successfully")
        else:
            log(f"TG error {r.status_code}", "WARN")
    except Exception as e:
        log(f"TG error: {e}", "WARN")
else:
    print("Telegram not configured:\n" + answer)