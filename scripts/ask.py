# ============================================================
# ARGUS — ПОИСК БЕЗ LLM
# Находит фрагменты в knowledge.json и отправляет в Telegram
# ============================================================

import os
import re
import sys
import json
import requests

KNOWLEDGE_FILE = "data/knowledge.json"

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


# --- Стоп-слова ---
STOP_WORDS = {
    "и", "в", "во", "не", "что", "он", "на", "я", "с", "со",
    "как", "а", "то", "все", "она", "так", "его", "но", "да",
    "ты", "к", "у", "же", "вы", "за", "бы", "по", "только",
    "ее", "мне", "было", "вот", "от", "меня", "еще", "нет",
    "о", "из", "ему", "теперь", "когда", "даже", "ну", "вдруг",
    "ли", "если", "уже", "или", "ни", "быть", "был", "него",
    "до", "вас", "нибудь", "опять", "уж", "вам", "ведь", "там",
    "потом", "себя", "ничего", "ей", "может", "они", "тут",
    "где", "есть", "надо", "ней", "для", "мы", "тебя", "их",
    "чем", "была", "сам", "чтоб", "без", "будто", "чего", "раз",
    "тоже", "себе", "под", "будет", "ж", "тогда", "кто", "этот",
    "того", "потому", "этого", "какой", "совсем", "ним", "здесь",
    "этом", "один", "почти", "мой", "тем", "чтобы", "нее",
    "сейчас", "были", "куда", "зачем", "всех", "никогда",
    "можно", "при", "наконец", "два", "об", "другой", "хоть",
    "после", "над", "больше", "тот", "через", "эти", "нас",
    "про", "всего", "них", "какая", "много", "разве", "три",
    "эту", "моя", "впрочем", "хорошо", "свою", "этой", "перед",
    "иногда", "лучше", "чуть", "том", "нельзя", "такой", "им",
    "более", "всегда", "конечно", "всю", "между", "это", "этот"
}

# --- Разбираем запрос ---
query_norm = normalize(query)
query_words = []
for word in query_norm.split():
    if len(word) < 3:
        continue
    if word in STOP_WORDS:
        continue
    query_words.append(word)

if not query_words:
    answer = "❌ Запрос слишком короткий."
    print(answer)
else:
    # --- Поиск ---
    results = []
    for chunk in chunks:
        text = chunk.get("text", "")
        text_norm = normalize(text)
        score = 0
        for w in query_words:
            count = text_norm.count(w)
            if count > 0:
                score = score + 1
                if count > 1:
                    score = score + 0.5
        if score > 0:
            score = score / len(query_words)
            length_bonus = min(len(text) / 1000, 1.0) * 0.3
            score = score + length_bonus
            results.append({
                "score": round(score, 3),
                "text": text
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

    if not top:
        answer = "❌ По запросу ничего не найдено."
        print(answer)
    else:
        # --- Формируем сообщение из найденных фрагментов ---
        answer = f"🔍 <b>Запрос:</b> {query}\n"
        answer = answer + f"📊 Найдено: {len(results)}\n\n"

        for i, r in enumerate(top, 1):
            # Обрезаем каждый фрагмент до 400 символов
            text = r["text"]
            if len(text) > 400:
                text = text[:400] + "..."
            answer = answer + f"<b>#{i}</b> (score {r['score']})\n{text}\n\n"

        # Обрезаем итоговый ответ под лимит Telegram
        if len(answer) > 3900:
            answer = answer[:3900] + "\n\n... (обрезано)"

        print(f"✅ Найдено фрагментов: {len(top)}")


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
                "parse_mode": "HTML"
            },
            timeout=10
        )
        print("📤 Отправлено в Telegram")
    except Exception as e:
        print(f"⚠️ Ошибка Telegram: {e}")
else:
    print("⚠️ Telegram не настроен")
    print(f"\n📄 Ответ:\n{answer}")