# ============================================================
# ARGUS — ПОИСК + ЛОГИРОВАНИЕ + ПЕРЕВОД
# ============================================================

import os
import re
import sys
import json
import time
import requests

from logger import log_action
from translate import is_english, translate_to_ru

KNOWLEDGE_FILE = "data/knowledge.json"

start_time = time.time()

# ---------- Проверка знаний ----------
if not os.path.exists(KNOWLEDGE_FILE):
    log_action("ask", error="knowledge.json не найден")
    print("❌ knowledge.json не найден.")
    sys.exit()

with open(KNOWLEDGE_FILE, "r", encoding="utf-8") as f:
    knowledge = json.load(f)

chunks = knowledge.get("chunks", [])

if not chunks:
    log_action("ask", error="База знаний пуста")
    print("❌ База знаний пуста.")
    sys.exit()

# ---------- Вопрос ----------
query = os.getenv("QUERY") or " ".join(sys.argv[1:]) or "Что такое Новая Атлантида?"
print(f"🔍 Вопрос: {query}")

# ---------- Стоп-слова ----------
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
    "более", "всегда", "конечно", "всю", "между", "это"
}


def normalize(text):
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ---------- Разбор запроса ----------
query_words = []
for w in normalize(query).split():
    if len(w) < 3:
        continue
    if w in STOP_WORDS:
        continue
    query_words.append(w)

if not query_words:
    answer = "❌ Запрос слишком короткий."
    log_action("ask", query=query, error="Короткий запрос")
    print(answer)
else:
    # ---------- Поиск ----------
    results = []
    for chunk in chunks:
        text = chunk.get("text", "")
        text_norm = normalize(text)
        score = 0
        for w in query_words:
            count = text_norm.count(w)
            if count > 0:
                score += 1
                if count > 1:
                    score += 0.5
        if score > 0:
            score = score / len(query_words)
            length_bonus = min(len(text) / 1000, 1.0) * 0.3
            score += length_bonus
            results.append({"score": round(score, 3), "text": text})

    results.sort(key=lambda x: x["score"], reverse=True)
    top = results[:5]

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
            if len(text) > 400:
                text = text[:400] + "..."
            final_top.append({"score": r["score"], "text": text})

        # ---------- Формируем ответ ----------
        answer = f"🔍 <b>Запрос:</b> {query}\n"
        answer += f"📊 Найдено: {len(results)}\n"
        if translated_count > 0:
            answer += f"🌐 Переведено с EN: {translated_count}\n"
        answer += "\n"

        for i, r in enumerate(final_top, 1):
            answer += f"<b>#{i}</b> (score {r['score']})\n{r['text']}\n\n"

        if len(answer) > 3900:
            answer = answer[:3900] + "\n\n... (обрезано)"

        # ---------- Логирование ----------
        elapsed_ms = int((time.time() - start_time) * 1000)
        log_action(
            "ask",
            query=query,
            found_chunks=len(top),
            response_time_ms=elapsed_ms,
            extra={"translated": translated_count}
        )

        print(f"✅ Найдено: {len(top)}, переведено: {translated_count}")


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
            timeout=10
        )
        print("📤 Отправлено в Telegram")
    except Exception as e:
        log_action("ask", query=query, error=f"Telegram: {e}")
        print(f"⚠️ Telegram: {e}")
else:
    print("⚠️ Telegram не настроен")
    print(f"\n📄 Ответ:\n{answer}")