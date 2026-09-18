# ============================================================
# ARGUS — АНАЛИЗ НОВОСТЕЙ
# Собирает новости из RSS, считает сентимент по словам
# ============================================================

import os
import re
import json
import requests
from datetime import datetime, timedelta


# ============================================================
# ИСТОЧНИКИ RSS
# ============================================================
FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "lang": "en"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss", "lang": "en"},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/feed", "lang": "en"},
    {"name": "ForkLog", "url": "https://forklog.com/feed", "lang": "ru"},
    {"name": "РБК Крипто", "url": "https://www.rbc.ru/crypto/rss", "lang": "ru"},
]


# ============================================================
# СЛОВАРИ СЕНТИМЕНТА
# ============================================================
BULLISH_WORDS = [
    # EN
    "rally", "surge", "bullish", "gain", "rise", "soar", "jump", "high",
    "adoption", "approve", "partnership", "launch", "record", "breakout",
    "institutional", "etf", "bull", "moon", "growth", "positive",
    # RU
    "рост", "бычий", "прибыль", "подъём", "рекорд", "прорыв",
    "принятие", "одобрен", "партнёрство", "запуск", "позитив",
]

BEARISH_WORDS = [
    # EN
    "crash", "dump", "bearish", "drop", "fall", "plunge", "low", "fear",
    "ban", "hack", "scam", "fraud", "lawsuit", "sec", "regulation",
    "liquidation", "bankruptcy", "bear", "panic", "negative", "warning",
    # RU
    "падение", "медвежий", "обвал", "провал", "запрет", "взлом",
    "мошенничество", "иск", "регуляц", "ликвидац", "банкротств",
    "паник", "негатив", "предупрежд",
]


# ============================================================
# ПУТИ
# ============================================================
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(REPO_ROOT, "data")

SENTIMENT_FILE = os.path.join(DATA_DIR, "news_sentiment.json")
NEWS_HISTORY_FILE = os.path.join(DATA_DIR, "news_history.json")


def load(path, default=None):
    if not os.path.exists(path):
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
# ПАРСИНГ RSS
# ============================================================
def fetch_feed(feed):
    """Забирает последние новости из RSS."""
    items = []
    try:
        r = requests.get(feed["url"], timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ARGUS-bot/1.0)"
        })
        r.raise_for_status()
        text = r.text

        # Достаём <item> или <entry>
        blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not blocks:
            blocks = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)

        for b in blocks[:20]:
            # Заголовок
            title_match = re.search(r"<title[^>]*>(.*?)</title>", b, re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            # Ссылка
            link_match = re.search(r"<link[^>]*>(.*?)</link>", b, re.DOTALL)
            link = link_match.group(1).strip() if link_match else ""

            # Убираем CDATA и HTML
            title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title)
            title = re.sub(r"<[^>]+>", "", title)
            title = " ".join(title.split())

            if title:
                items.append({
                    "title": title,
                    "link": link,
                    "source": feed["name"],
                    "lang": feed["lang"]
                })

    except Exception as e:
        print(f"⚠️ {feed['name']}: {e}")

    return items


# ============================================================
# СЕНТИМЕНТ ПО СЛОВАМ
# ============================================================
def analyze_sentiment(title):
    """Возвращает score от -1 до +1."""
    text = title.lower()

    bull = 0
    bear = 0

    for word in BULLISH_WORDS:
        if word in text:
            bull += 1

    for word in BEARISH_WORDS:
        if word in text:
            bear += 1

    total = bull + bear
    if total == 0:
        return 0.0, bull, bear

    score = (bull - bear) / total
    return round(score, 3), bull, bear


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    print("📰 ARGUS NEWS ANALYZER")
    print("=" * 50)

    all_news = []

    # Собираем все новости
    for feed in FEEDS:
        items = fetch_feed(feed)
        print(f"📡 {feed['name']}: {len(items)} новостей")

        for item in items:
            score, bull, bear = analyze_sentiment(item["title"])
            item["sentiment"] = score
            item["bull_words"] = bull
            item["bear_words"] = bear
            item["collected_at"] = datetime.utcnow().isoformat()
            all_news.append(item)

    if not all_news:
        print("❌ Новостей не собрано.")
        return

    # Средний сентимент
    scores = [n["sentiment"] for n in all_news]
    avg_sentiment = sum(scores) / len(scores)

    # Подсчёт
    bullish_count = sum(1 for s in scores if s > 0.2)
    bearish_count = sum(1 for s in scores if s < -0.2)
    neutral_count = len(scores) - bullish_count - bearish_count

    # Общее состояние
    if avg_sentiment > 0.2:
        mood = "🟢 БЫЧЬЕ"
    elif avg_sentiment < -0.2:
        mood = "🔴 МЕДВЕЖЬЕ"
    else:
        mood = "🟡 НЕЙТРАЛЬНОЕ"

    result = {
        "generated_at": datetime.utcnow().isoformat(),
        "total_news": len(all_news),
        "avg_sentiment": round(avg_sentiment, 3),
        "mood": mood,
        "bullish_count": bullish_count,
        "bearish_count": bearish_count,
        "neutral_count": neutral_count,
        "top_bullish": sorted(
            [n for n in all_news if n["sentiment"] > 0.2],
            key=lambda x: x["sentiment"], reverse=True
        )[:5],
        "top_bearish": sorted(
            [n for n in all_news if n["sentiment"] < -0.2],
            key=lambda x: x["sentiment"]
        )[:5]
    }

    save(SENTIMENT_FILE, result)

    # История (по дням)
    history = load(NEWS_HISTORY_FILE, {"days": []})
    today = datetime.utcnow().strftime("%Y-%m-%d")

    # Заменяем запись за сегодня
    history["days"] = [d for d in history["days"] if d["date"] != today]
    history["days"].append({
        "date": today,
        "sentiment": round(avg_sentiment, 3),
        "total": len(all_news)
    })
    if len(history["days"]) > 90:
        history["days"] = history["days"][-90:]
    save(NEWS_HISTORY_FILE, history)

    # ---------- Вывод ----------
    print("=" * 50)
    print(f"📊 Всего новостей: {len(all_news)}")
    print(f"🎭 Настроение рынка: {mood}")
    print(f"📈 Средний сентимент: {avg_sentiment:.3f}")
    print(f"   Бычьих: {bullish_count}")
    print(f"   Медвежьих: {bearish_count}")
    print(f"   Нейтральных: {neutral_count}")

    if result["top_bullish"]:
        print("\n🟢 Топ бычьих:")
        for n in result["top_bullish"][:3]:
            print(f"   + {n['title'][:80]}")

    if result["top_bearish"]:
        print("\n🔴 Топ медвежьих:")
        for n in result["top_bearish"][:3]:
            print(f"   - {n['title'][:80]}")

    print("=" * 50)


if __name__ == "__main__":
    main()