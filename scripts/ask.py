# ============================================================
# ARGUS — СЕМАНТИЧЕСКИЙ ПОИСК (v3)
# + Reranker (cross-encoder) для точной пересортировки
# ============================================================

import os
import sys
import json
import time
import faiss
import requests
from sentence_transformers import SentenceTransformer

from logger import log_action
from translate import is_english, translate_to_ru

# --- Пути от корня репо ---
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

MODEL_NAME = "intfloat/multilingual-e5-small"
INDEX_FILE = os.path.join(DATA_DIR, "faiss.index")
CHUNKS_FILE = os.path.join(DATA_DIR, "chunks_for_index.json")

# Сколько кандидатов берём из FAISS перед reranker
FAISS_TOP_K = 20
# Сколько оставляем в финальном ответе
FINAL_TOP_K = 5

start_time = time.time()

# --- Проверки ---
if not os.path.exists(INDEX_FILE):
    log_action("ask", error="faiss.index not found")
    print("❌ FAISS индекс не найден.")
    sys.exit(1)

if not os.path.exists(CHUNKS_FILE):
    log_action("ask", error="chunks_for_index.json not found")
    print("❌ Файл чанков не найден.")
    sys.exit(1)

# --- Загрузка ---
print("📦 Загружаю модель...")
model = SentenceTransformer(MODEL_NAME)

print("📦 Загружаю индекс...")
index = faiss.read_index(INDEX_FILE)

with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
    chunks = json.load(f)

print("✅ Индекс: " + str(index.ntotal) + " векторов, чанков: " + str(len(chunks)))

# --- Reranker ---
rerank_fn = None
try:
    from reranker import rerank as rerank_fn
    print("✅ Reranker подключён")
except Exception as e:
    print("⚠️ Reranker недоступен: " + str(e))
    rerank_fn = None

# --- Вопрос ---
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "Что такое Новая Атлантида?"
print("🔍 Вопрос: " + query)

# --- Поиск в FAISS ---
query_vec = model.encode([query], normalize_embeddings=True).astype("float32")
distances, indices = index.search(query_vec, k=FAISS_TOP_K)

# --- Собираем кандидатов ---
candidates = []
for i, idx in enumerate(indices[0]):
    if 0 <= idx < len(chunks):
        candidates.append({
            "score": float(distances[0][i]),
            "text": chunks[idx],
            "index": int(idx),
        })

if not candidates:
    answer = "❌ По запросу ничего не найдено."
    log_action("ask", query=query, found_chunks=0)
    print(answer)
else:
    # --- Reranker ---
    if rerank_fn:
        try:
            top = rerank_fn(query, candidates, top_k=FINAL_TOP_K)
            print("🎯 Reranker отсортировал " + str(len(candidates)) + " -> " + str(len(top)))
        except Exception as e:
            print("⚠️ Reranker упал: " + str(e) + ", использую FAISS-порядок")
            top = candidates[:FINAL_TOP_K]
    else:
        top = candidates[:FINAL_TOP_K]

    # --- Перевод английских фрагментов ---
    translated_count = 0
    final_top = []

    for r in top:
        text = r["text"]
        if is_english(text):
            try:
                text = translate_to_ru(text)
                translated_count += 1
            except Exception as e:
                print("⚠️ Перевод: " + str(e))

        if len(text) > 500:
            text = text[:500] + "..."

        final_top.append({
            "score": r["score"],
            "rerank_score": r.get("rerank_score"),
            "text": text,
        })

    # --- Формируем ответ ---
    answer = "🔍 <b>Запрос:</b> " + query + "\n"
    answer += "📊 Найдено: " + str(len(top)) + "\n"
    if rerank_fn:
        answer += "🎯 Reranker: активен\n"
    if translated_count > 0:
        answer += "🌐 Переведено с EN: " + str(translated_count) + "\n"
    answer += "\n"

    for i, r in enumerate(final_top, 1):
        if r.get("rerank_score") is not None:
            answer += "<b>#" + str(i) + "</b> (score " + "{:.3f}".format(r["score"]) + ", rerank " + "{:.3f}".format(r["rerank_score"]) + ")\n"
        else:
            answer += "<b>#" + str(i) + "</b> (score " + "{:.3f}".format(r["score"]) + ")\n"
        answer += r["text"] + "\n\n"

    if len(answer) > 3900:
        answer = answer[:3900] + "\n\n... (обрезано)"

    # --- Логирование ---
    elapsed_ms = int((time.time() - start_time) * 1000)
    avg_score = sum(r["score"] for r in top) / len(top)

    log_action(
        "ask",
        query=query,
        found_chunks=len(top),
        avg_distance=round(avg_score, 3),
        response_time_ms=elapsed_ms,
        extra={"translated": translated_count, "reranked": bool(rerank_fn)},
    )

    print("✅ Найдено: " + str(len(top)) + ", переведено: " + str(translated_count) + ", время: " + str(elapsed_ms) + " мс")


# --- Отправка в Telegram ---
bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
default_chat = os.getenv("TELEGRAM_CHAT_ID")
chat_id = os.getenv("CHAT_ID") or default_chat

if bot_token and chat_id:
    try:
        requests.post(
            "https://api.telegram.org/bot" + bot_token + "/sendMessage",
            data={
                "chat_id": chat_id,
                "text": answer[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
            timeout=15,
        )
        print("📤 Отправлено в Telegram")
    except Exception as e:
        log_action("ask", query=query, error="Telegram: " + str(e))
        print("⚠️ Telegram: " + str(e))
else:
    print("⚠️ Telegram не настроен")
    print("\n📄 Ответ:\n" + answer)