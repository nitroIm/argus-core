# ============================================================
# ARGUS — ГЕНЕРАТОР ПОСТОВ v2
# Цитаты берутся из собственных книг (knowledge.json)
# ============================================================

import os
import re
import json
import random
import requests
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
DATA_DIR = os.path.join(REPO_ROOT, "data")

NEWS_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
PRICE_FILE = os.path.join(DATA_DIR, "price_history.json")
KNOWLEDGE_FILE = os.path.join(DATA_DIR, "knowledge.json")
POST_FILE = os.path.join(DATA_DIR, "pending_post.json")
HISTORY_FILE = os.path.join(DATA_DIR, "post_history.json")
QUOTES_CACHE = os.path.join(DATA_DIR, "quotes_pool.json")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ИЗВЛЕЧЕНИЕ ЦИТАТ ИЗ КНИГ
# ============================================================
PHILOSOPHY_HINTS = [
    "знани", "истин", "мудр", "человек", "мысл", "опыт", "наук",
    "природ", "разум", "сила", "власт", "свобод", "доброд",
    "смысл", "правд", "развити", "прогресс", "будущ",
    "knowledge", "truth", "wisdom", "power", "mind", "reason",
]


def extract_quotes_from_books(max_quotes=200):
    """
    Проходит по knowledge.json, ищет короткие «афористичные» предложения.
    Кеширует результат в quotes_pool.json.
    """
    cached = load_json(QUOTES_CACHE, {})
    if cached.get("quotes") and len(cached.get("quotes", [])) > 20:
        return cached["quotes"]

    kb = load_json(KNOWLEDGE_FILE, {})
    chunks = kb.get("chunks", [])
    if not chunks:
        return []

    quotes = []
    seen = set()

    for chunk in chunks:
        text = chunk.get("text", "")
        book = chunk.get("book", "?")
        if not text:
            continue

        # Разбиваем на предложения
        sentences = re.split(r"(?<=[.!?])\s+", text)

        for s in sentences:
            s = s.strip()
            if len(s) < 60 or len(s) > 220:
                continue
            if s in seen:
                continue

            low = s.lower()
            # Должно содержать философское слово
            if not any(h in low for h in PHILOSOPHY_HINTS):
                continue
            # Не должно быть мусора
            if any(c in s for c in ["http", "@", "…", "(", ")"]):
                continue
            # Должно начинаться с заглавной
            if not s[0].isupper():
                continue

            seen.add(s)
            quotes.append({
                "text": s,
                "book": book,
                "source": book.replace(".pdf", ""),
            })

            if len(quotes) >= max_quotes:
                break

        if len(quotes) >= max_quotes:
            break

    save_json(QUOTES_CACHE, {"generated_at": datetime.utcnow().isoformat(),
                             "total": len(quotes), "quotes": quotes})
    return quotes


# ============================================================
# ГЕНЕРАЦИЯ БРИДЖА (связь цитаты с рынком)
# ============================================================
BRIDGE_TEMPLATES = [
    "Что это значит для трейдинга? {idea}",
    "Как это работает на рынке? {idea}",
    "Применим к трейдингу: {idea}",
    "Связь с рынком прямая: {idea}",
    "Трейдеру на заметку: {idea}",
]

MARKET_IDEAS = {
    "positive": [
        "рынок растёт, но эйфория — плохой советчик. Правило важнее эмоции.",
        "все видят прибыль, но не все видят риск. Именно поэтому 90% теряют.",
        "рост без плана — это лотерея. План без роста — тоже лотерея.",
        "когда все покупают, дисциплина важнее интуиции.",
    ],
    "negative": [
        "страх — плохой советчик. Лучший советчик — твой дневник сделок.",
        "в падении видно, кто действительно управляет риском, а кто гадает.",
        "рынок падает не чтобы тебя наказать, а чтобы проверить твои правила.",
        "просадка — не конец, а тест на дисциплину.",
    ],
    "neutral": [
        "когда рынок спокоен — самое время учиться. В шторме учиться поздно.",
        "нейтральный рынок — это окно для анализа, а не для агрессии.",
        "спокойствие на рынке — лучший момент для бэктеста и размышлений.",
        "равновесие — это пауза перед движением. Готовься.",
    ],
}


def build_bridge(sentiment_score):
    if sentiment_score > 0.15:
        bucket = "positive"
    elif sentiment_score < -0.15:
        bucket = "negative"
    else:
        bucket = "neutral"

    idea = random.choice(MARKET_IDEAS[bucket])
    template = random.choice(BRIDGE_TEMPLATES)
    return template.format(idea=idea)


# ============================================================
# ДАННЫЕ
# ============================================================
def get_prices():
    data = load_json(PRICE_FILE, [])
    result = {}
    if isinstance(data, dict):
        for k in data.keys():
            kk = k.upper()
            if kk.startswith("BTC") and "btc" not in result:
                arr = data[k]
                if isinstance(arr, list) and arr:
                    last = arr[-1]
                    if isinstance(last, dict):
                        result["btc"] = last.get("close") or last.get("c")
                    elif isinstance(last, (int, float)):
                        result["btc"] = last
            if kk.startswith("ETH") and "eth" not in result:
                arr = data[k]
                if isinstance(arr, list) and arr:
                    last = arr[-1]
                    if isinstance(last, dict):
                        result["eth"] = last.get("close") or last.get("c")
                    elif isinstance(last, (int, float)):
                        result["eth"] = last
    return result


def get_sentiment():
    data = load_json(NEWS_FILE, {})
    return {
        "mood": data.get("mood", "нейтральное"),
        "score": data.get("avg_sentiment", 0),
        "top_bull": (data.get("top_bullish") or [{}])[:1],
        "top_bear": (data.get("top_bearish") or [{}])[:1],
        "total": data.get("total_news", 0),
    }


# ============================================================
# ГЕНЕРАТОРЫ
# ============================================================
def gen_morning():
    prices = get_prices()
    sent = get_sentiment()

    lines = ["Утренняя сводка ARGUS"]
    lines.append("")
    if prices.get("btc"):
        lines.append("BTC: $" + "{:,.0f}".format(prices["btc"]))
    if prices.get("eth"):
        lines.append("ETH: $" + "{:,.0f}".format(prices["eth"]))
    lines.append("")
    lines.append("Настроение рынка: " + str(sent["mood"]))
    lines.append("Сентимент: " + "{:+.3f}".format(sent["score"]))

    if sent["top_bull"] and sent["top_bull"][0].get("title"):
        lines.append("")
        lines.append("Главное позитивное:")
        lines.append("- " + str(sent["top_bull"][0]["title"])[:120])

    if sent["top_bear"] and sent["top_bear"][0].get("title"):
        lines.append("")
        lines.append("Главное негативное:")
        lines.append("- " + str(sent["top_bear"][0]["title"])[:120])

    return "\n".join(lines), "morning"


def gen_philosophy():
    quotes = extract_quotes_from_books()
    sent = get_sentiment()

    if not quotes:
        # Резерв — если цитат нет
        return gen_insight()

    q = random.choice(quotes)

    text = "Заметка дня"
    text += "\n\n"
    text += '"' + q["text"] + '"\n'
    text += "— " + q["source"]
    text += "\n\n"
    text += build_bridge(sent["score"])
    return text, "philosophy"


def gen_insight():
    sent = get_sentiment()
    prices = get_prices()

    news_title = ""
    if sent["top_bear"] and sent["top_bear"][0].get("title"):
        news_title = sent["top_bear"][0]["title"]
    elif sent["top_bull"] and sent["top_bull"][0].get("title"):
        news_title = sent["top_bull"][0]["title"]

    if not news_title:
        return gen_philosophy()

    text = "Инсайт дня\n\n"
    text += str(news_title)[:140] + "\n\n"

    if prices.get("btc"):
        text += "BTC сейчас: $" + "{:,.0f}".format(prices["btc"]) + "\n\n"

    text += build_bridge(sent["score"])
    return text, "insight"


# ============================================================
# ВЫБОР ТИПА С РОТАЦИЕЙ
# ============================================================
def generate_post():
    history = load_json(HISTORY_FILE, {"types": []})
    last_types = history.get("types", [])[-3:]

    now_hour = datetime.utcnow().hour
    if 4 <= now_hour < 10:
        candidates = ["morning", "philosophy", "insight"]
    elif 10 <= now_hour < 17:
        candidates = ["philosophy", "insight", "morning"]
    else:
        candidates = ["insight", "philosophy", "morning"]

    fresh = [c for c in candidates if c not in last_types]
    if not fresh:
        fresh = candidates

    post_type = fresh[0]

    if post_type == "morning":
        text, _ = gen_morning()
    elif post_type == "philosophy":
        text, _ = gen_philosophy()
    else:
        text, _ = gen_insight()

    history["types"].append(post_type)
    if len(history["types"]) > 50:
        history["types"] = history["types"][-50:]
    save_json(HISTORY_FILE, history)

    return text, post_type


# ============================================================
# ОТПРАВКА
# ============================================================
def send_draft(text, post_type):
    if not BOT_TOKEN or not CHAT_ID:
        print("no BOT_TOKEN / CHAT_ID")
        return False

    save_json(POST_FILE, {
        "text": text,
        "type": post_type,
        "created_at": datetime.utcnow().isoformat(),
        "status": "pending",
    })

    kb = {
        "inline_keyboard": [[
            {"text": "Опубликовать", "callback_data": "post_pub:go"},
            {"text": "Перегенерировать", "callback_data": "post_pub:regen"},
            {"text": "Удалить", "callback_data": "post_skip:go"},
        ]]
    }

    header = "Черновик (" + post_type + ")\n\n"
    full = header + text

    try:
        r = requests.post(
            "https://api.telegram.org/bot" + BOT_TOKEN + "/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": full[:4000],
                "reply_markup": kb,
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print("tg error: " + str(e))
        return False


def main():
    print("ARGUS CONTENT v2")
    print("=" * 50)

    # Прогреваем пул цитат из книг
    quotes = extract_quotes_from_books()
    print("Цитат в пуле: " + str(len(quotes)))

    text, post_type = generate_post()
    print("Тип: " + post_type)
    print("Длина: " + str(len(text)))

    if send_draft(text, post_type):
        print("Отправлено в Telegram")
    else:
        print("Ошибка отправки")


if __name__ == "__main__":
    main()