# ============================================================
# ARGUS — ASK v10.3-DEBUG
# ------------------------------------------------------------
# Диагностика. Отключает RERANK_MIN и реранкер.
# Показывает топ-5 FAISS без фильтров.
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


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S", time.gmtime())
    print(f"[{ts}] [{level}] {msg}", flush=True)


CATEGORIES = {}
if CATEGORIES_FILE.exists():
    with open(CATEGORIES_FILE, encoding="utf-8") as f:
        CATEGORIES = json.load(f)
    log(f"Loaded categories: {len(CATEGORIES)}")


if not INDEX_FILE.exists():
    print("FAISS index not found.")
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

log(f"Index: {index.ntotal} vectors, meta: {len(meta_chunks)}")


query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "MACD"
log(f"Query: {query}")

search_query = f"query: {query}" if use_prefix else query
query_vec = model.encode(
    [search_query], normalize_embeddings=True
).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

# DEBUG: показываем ТОП-10 без фильтров
log("=== TOP-10 FAISS (raw, no filter) ===")
top10_info = []
for i in range(min(10, len(indices[0]))):
    idx = int(indices[0][i])
    score = float(distances[0][i])
    if 0 <= idx < len(meta_chunks):
        b = meta_chunks[idx].get("book", "?")
        cat = CATEGORIES.get(b, "N/A")
        log(f"  {i+1}. {score:.3f} | {b} | cat={cat}")
        top10_info.append((score, b, cat))

# Показываем топ-5 в чат
answer = "🔎 <b>DEBUG MACD</b>\n"
answer += f"Запрос: <i>{query}</i>\n"
answer += f"Индекс: {index.ntotal} векторов\n\n"
answer += "<b>Топ-5 FAISS без фильтров:</b>\n"
for i, (s, b, c) in enumerate(top10_info[:5], 1):
    answer += f"{i}. <b>{b}</b>\n"
    answer += f"   score={s:.3f} · cat={c}\n\n"

if len(answer) > 3900:
    answer = answer[:3890] + "\n\n<i>обрезано</i>"

log_action("ask", query=query, found_chunks=len(top10_info))


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
    print(answer)