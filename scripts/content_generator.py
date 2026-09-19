# ============================================================
# ARGUS — ГЕНЕРАТОР ПОСТОВ (v4)
# v4: pathlib, фикс datetime, синхронизация с collect.py v3 (market_summary.json)
# ============================================================

import os
import sys
import re
import json
import random
import requests
from datetime import datetime, timezone
from pathlib import Path

# --- Пути от корня репо ---
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

NEWS_FILE = DATA_DIR / "news_sentiment.json"
MARKET_SUMMARY = DATA_DIR / "market_summary.json"
KNOWLEDGE_FILE = DATA_DIR / "knowledge.json"
POST_FILE = DATA_DIR / "pending_post.json"
QUOTES_CACHE = DATA_DIR / "quotes_pool.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def load_json(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ЦИТАТЫ ИЗ КНИГ
# ============================================================
PHILOSOPHY_HINTS = [
    "знани", "истин", "мудр", "человек", "мысл", "опыт", "наук",
    "природ", "разум", "сила", "власт", "свобод", "доброд",
    "смысл", "правд", "развити", "прогресс", "будущ", "жизн",
    "душ", "сердц", "страст", "вол", "характер", "судьб",
    "рынок", "цен", "риск", "прибыл", "убыт", "капитал",
    "стратег", "решен", "правил", "дисциплин", "эмоци",
    "анализ", "прогноз", "вероятн", "данн", "модел",
    "любов", "вера", "надежд", "страх", "смел", "мужеств",
    "ошибк", "урок", "цель", "путь",
    "knowledge", "truth", "wisdom", "power", "mind", "reason",
    "market", "risk", "profit", "strategy", "decision",
]


def extract_quotes_from_books(max_quotes=500):
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

        sentences = re.split(r"(?<=[.!?])\s+", text)

        for s in sentences:
            s = s.strip()
            if len(s) < 60 or len(s) > 220:
                continue
            if s in seen:
                continue

            low = s.lower()
            if not any(h in low for h in PHILOSOPHY_HINTS):
                continue
            if any(c in s for c in ["http", "@", "…", "(", ")", "[", "]"]):
                continue
            if not s[0].isupper():
                continue

            seen.add(s)
            quotes.append({
                "text": s,
                "book": book,
                "source": book.replace(".pdf", "").replace(".epub", "").strip(),
            })

            if len(quotes) >= max_quotes:
                break

        if len(quotes) >= max_quotes:
            break

    save_json(QUOTES_CACHE, {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total": len(quotes),
        "quotes": quotes,
    })
    return quotes


# ============================================================
# БРИДЖ (Связка философии и рынка)
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
        "рост без плана — лотерея. План без роста — тоже лотерея.",
        "когда все покупают — дисциплина важнее интуиции.",
    ],
    "negative": [
        "страх — плохой советчик. Лучший советчик — твой дневник сделок.",
        "в падении видно, кто управляет риском, а кто гадает.",
        "рынок падает не чтобы наказать, а чтобы проверить твои правила.",
        "просадка — не конец, а тест на дисциплину.",
    ],
    "neutral": [
        "когда рынок спокоен — самое время учиться. В шторме учиться поздно.",
        "нейтральный рынок — окно для анализа, а не для агрессии.",
        "спокойствие — лучший момент для бэктеста и размышлений.",
        "равновесие — пауза перед движением. Готовься.",
    ],
}


def build_bridge(score):
    # score ожидается от -1.0 до 1.0 (или аналогичная шкала)
    if score > 0.15:
        bucket = "positive"
    elif score < -0.15:
        bucket = "negative"
    else:
        bucket = "neutral"

    idea = random.choice(MARKET_IDEAS[bucket])
    template = random.choice(BRIDGE_TEMPLATES)
    return template.format(idea=idea)


# ============================================================
# ДАННЫЕ (Синхронизировано с collect.py v3)
# ============================================================
def get_market_data():
    """Читает актуальные цены и сентимент из market_summary.json (или fallback на news_sentiment.json)"""
    prices = {}
    sentiment = {
        "mood": "нейтральное",
        "score": 0.0,
        "top_bull": [],
        "top_bear": [],
        "total": 0,
    }

    # 1. Пробуем прочитать market_summary.json (создаётся collect.py v3)
    summary = load_json(MARKET_SUMMARY, {})
    if "assets" in summary:
        for k, v in summary["assets"].items():
            if "BTC" in k.upper(): prices["btc"] = v
            if "ETH" in k.upper(): prices["eth"] = v
            
    if "sentiment" in summary:
        fg = summary["sentiment"]
        # Нормализуем Fear & Greed (0-100) в шкалу -1.0 ... 1.0
        sentiment["score"] = (fg.get("value", 50) - 50) / 50.0
        sentiment["mood"] = fg.get("classification", "нейтральное")

    # 2. Дополняем или перезаписываем сентимент из news_sentiment.json, если он есть
    news = load_json(NEWS_FILE, {})
    if news.get("avg_sentiment") is not None:
        sentiment["score"] = news.get("avg_sentiment", sentiment["score"])
        sentiment["mood"] = news.get("mood", sentiment["mood"])
        sentiment["top_bull"] = news.get("top_bullish", sentiment["top_bull"])
        sentiment["top_bear"] = news.get("top_bearish", sentiment["top_bear"])
        sentiment["total"] = news.get("total_news", sentiment["total"])
        
    return prices, sentiment


# ============================================================
# ГЕНЕРАТОРЫ
# ============================================================
def gen_morning():
    prices, sent = get_market_data()

    lines = ["🌅 <b>Утренняя сводка ARGUS</b>", ""]
    if prices.get("btc"):
        lines.append(f"💰 <b>BTC:</b> ${prices['btc']:,.0f}")
    if prices.get("eth"):
        lines.append(f"💰 <b>ETH:</b> ${prices['eth']:,.0f}")
    
    lines.append("")
    lines.append(f"📊 Настроение рынка: <b>{sent['mood']}</b>")
    lines.append(f"📈 Сентимент: <code>{sent['score']:+.3f}</code>")

    if sent["top_bull"]:
        item = random.choice(sent["top_bull"])
        if isinstance(item, dict) and item.get("title"):
            lines.append(f"\n✅ <b>Позитив:</b> {str(item['title'])[:120]}")
    elif isinstance(sent["top_bull"], str):
        lines.append(f"\n✅ <b>Позитив:</b> {sent['top_bull'][:120]}")

    if sent["top_bear"]:
        item = random.choice(sent["top_bear"])
        if isinstance(item, dict) and item.get("title"):
            lines.append(f"\n⚠️ <b>Негатив:</b> {str(item['title'])[:120]}")
    elif isinstance(sent["top_bear"], str):
        lines.append(f"\n⚠️ <b>Негатив:</b> {sent['top_bear'][:120]}")

    return "\n".join(lines), "morning"


def gen_philosophy():
    quotes = extract_quotes_from_books()
    _, sent = get_market_data()

    if not quotes:
        return gen_insight()

    q = random.choice(quotes)

    text = "📜 <b>Заметка дня</b>\n\n"
    text += f"<i>«{q['text']}»</i>\n"
    text += f"— {q['source']}\n\n"
    text += build_bridge(sent["score"])
    return text, "philosophy"


def gen_insight():
    _, sent = get_market_data()
    prices, _ = get_market_data()

    pool = []
    for x in sent["top_bull"]:
        if isinstance(x, dict) and x.get("title"):
            pool.append(x["title"])
        elif isinstance(x, str):
            pool.append(x)
            
    for x in sent["top_bear"]:
        if isinstance(x, dict) and x.get("title"):
            pool.append(x["title"])
        elif isinstance(x, str):
            pool.append(x)

    if not pool:
        return gen_philosophy()

    news_title = random.choice(pool)

    text = "💡 <b>Инсайт дня</b>\n\n"
    text += f"{str(news_title)[:140]}\n\n"

    if prices.get("btc"):
        text += f"📌 BTC сейчас: <b>${prices['btc']:,.0f}</b>\n\n"

    text += build_bridge(sent["score"])
    return text, "insight"


# ============================================================
# ВЫБОР ТИПА (по времени UTC)
# ============================================================
def generate_post():
    hour = datetime.now(timezone.utc).hour

    if hour < 10:
        post_type = "morning"
    elif hour < 16:
        post_type = "philosophy"
    else:
        post_type = "insight"

    if post_type == "morning":
        text, _ = gen_morning()
    elif post_type == "philosophy":
        text, _ = gen_philosophy()
    else:
        text, _ = gen_insight()

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
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    })

    kb = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": "post_pub:go"},
            {"text": "🔄 Перегенерировать", "callback_data": "post_pub:regen"},
            {"text": "❌ Удалить", "callback_data": "post_skip:go"},
        ]]
    }

    header = f"📝 <b>Черновик</b> ({post_type})\n\n"
    full = header + text

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": full[:4000],
                "reply_markup": kb,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        print("tg error: " + str(e))
        return False


def main():
    print("ARGUS CONTENT GENERATOR v4")
    print("=" * 50)

    quotes = extract_quotes_from_books()
    print(f"Цитат в пуле: {len(quotes)}")

    text, post_type = generate_post()
    print(f"Тип: {post_type}")
    print(f"Длина: {len(text)}")

    if send_draft(text, post_type):
        print("✅ Отправлено в Telegram")
    else:
        print("❌ Ошибка отправки")


if __name__ == "__main__":
    main()
