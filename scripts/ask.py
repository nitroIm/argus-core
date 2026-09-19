# ============================================================
# ARGUS — СЕМАНТИЧЕСКИЙ ПОИСК (v5)
# v5: синхронизация с build_index (meta.json, префиксы E5), pathlib, безопасные импорты
# ============================================================

import os
import sys
import json
import time
import faiss
import requests
from pathlib import Path
from sentence_transformers import SentenceTransformer

# --- Безопасные импорты (чтобы скрипт не падал, если модулей нет) ---
try:
    from logger import log_action
except ImportError:
    def log_action(*args, **kwargs): pass  # Заглушка, если logger нет

try:
    from translate import is_english, translate_to_ru
except ImportError:
    def is_english(text): return False
    def translate_to_ru(text): return text

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"
MODELS_DIR = REPO_ROOT / "models"

# Файлы
INDEX_FILE = DATA_DIR / "faiss.index"
META_FILE = DATA_DIR / "chunks_meta.json"  # <-- ИСПРАВЛЕНО: читаем метаданные, а не просто тексты

# Настройки поиска
FAISS_TOP_K = 20
FINAL_TOP_K = 5

start_time = time.time()

# --- 1. Проверки ---
if not INDEX_FILE.exists():
    log_action("ask", error="faiss.index not found")
    print("❌ FAISS индекс не найден. Сначала запусти build_index.")
    sys.exit(1)

if not META_FILE.exists():
    log_action("ask", error="chunks_meta.json not found")
    print("❌ Файл метаданных чанков не найден.")
    sys.exit(1)

# --- 2. Загрузка модели (Динамический выбор: обученная или E5) ---
TRAINED_MODEL = MODELS_DIR / "argus-embeddings"
BASE_MODEL = "intfloat/multilingual-e5-small"

if TRAINED_MODEL.exists() and (TRAINED_MODEL / "config.json").exists():
    model_path = str(TRAINED_MODEL)
    use_prefix = False
    print(f"🎓 Используем обученную модель: {model_path}")
else:
    model_path = BASE_MODEL
    use_prefix = True
    print(f"📦 Используем базовую модель: {model_path} (с префиксом 'query: ')")

print("Загружаю модель...")
model = SentenceTransformer(model_path)

print("Загружаю индекс...")
index = faiss.read_index(str(INDEX_FILE))

with open(META_FILE, "r", encoding="utf-8") as f:
    meta_chunks = json.load(f)

print(f"Индекс: {index.ntotal} векторов, метаданных чанков: {len(meta_chunks)}")

# --- 3. Reranker (опционально) ---
rerank_fn = None
try:
    from reranker import rerank as rerank_fn
    print("Reranker подключён")
except Exception:
    pass  # Reranker недоступен, используем только FAISS

# --- 4. Вопрос ---
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "Что такое имбаланс?"
print(f"Вопрос: {query}")

# --- 5. Поиск ---
# ВАЖНО: добавляем префикс "query: " только если используем базовую E5
search_query = f"query: {query}" if use_prefix else query

query_vec = model.encode([search_query], normalize_embeddings=True).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

candidates = []
for i, idx in enumerate(indices[0]):
    if 0 <= idx < len(meta_chunks):
        candidates.append({
            "score": float(distances[0][i]),
            "meta": meta_chunks[idx],  # <-- Теперь сохраняем полные метаданные
            "index": int(idx),
        })

if not candidates or all(c["score"] < 0.3 for c in candidates):  # Фильтр совсем мусорных совпадений
    answer = "🔎 По запросу ничего релевантного не найдено в базе знаний."
    log_action("ask", query=query, found_chunks=0)
    print(answer)
else:
    # --- 6. Reranker ---
    if rerank_fn:
        try:
            # Передаем тексты для rerank
            texts_for_rerank = [c["meta"].get("text", "") for c in candidates]
            top_indices = rerank_fn(query, texts_for_rerank, top_k=FINAL_TOP_K)
            top = [candidates[i] for i in top_indices if i < len(candidates)]
            print(f"Reranker отсортировал {len(candidates)} -> {len(top)}")
        except Exception as e:
            print(f"Reranker упал: {e}, использую FAISS-порядок")
            top = candidates[:FINAL_TOP_K]
    else:
        top = candidates[:FINAL_TOP_K]

    # --- 7. Постобработка (перевод и обрезка) ---
    translated_count = 0
    final_top = []

    for r in top:
        text = r["meta"].get("text", "")
        book = r["meta"].get("book", r["meta"].get("source", "Неизвестно"))
        
        if is_english(text):
            try:
                text = translate_to_ru(text)
                translated_count += 1
            except Exception:
                pass  # Игнорируем ошибки перевода, оставляем оригинал

        # Безопасная обрезка
        if len(text) > 500:
            text = text[:497] + "..."

        final_top.append({
            "score": r["score"],
            "rerank_score": r.get("rerank_score"),
            "book": book,
            "text": text,
        })

    # --- 8. Формирование ответа ---
    answer = f"🔎 <b>Результаты для:</b> <i>{query}</i>\n\n"
    
    for i, r in enumerate(final_top, 1):
        score_str = f"{r['score']:.2f}"
        if r.get("rerank_score") is not None:
            score_str += f" (rerank: {r['rerank_score']:.2f})"
            
        answer += f"{i}. <b>{r['book']}</b> (совпадение: {score_str})\n"
        answer += f"<code>{r['text']}</code>\n\n"

    # Безопасная обрезка для Telegram (максимум 4000 символов)
    if len(answer) > 3900:
        answer = answer[:3890] + "\n\n<i>... (ответ обрезан из-за длины)</i>"

    # --- 9. Логирование для Observer/Analyzer ---
    elapsed_ms = int((time.time() - start_time) * 1000)
    avg_score = sum(r["score"] for r in top) / len(top) if top else 0.0

    log_action(
        "ask",
        query=query,
        found_chunks=len(top),
        avg_score=round(avg_score, 3),  # <-- ИСПРАВЛЕНО: avg_score вместо avg_distance (т.к. у нас Inner Product)
        response_time_ms=elapsed_ms,
        extra={"translated": translated_count, "reranked": bool(rerank_fn)},
    )

    print(f"Найдено: {len(top)}, переведено: {translated_count}, время: {elapsed_ms} мс")


# --- 10. Отправка в Telegram ---
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
            print("✅ Отправлено в Telegram OK")
        else:
            print(f"⚠️ Telegram {r.status_code}: {r.text[:200]}")
            log_action("ask", query=query, error=f"Telegram {r.status_code}")
    except Exception as e:
        log_action("ask", query=query, error=f"Telegram: {e}")
        print(f"⚠️ Telegram ошибка: {e}")
else:
    print("Telegram не настроен. Локальный ответ:\n" + answer)
