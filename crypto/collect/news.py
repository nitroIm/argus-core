# ============================================================
# ARGUS-Trader — NEWS ANALYZER
# ------------------------------------------------------------
# Парсит RSS, анализирует сентимент, переводит EN→RU.
# Работает оперативно — для оценки настроения рынка "на лету".
# Данные НЕ пишутся в БД, только JSON в crypto/data/.
# ------------------------------------------------------------
# v4: retries, взвешенный сентимент, дедупликация, pathlib
# v5: перенос в crypto/, пути через CRYPTO_ROOT, перевод
#     переиспользуется из scripts/translate.py через sys.path
# ============================================================

import os
import re
import sys
import json
import hashlib
import requests
from datetime import datetime, timezone
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- Пути ---
SCRIPT_DIR = Path(__file__).resolve().parent        # crypto/collect/
CRYPTO_ROOT = SCRIPT_DIR.parent                     # crypto/
REPO_ROOT = CRYPTO_ROOT.parent                      # argus-core/
DATA_DIR = CRYPTO_ROOT / "data"                     # crypto/data/
SCRIPTS_DIR = REPO_ROOT / "scripts"                 # scripts/

DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- Импорт переводчика из scripts/ ---
sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from translate import translate_to_ru, is_english
    TRANSLATE_AVAILABLE = True
except Exception as e:
    TRANSLATE_AVAILABLE = False
    def translate_to_ru(t): return t
    def is_english(t): return False
    print(f"⚠️ translate.py недоступен: {e}")

# ============================================================
# ИСТОЧНИКИ RSS
# ============================================================
FEEDS = [
    {"name": "CoinDesk",      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/", "lang": "en"},
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss",                    "lang": "en"},
    {"name": "The Block",     "url": "https://www.theblock.co/rss.xml",                  "lang": "en"},
    {"name": "Decrypt",       "url": "https://decrypt.co/feed",                          "lang": "en"},
    {"name": "CoinMarketCap", "url": "https://blog.coinmarketcap.com/feed/",             "lang": "en"},
    {"name": "ForkLog",       "url": "https://forklog.com/feed",                         "lang": "ru"},
    {"name": "РБК Крипто",    "url": "https://www.rbc.ru/crypto/rss",                    "lang": "ru"},
]

# ============================================================
# ВЗВЕШЕННЫЙ СЕНТИМЕНТ
# ============================================================
BULLISH_WEIGHTS = {
    "moon": 2.0, "surge": 1.5, "rally": 1.5, "breakout": 1.5, "soar": 1.5,
    "gain": 1.0, "rise": 1.0, "jump": 1.0, "high": 1.0, "growth": 1.0,
    "adoption": 1.2, "approve": 1.5, "partnership": 1.2, "launch": 1.0,
    "record": 1.2, "institutional": 1.2, "etf": 1.2, "bull": 1.2, "positive": 1.0,
    "рост": 1.0, "бычий": 1.2, "прибыль": 1.0, "подъём": 1.2, "рекорд": 1.2,
    "прорыв": 1.5, "принятие": 1.2, "одобрен": 1.5, "партнёрство": 1.2,
    "запуск": 1.0, "позитив": 1.0,
}

BEARISH_WEIGHTS = {
    "crash": 2.0, "dump": 1.5, "plunge": 1.5, "collapse": 1.5, "fear": 1.2,
    "drop": 1.0, "fall": 1.0, "low": 1.0, "panic": 1.5, "negative": 1.0,
    "warning": 1.2, "ban": 1.5, "hack": 1.5, "scam": 1.5, "fraud": 1.5,
    "lawsuit": 1.2, "sec": 1.2, "regulation": 1.2, "liquidation": 1.2,
    "bankruptcy": 1.5, "bear": 1.2,
    "падение": 1.0, "медвежий": 1.2, "обвал": 1.5, "провал": 1.2, "запрет": 1.5,
    "взлом": 1.5, "мошенничество": 1.5, "иск": 1.2, "регуляц": 1.2,
    "ликвидац": 1.2, "банкротств": 1.5, "паник": 1.5, "негатив": 1.0,
    "предупрежд": 1.2,
}

# ============================================================
# ФАЙЛЫ (в crypto/data/)
# ============================================================
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
NEWS_HISTORY_FILE = DATA_DIR / "news_history.json"
TRANSLATE_CACHE_FILE = DATA_DIR / "translate_cache.json"

# ============================================================
# TELEGRAM
# ============================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception:
        pass


# ============================================================
# HTTP КЛИЕНТ С РЕТРАЯМИ
# ============================================================
def get_robust_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; ARGUS-NewsBot/1.0)"
    })
    return session


SESSION = get_robust_session()


# ============================================================
# УТИЛИТЫ
# ============================================================
def load(path, default=None):
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default if default is not None else {}


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()[:16]


_translate_cache = load(TRANSLATE_CACHE_FILE, {})


def translate_cached(text: str) -> str:
    if not text or not text.strip():
        return text
    # Уже русский?
    if re.search(r"[а-яА-ЯёЁ]", text):
        return text

    key = _hash(text)
    if key in _translate_cache:
        return _translate_cache[key]

    try:
        translated = translate_to_ru(text)
        if translated and translated.strip():
            _translate_cache[key] = translated
            return translated
    except Exception:
        pass
    return text


def save_translate_cache():
    try:
        cache = _translate_cache
        if len(cache) > 5000:
            keys = list(cache.keys())[-3000:]
            cache = {k: cache[k] for k in keys}
        save(TRANSLATE_CACHE_FILE, cache)
    except Exception:
        pass


# ============================================================
# ПАРСИНГ RSS
# ============================================================
def fetch_feed(feed):
    items = []
    try:
        r = SESSION.get(feed["url"], timeout=20)
        r.raise_for_status()
        text = r.text

        blocks = re.findall(r"<item\b[^>]*>(.*?)</item>", text,
                            re.IGNORECASE | re.DOTALL)
        if not blocks:
            blocks = re.findall(r"<entry\b[^>]*>(.*?)</entry>", text,
                                re.IGNORECASE | re.DOTALL)

        for b in blocks[:25]:
            title_match = re.search(r"<title\b[^>]*>(.*?)</title>", b,
                                    re.IGNORECASE | re.DOTALL)
            if not title_match:
                continue

            title = title_match.group(1).strip()
            title = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", title, flags=re.IGNORECASE)
            title = re.sub(r"<[^>]+>", "", title)
            title = " ".join(title.split())

            if len(title) > 15:
                items.append({
                    "title": title,
                    "source": feed["name"],
                    "lang": feed["lang"],
                })
    except Exception as e:
        print(f"⚠️ {feed['name']} недоступен: {e}")
    return items


# ============================================================
# СЕНТИМЕНТ
# ============================================================
def analyze_sentiment(title):
    text = title.lower()
    bull_score = sum(BULLISH_WEIGHTS.get(w, 0) for w in BULLISH_WEIGHTS if w in text)
    bear_score = sum(BEARISH_WEIGHTS.get(w, 0) for w in BEARISH_WEIGHTS if w in text)

    total_weight = bull_score + bear_score
    if total_weight == 0:
        return 0.0, 0, 0

    score = (bull_score - bear_score) / total_weight
    return round(score, 3), int(bull_score), int(bear_score)


# ============================================================
# ОСНОВНАЯ ЛОГИКА
# ============================================================
def main():
    print("📰 ARGUS-Trader NEWS ANALYZER")
    print(f"🌐 Переводчик: {'✅' if TRANSLATE_AVAILABLE else '❌'}")
    print(f"📂 Data: {DATA_DIR}")
    print("=" * 50)

    all_news = []
    seen_hashes = set()

    for feed in FEEDS:
        items = fetch_feed(feed)
        print(f"📡 {feed['name']}: {len(items)} заголовков")

        for item in items:
            h = _hash(item["title"])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            score, bull, bear = analyze_sentiment(item["title"])
            item["sentiment"] = score
            item["bull_weight"] = bull
            item["bear_weight"] = bear
            item["collected_at"] = datetime.now(timezone.utc).isoformat()

            original = item["title"]
            item["title_original"] = original

            if feed["lang"] == "en" or is_english(original):
                item["title"] = translate_cached(original)

            all_news.append(item)

    if not all_news:
        print("❌ Новостей не собрано")
        notify("❌ <b>ARGUS News:</b> не удалось собрать новости.")
        return

    scores = [n["sentiment"] for n in all_news]
    avg_sentiment = sum(scores) / len(scores)

    bullish_count = sum(1 for s in scores if s > 0.2)
    bearish_count = sum(1 for s in scores if s < -0.2)
    neutral_count = len(scores) - bullish_count - bearish_count

    if avg_sentiment > 0.25:
        mood = "🟢 БЫЧЬЕ"
    elif avg_sentiment < -0.25:
        mood = "🔴 МЕДВЕЖЬЕ"
    else:
        mood = "🟡 НЕЙТРАЛЬНОЕ"

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
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
        )[:5],
    }

    save(SENTIMENT_FILE, result)

    # История
    history = load(NEWS_HISTORY_FILE, {"days": []})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history["days"] = [d for d in history.get("days", []) if d.get("date") != today]
    history["days"].append({
        "date": today,
        "sentiment": round(avg_sentiment, 3),
        "total": len(all_news),
    })
    if len(history["days"]) > 90:
        history["days"] = history["days"][-90:]
    save(NEWS_HISTORY_FILE, history)

    save_translate_cache()

    # Лог
    print("=" * 50)
    print(f"📊 Уникальных новостей: {len(all_news)}")
    print(f"🎭 Настроение: {mood}")
    print(f"📈 Средний сентимент: {avg_sentiment:.3f}")
    print(f"   🟢 Бычьих: {bullish_count} | 🔴 Медвежьих: {bearish_count} | 🟡 Нейтральных: {neutral_count}")

    if result["top_bullish"]:
        print("\n🟢 Топ бычьих:")
        for n in result["top_bullish"][:3]:
            print(f"   + {n['title'][:80]}")

    if result["top_bearish"]:
        print("\n🔴 Топ медвежьих:")
        for n in result["top_bearish"][:3]:
            print(f"   - {n['title'][:80]}")
    print("=" * 50)

    notify(
        f"📰 <b>ARGUS News Update</b>\n"
        f"Собрано: {len(all_news)}\n"
        f"Настроение: {mood}\n"
        f"Сентимент: <code>{avg_sentiment:+.3f}</code>"
    )


if __name__ == "__main__":
    main()