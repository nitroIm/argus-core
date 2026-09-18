# ============================================================
# ARGUS — СЕМАНТИЧЕСКИЙ ПОИСК + ЛОГИРОВАНИЕ + ПЕРЕВОД
# Ищет ответы ПО СМЫСЛУ через FAISS + свою модель эмбеддингов
# ============================================================

import os
import sys
import json
import time
import numpy as np
import faiss
import requests
from sentence_transformers import SentenceTransformer

from logger import log_action
from translate import is_english, translate_to_ru

# ---------- Пути ----------
MODEL_DIR = "models/argus-embeddings"
INDEX_FILE = "data/faiss.index"
CHUNKS_FILE = "data/chunks_for_index.json"

start_time = time.time()

# ---------- Проверки ----------
if not os.path.exists(MODEL_DIR):
    log_action("ask", error="Модель не найдена")
    print("❌ Модель не найдена. Сначала запусти ARGUS Train Model.")
    sys.exit(1)

if not os.path.exists(INDEX_FILE):
    log_action("ask", error="faiss.index не найден")
    print("❌ FAISS-индекс не найден.")
    sys.exit(1)

if not os.path.exists(CHUNKS_FILE):
    log_action("ask", error="chunks_for_index.json не найден")
    print("❌ Файл чанков не найден.")
    sys.exit(1)

# ---------- Загрузка ----------
print("📦 Загружаю модель...")
model = SentenceTransformer(MODEL_DIR)

print("📦 Загружаю индекс...")
index = faiss.read_index(INDEX_FILE)

with open(CHUNKS_FILE, "r", encoding="utf-8") as f:
    chunks = json.load(f)

print(f"✅ Индекс: {index.ntotal} векторов, чанков: {len(chunks)}")

# ---------- Вопрос ----------
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "Что такое Новая Атлантида?"
print(f"🔍 Вопрос: {query}")

# ---------- Семантический поиск ----------
query_vec = model.encode([query]).astype("float32")
distances, indices = index.search(query_vec, k=5)

# ---------- Собираем результаты ----------
top = []
for i, idx in enumerate(indices[0]):
    if 0 <= idx < len(chunks):
        top.append({
            "score": float(distances[0][i]),
            "text": chunks[idx]
        })

if not top:
    answer = "❌ По запросу ничего не найдено."
    log_action("ask", query=query, found_chunks=0)
    print(answer)
else:
    # ---------- Перевод английских фрагментов ----------
    translated_count = 0
    final_top = []

    for r in top:
        text = r["text"]
        if is_english(text):
            try:
                text = translate_to_ru(text)
                translated_count += 1
            except Exception as e:
                print(f"⚠️ Ошибка перевода: {e}")

        if len(text) > 500:
            text = text[:500] + "..."

        final_top.append({"score": r["score"], "text": text})

    # ---------- Формируем ответ ----------
    answer = f"🔍 <b>Запрос:</b> {query}\n"
    answer += f"📊 Найдено: {len(top)}\n"
    if translated_count > 0:
        answer += f"🌐 Переведено с EN: {translated_count}\n"
    answer += "\n"

    for i, r in enumerate(final_top, 1):
        answer += f"<b>#{i}</b> (расстояние {r['score']:.3f})\n{r['text']}\n\n"

    if len(answer) > 3900:
        answer = answer[:3900] + "\n\n... (обрезано)"

    # ---------- Логирование ----------
    elapsed_ms = int((time.time() - start_time) * 1000)
    avg_dist = sum(r["score"] for r in top) / len(top)

    log_action(
        "ask",
        query=query,
        found_chunks=len(top),
        avg_distance=round(avg_dist, 3),
        response_time_ms=elapsed_ms,
        extra={"translated": translated_count}
    )

    print(f"✅ Найдено: {len(top)}, переведено: {translated_count}, время: {elapsed_ms} мс")


# ---------- Отправка в Telegram ----------
bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
default_chat = os.getenv("TELEGRAM_CHAT_ID")
chat_id = os.getenv("CHAT_ID") or default_chat

if bot_token and chat_id:
    try:
        requests.get(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            params={
                "chat_id": chat_id,
                "text": answer[:4000],
                "parse_mode": "HTML"
            },
            timeout=15
        )
        print("📤 Отправлено в Telegram")
    except Exception as e:
        log_action("ask", query=query, error=f"Telegram: {e}")
        print(f"⚠️ Telegram: {e}")
else:
    print("⚠️ Telegram не настроен")
    print(f"\n📄 Ответ:\n{answer}")