# ============================================================
# ARGUS — АНАЛИЗ НОВОСТЕЙ
# v3: локальный перевод Helsinki-NLP + кэш
# ============================================================

import os
import re
import json
import hashlib
import requests
from datetime import datetime, timedelta

# --- Импорт локального переводчика ---
try:
    from translate import translate_to_ru, is_english
    TRANSLATE_AVAILABLE = True
except Exception as e:
    print(f"⚠️ translate.py недоступен: {e}")
    TRANSLATE_AVAILABLE = False
    def translate_to_ru(t): return t
    def is_english(t): return False


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
    "rally", "surge", "bullish", "gain", "rise", "soar", "jump", "high",
    "adoption", "approve", "partnership", "launch", "record", "breakout",
    "institutional", "etf", "bull", "moon", "growth", "positive",
    "рост", "бычий", "прибыль", "подъём", "рекорд", "прорыв",
    "принятие", "одобрен", "партнёрство", "запуск", "позитив",
]

BEARISH_WORDS = [
    "crash", "dump", "bearish", "drop", "fall", "plunge", "low", "fear",
    "ban", "hack", "scam", "fraud", "lawsuit", "sec", "regulation",
    "liquidation", "bankruptcy", "bear", "panic", "negative", "warning",
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
TRANSLATE_CACHE_FILE = os.path.join(DATA_DIR, "translate_cache.json")


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
# КЭШ ПЕРЕВОДОВ
# ============================================================
_translate_cache = load(TRANSLATE_CACHE_FILE, {})


def _hash(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


def translate_cached(text: str) -> str:
    """Перевод с кэшем. Русский пропускает как есть."""
    if not text or not text.strip():
        return text
    if re.search(r"[а-яА-Я]", text):
        return text

    key = _hash(text)
    if key in _translate_cache:
        return _translate_cache[key]

    try:
        translated = translate_to_ru(text)
        if translated and translated.strip():
            _translate_cache[key] = translated
            return translated
    except Exception as e:
        print(f"⚠️ translate: {e}")

    return text


def save_translate_cache():
    try:
        if len(_translate_cache) > 5000:
            keys = list(_translate_cache.keys())[-3000:]
            trimmed = {k: _translate_cache[k] for k in keys}
            save(TRANSLATE_CACHE_FILE, trimmed)
        else:
            save(TRANSLATE_CACHE_FILE, _translate_cache)
    except Exception as e:
        print(f"⚠️ save translate cache: {e}")


# ============================================================
# ПАРСИНГ RSS
# ============================================================
def fetch_feed(feed):
    items = []
    try:
        r = requests.get(feed["url"], timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (compatible; ARGUS-bot/1.0)"
        })
        r.raise_for_status()
        text = r.text

        blocks = re.findall(r"<item>(.*?)</item>", text, re.DOTALL)
        if not blocks:
            blocks = re.findall(r"<entry>(.*?)</entry>", text, re.DOTALL)

        for b in blocks[:20]:
            title_match = re.search(r"<title[^>]*>(.*?)</title>", b, re.DOTALL)
            title = title_match.group(1).strip() if title_match else ""

            link_match = re.search(r"<link[^>]*>(.*?)</link>", b, re.DOTALL)
            link = link_match.group(1).strip() if link_match else ""

            title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title)
            title = re.sub(r"<[^>]+>", "", title)
            title = " ".join(title.split())

            if title:
                items.append({
                    "title": title,
                    "link": link,
                    "source": feed["name"],
                    "lang": feed["lang"],
                })
    except Exception as e:
        print(f"⚠️ {feed['name']}: {e}")

    return items


# ============================================================
# СЕНТИМЕНТ
# ============================================================
def analyze_sentiment(title):
    text = title.lower()
    bull = sum(1 for w in BULLISH_WORDS if w in text)
    bear = sum(1 for w in BEARISH_WORDS if w in text)
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
    print(f"🌐 Переводчик: {'локальный (Helsinki-NLP)' if TRANSLATE_AVAILABLE else 'НЕДОСТУПЕН'}")
    print("=" * 50)

    all_news = []

    for feed in FEEDS:
        items = fetch_feed(feed)
        print(f"📡 {feed['name']}: {len(items)} новостей")

        for item in items:
            score, bull, bear = analyze_sentiment(item["title"])
            item["sentiment"] = score
            item["bull_words"] = bull
            item["bear_words"] = bear
            item["collected_at"] = datetime.utcnow().isoformat()

            original = item["title"]
            item["title_original"] = original

            # Переводим только английские
            if feed["lang"] == "en" or is_english(original):
                item["title"] = translate_cached(original)

            all_news.append(item)

    if not all_news:
        print("❌ Новостей не собрано.")
        return

    scores = [n["sentiment"] for n in all_news]
    avg_sentiment = sum(scores) / len(scores)

    bullish_count = sum(1 for s in scores if s > 0.2)
    bearish_count = sum(1 for s in scores if s < -0.2)
    neutral_count = len(scores) - bullish_count - bearish_count

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
            key=lambda x: x["sentiment"], reverse=True,
        )[:5],
        "top_bearish": sorted(
            [n for n in all_news if n["sentiment"] < -0.2],
            key=lambda x: x["sentiment"],
        )[:5],
    }

    save(SENTIMENT_FILE, result)

    history = load(NEWS_HISTORY_FILE, {"days": []})
    today = datetime.utcnow().strftime("%Y-%m-%d")
    history["days"] = [d for d in history["days"] if d["date"] != today]
    history["days"].append({
        "date": today,
        "sentiment": round(avg_sentiment, 3),
        "total": len(all_news),
    })
    if len(history["days"]) > 90:
        history["days"] = history["days"][-90:]
    save(NEWS_HISTORY_FILE, history)

    save_translate_cache()

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