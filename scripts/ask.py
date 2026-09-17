# ============================================================
# ARGUS — ПОИСК + LLM + ОТПРАВКА В TELEGRAM (v2)
# С автоматическим fallback между бесплатными моделями
# ============================================================

import os
import re
import sys
import json
import requests

KNOWLEDGE_FILE = "data/knowledge.json"

# --- Список бесплатных моделей в порядке приоритета ---
FREE_MODELS = [
    "z-ai/glm-4.5-air:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
    "deepseek/deepseek-r1-0528:free",
    "nvidia/nemotron-3-nano-30b-a3b:free",
    "openai/gpt-oss-20b:free",
    "qwen/qwen3-vl-235b-a22b-thinking:free",
    "openrouter/free"   # универсальный роутер — последний резерв
]

# --- Проверка знаний ---
if not os.path.exists(KNOWLEDGE_FILE):
    print("❌ knowledge.json не найден.")
    sys.exit()

with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = knowledge.get("chunks", [])
if not chunks:
    print("❌ База знаний пуста.")
    sys.exit()

# --- Вопрос ---
query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Что такое Новая Атлантида?"
print(f"🔍 Вопрос: {query}")

# --- Нормализация ---
def normalize(text):
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

# --- Поиск релевантных чанков ---
query_words = [w for w in normalize(query).split() if len(w) > 2]

results = []
for chunk in chunks:
    text_norm = normalize(chunk.get("text", ""))
    score = sum(text_norm.count(w) for w in query_words)
    if score > 0:
        results.append((score, chunk))

results.sort(key=lambda x: x[0], reverse=True)
top_chunks = [r[1] for r in results[:5]]

if not top_chunks:
    answer = "❌ По запросу ничего не найдено в базе знаний."
    print(answer)
else:
    context = "\n\n".join([c["text"] for c in top_chunks])

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        answer = "❌ OPENROUTER_API_KEY не задан."
        print(answer)
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/nitrolm/argus-core",
            "X-Title": "ARGUS"
        }

        prompt = f"""Ответь на вопрос, используя ТОЛЬКО предоставленный контекст.
Если ответа в контексте нет — скажи "В базе знаний нет ответа".

Контекст:
{context}

Вопрос: {query}

Ответ:"""

        payload = {
            "models": FREE_MODELS,   # ← массив моделей для fallback
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }

        answer = None
        used_model = None

        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=90
            )
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"]
            used_model = data.get("model", "unknown")
            print(f"✅ Ответ получен от модели: {used_model}")
        except Exception as e:
            answer = f"❌ Все модели недоступны. Ошибка: {e}"
            print(answer)

# --- Отправка в Telegram ---
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
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        print("📤 Ответ отправлен в Telegram")
    except Exception as e:
        print(f"⚠️ Ошибка отправки в Telegram: {e}")
else:
    print("⚠️ Telegram не настроен")
    print(f"\n📄 Ответ:\n{answer}")