# ============================================================
# ARGUS — ASK v8 [PRODUCTION]
# ------------------------------------------------------------
# v8: продакшн-версия.
#   • Улучшенная дедупликация: нормализация текста перед сравнением
#     (схлопывание пробелов, чистка кавычек и запятых, сравнение
#     по первым 300 символам) — убирает "почти-дубли"
#   • Нормализация rerank_score через сигмоиду (0..100%)
#   • Красивый вывод: book title без .pdf, компактные метрики
#   • Совместим с translate.py v3, reranker.py v2, model_info.json
#   • Совместим с chunks_for_index.json (новый + legacy формат)
# ------------------------------------------------------------
# v7: первый дедуп, сигмоида для rerank, чистка имени книги
# v6: chunks_for_index.json, fix reranker API, model_info.json support
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

# --- Безопасные импорты ---
try:
    from logger import log_action
except ImportError:
    def log_action(*args, **kwargs): pass

try:
    from translate import is_english, translate_to_ru
except ImportError:
    def is_english(text): return False
    def translate_to_ru(text): return text

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_for_index.json"
MODEL_INFO_FILE = DATA_DIR / "model_info.json"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"

FAISS_TOP_K = 20
FINAL_TOP_K = 5

start_time = time.time()


# ============================================================
# ЛОГИРОВАНИЕ
# ============================================================
def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S", time.gmtime())
    print(f"[{ts}] [{level}] {msg}", flush=True)


# ============================================================
# НОРМАЛИЗАЦИЯ ТЕКСТА (для дедупликации)
# ============================================================
def normalize_for_dedup(text: str) -> str:
    """
    Приводит текст к канонической форме для сравнения:
    - все пробельные символы → один пробел
    - убирает множественные запятые и точки
    - убирает начальные и конечные кавычки/мусор
    - приводит к нижнему регистру
    """
    if not text:
        return ""
    # Все виды пробелов → обычный пробел
    text = re.sub(r"\s+", " ", text)
    # ,, → ,  и  .... → .
    text = re.sub(r",{2,}", ",", text)
    text = re.sub(r"\.{2,}", ".", text)
    # Убираем ведущие/замыкающие кавычки, тире, звёздочки
    text = text.strip(' "\'«»""„“”*—-,.;')
    return text.strip().lower()


def dedup_key(text: str) -> str:
    """Хэш по первым 300 символам нормализованного текста."""
    norm = normalize_for_dedup(text)
    return hashlib.md5(norm[:300].encode("utf-8")).hexdigest()


# ============================================================
# 1. ПРОВЕРКИ
# ============================================================
if not INDEX_FILE.exists():
    log_action("ask", error="faiss.index not found")
    print("❌ FAISS индекс не найден. Сначала запусти build_index.")
    sys.exit(1)

if not META_FILE.exists() or META_FILE.stat().st_size == 0:
    log_action("ask", error="chunks_for_index.json missing or empty")
    print("❌ Файл метаданных чанков не найден или пуст.")
    print("   Запусти /train для пересборки.")
    sys.exit(1)


# ============================================================
# 2. ОПРЕДЕЛЕНИЕ МОДЕЛИ
# ============================================================
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
        log(f"model_info.json: {info.get('model_label', '?')}, prefix='{prefix}'")
    except Exception as e:
        log(f"model_info.json битый: {e}", "WARN")

elif TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
    model_path = str(TRAINED_MODEL)
    use_prefix = False
    log(f"Обученная модель: {model_path}")

else:
    model_path = BASE_MODEL
    use_prefix = True
    log(f"Базовая модель: {model_path} (префикс 'query: ')")


# ============================================================
# 3. ЗАГРУЗКА МОДЕЛИ И ИНДЕКСА
# ============================================================
log("Загружаю модель...")
model = SentenceTransformer(model_path)

log("Загружаю индекс...")
index = faiss.read_index(str(INDEX_FILE))

try:
    with open(META_FILE, "r", encoding="utf-8") as f:
        raw_meta = json.load(f)
except json.JSONDecodeError as e:
    log_action("ask", error=f"chunks_for_index.json invalid: {e}")
    print(f"❌ chunks_for_index.json битый: {e}")
    sys.exit(1)

if not isinstance(raw_meta, list) or not raw_meta:
    log_action("ask", error="chunks_for_index.json empty or not list")
    print("❌ chunks_for_index.json пуст или не список.")
    sys.exit(1)


# ============================================================
# 4. ПОДДЕРЖКА LEGACY ФОРМАТА (список строк)
# ============================================================
meta_chunks = []
if isinstance(raw_meta[0], str):
    log("chunks_for_index.json в LEGACY формате (список строк)", "WARN")
    kn_chunks = []
    if KNOWLEDGE_FILE.exists():
        try:
            with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
                kn = json.load(f)
            kn_chunks = kn.get("chunks", [])
        except Exception:
            pass

    for i, text in enumerate(raw_meta):
        if i < len(kn_chunks):
            c = kn_chunks[i]
            cid = c.get("id") or f"legacy#{hashlib.md5(text.encode()).hexdigest()[:12]}"
            meta_chunks.append({
                "id": cid,
                "source": c.get("source", c.get("book", "")),
                "book": c.get("book", ""),
                "text": text,
            })
        else:
            cid = f"legacy#{hashlib.md5(text.encode()).hexdigest()[:12]}"
            meta_chunks.append({"id": cid, "source": "", "book": "", "text": text})
else:
    meta_chunks = raw_meta
    log("chunks_for_index.json в новом формате")

log(f"Индекс: {index.ntotal} векторов, метаданных: {len(meta_chunks)}")


# ============================================================
# 5. RERANKER
# ============================================================
rerank_fn = None
try:
    from reranker import rerank as rerank_fn
    log("Reranker подключён")
except Exception as e:
    log(f"Reranker недоступен: {e}", "WARN")


# ============================================================
# 6. ВОПРОС
# ============================================================
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "Что такое имбаланс?"
log(f"Вопрос: {query}")


# ============================================================
# 7. ПОИСК
# ============================================================
search_query = f"query: {query}" if use_prefix else query
query_vec = model.encode([search_query], normalize_embeddings=True).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

candidates = []
for i, idx in enumerate(indices[0]):
    if 0 <= idx < len(meta_chunks):
        candidates.append({
            "score": float(distances[0][i]),
            "meta": meta_chunks[idx],
            "index": int(idx),
        })


# ============================================================
# 8. ФОРМИРОВАНИЕ ОТВЕТА
# ============================================================
if not candidates or all(c["score"] < 0.3 for c in candidates):
    answer = "🔎 По запросу ничего релевантного не найдено в базе знаний."
    log_action("ask", query=query, found_chunks=0)
    print(answer)
else:
    # --- Reranker ---
    if rerank_fn:
        try:
            rerank_input = []
            for c in candidates:
                item = dict(c["meta"])
                item["_orig_index"] = c["index"]
                item["_faiss_score"] = c["score"]
                rerank_input.append(item)

            reranked = rerank_fn(query, rerank_input, top_k=FINAL_TOP_K * 3)

            top = []
            for r in reranked:
                orig_idx = r.get("_orig_index")
                if orig_idx is None or orig_idx >= len(meta_chunks):
                    continue
                top.append({
                    "score": r.get("_faiss_score", 0.0),
                    "rerank_score": r.get("rerank_score"),
                    "meta": meta_chunks[orig_idx],
                })
            log(f"Reranker: {len(candidates)} → {len(top)}")
        except Exception as e:
            log(f"Reranker упал: {e}, fallback на FAISS", "WARN")
            top = candidates[:FINAL_TOP_K * 2]
    else:
        top = candidates[:FINAL_TOP_K * 2]

    # --- ДЕДУПЛИКАЦИЯ (нормализованная) ---
    seen_keys = set()
    deduped_top = []
    duplicates_removed = 0

    for r in top:
        text = r["meta"].get("text", "").strip()
        key = dedup_key(text)
        if key in seen_keys:
            duplicates_removed += 1
            continue
        seen_keys.add(key)
        deduped_top.append(r)
        if len(deduped_top) >= FINAL_TOP_K:
            break

    top = deduped_top
    if duplicates_removed > 0:
        log(f"Удалено дубликатов: {duplicates_removed}")

    # --- ПОСТОБРАБОТКА ---
    translated_count = 0
    final_top = []

    for r in top:
        text = r["meta"].get("text", "")
        book = r["meta"].get("book") or r["meta"].get("source") or "Неизвестно"

        if is_english(text):
            try:
                text = translate_to_ru(text)
                translated_count += 1
            except Exception:
                pass

        if len(text) > 500:
            text = text[:497] + "..."

        book_clean = book.replace(".pdf", "").replace(".txt", "").replace(".md", "").strip()

        rerank_norm = None
        raw_rerank = r.get("rerank_score")
        if raw_rerank is not None:
            try:
                rerank_norm = 1 / (1 + math.exp(-float(raw_rerank)))
            except Exception:
                rerank_norm = None

        final_top.append({
            "score": r["score"],
            "rerank_norm": rerank_norm,
            "book": book_clean,
            "text": text,
        })

    # --- СБОРКА ОТВЕТА ---
    answer = f"🔎 <b>Результаты для:</b> <i>{query}</i>\n\n"

    for i, r in enumerate(final_top, 1):
        base_pct = r["score"] * 100
        if r["rerank_norm"] is not None:
            rerank_pct = r["rerank_norm"] * 100
            metrics = f"base {base_pct:.0f}% · релевантность {rerank_pct:.0f}%"
        else:
            metrics = f"base {base_pct:.0f}%"

        answer += f"{i}. 📖 <b>{r['book']}</b>\n"
        answer += f"   <i>{metrics}</i>\n"
        answer += f"<code>{r['text']}</code>\n\n"

    if len(answer) > 3900:
        answer = answer[:3890] + "\n\n<i>... (ответ обрезан)</i>"

    # --- ЛОГИРОВАНИЕ ---
    elapsed_ms = int((time.time() - start_time) * 1000)
    avg_score = sum(r["score"] for r in top) / len(top) if top else 0.0

    log_action(
        "ask",
        query=query,
        found_chunks=len(top),
        avg_score=round(avg_score, 3),
        response_time_ms=elapsed_ms,
        extra={
            "translated": translated_count,
            "reranked": bool(rerank_fn),
            "duplicates_removed": duplicates_removed,
        },
    )
    log(f"Найдено: {len(top)}, переведено: {translated_count}, "
        f"дублей убрано: {duplicates_removed}, время: {elapsed_ms} мс")


# ============================================================
# 9. ОТПРАВКА В TELEGRAM
# ============================================================
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
chat_id = os.getenv("CHAT_ID") or os.getenv("TELEGRAM_CHAT_ID")

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
            log("✅ Отправлено в Telegram OK")
        else:
            log(f"⚠️ Telegram {r.status_code}: {r.text[:200]}", "WARN")
            log_action("ask", query=query, error=f"Telegram {r.status_code}")
    except Exception as e:
        log_action("ask", query=query, error=f"Telegram: {e}")
        log(f"⚠️ Telegram ошибка: {e}", "WARN")
else:
    print("Telegram не настроен. Локальный ответ:\n" + answer)