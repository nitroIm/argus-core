# ============================================================
# ARGUS-Trader — NEWS ANALYZER v6.3
# ------------------------------------------------------------
# v6.3: translate.py теперь в crypto/report/
# v6.2: html.unescape, clean_text, пост-обработка перевода
# ============================================================

import os
import re
import sys
import json
import html
import hashlib
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

SCRIPT_DIR = Path(__file__).resolve().parent
CRYPTO_ROOT = SCRIPT_DIR.parent
DATA_DIR = CRYPTO_ROOT / "data"
REPORT_DIR = CRYPTO_ROOT / "report"

DATA_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPORT_DIR))
try:
    from translate import translate_to_ru
    from translate import is_english
    TRANSLATE_AVAILABLE = True
except Exception as e:
    TRANSLATE_AVAILABLE = False
    def translate_to_ru(t): return t
    def is_english(t): return False
    print("translate unavailable: " + str(e))

# ============================================================
# ИСТОЧНИКИ RSS + ВЕСА
# ============================================================
FEEDS = [
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss",                    "lang": "en", "weight": 1.3},
    {"name": "Decrypt",       "url": "https://decrypt.co/feed",                          "lang": "en", "weight": 1.1},
    {"name": "Bitcoin.com",   "url": "https://news.bitcoin.com/feed/",                   "lang": "en", "weight": 1.0},
    {"name": "CryptoSlate",   "url": "https://cryptoslate.com/feed/",                    "lang": "en", "weight": 1.0},
    {"name": "CryptoPotato",  "url": "https://cryptopotato.com/feed/",                   "lang": "en", "weight": 1.0},
    {"name": "Bitcoinist",    "url": "https://bitcoinist.com/feed/",                     "lang": "en", "weight": 0.9},
    {"name": "ForkLog",       "url": "https://forklog.com/feed",                         "lang": "ru", "weight": 1.2},
]

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

CLICKBAIT_PATTERNS = [
    r"!!!+",
    r"\?!!",
    r"\b(shocking|unbelievable|you won't believe|must see|urgent)\b",
    r"\b(шок|срочно|не поверите|вы не поверите)\b",
    r"(🚀|🔥|💎|🌙|⚡){2,}",
    r"\b(AI|Bot|bot)\s*(says|predicts|reveals)",
    r"\b(top|best|worst)\s+\d+\b",
]

SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
NEWS_HISTORY_FILE = DATA_DIR / "news_history.json"
TRANSLATE_CACHE_FILE = DATA_DIR / "translate_cache.json"

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


def get_robust_session():
    session = requests.Session()
    retry = Retry(
        total=3, backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ARGUS-NewsBot/1.0)"})
    return session


SESSION = get_robust_session()


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


# ============================================================
# CLEAN TEXT
# ============================================================
def clean_text(text: str) -> str:
    """Нормализует заголовок: убирает HTML, entities, мусор."""
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(' "\'«»""„""')
    return text.strip()


_translate_cache = load(TRANSLATE_CACHE_FILE, {})


def translate_cached(text: str) -> str:
    if not text or not text.strip():
        return text
    if re.search(r"[а-яА-ЯёЁ]", text):
        return text
    key = _hash(text)
    if key in _translate_cache:
        return _translate_cache[key]
    try:
        translated = translate_to_ru(text)
        if translated and translated.strip():
            translated = clean_text(translated)
            translated = re.sub(r"\s+", " ", translated)
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


def normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def keywords(text: str, min_len: int = 4) -> set:
    stop = {"with", "that", "from", "this", "will", "have", "what", "your",
            "который", "которая", "этого", "чтобы", "будет", "может"}
    words = normalize_title(text).split()
    return {w for w in words if len(w) >= min_len and w not in stop}


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

            raw_title = title_match.group(1).strip()
            title = clean_text(raw_title)

            if len(title) < 15:
                continue

            pub_date = None
            for tag in ["pubDate", "published", "updated", "dc:date"]:
                m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", b,
                              re.IGNORECASE | re.DOTALL)
                if m:
                    date_str = m.group(1).strip()
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(date_str)
                        if pub_date.tzinfo is None:
                            pub_date = pub_date.replace(tzinfo=timezone.utc)
                        break
                    except Exception:
                        pass

            items.append({
                "title": title,
                "source": feed["name"],
                "source_weight": feed.get("weight", 1.0),
                "lang": feed["lang"],
                "pub_date": pub_date.isoformat() if pub_date else None,
            })
    except Exception as e:
        print(f"⚠️ {feed['name']} недоступен: {e}")
    return items


def analyze_sentiment(title):
    text = title.lower()
    bull = sum(BULLISH_WEIGHTS.get(w, 0) for w in BULLISH_WEIGHTS if w in text)
    bear = sum(BEARISH_WEIGHTS.get(w, 0) for w in BEARISH_WEIGHTS if w in text)
    total = bull + bear
    if total == 0:
        return 0.0, 0, 0
    score = (bull - bear) / total
    return round(score, 3), int(bull), int(bear)


def freshness_weight(pub_date_iso):
    if not pub_date_iso:
        return 1.0
    try:
        pub = datetime.fromisoformat(pub_date_iso)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - pub
        if age < timedelta(hours=1):
            return 1.5
        elif age < timedelta(hours=6):
            return 1.0
        elif age < timedelta(hours=24):
            return 0.8
        else:
            return 0.5
    except Exception:
        return 1.0


def fake_score(title, source_weight):
    score = 0.0
    for pattern in CLICKBAIT_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            score += 0.25
    letters = [c for c in title if c.isalpha()]
    if letters:
        caps = sum(1 for c in letters if c.isupper())
        if caps / len(letters) > 0.3:
            score += 0.2
    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF]", title))
    if emoji_count > 3:
        score += 0.15
    if source_weight < 1.0:
        score += 0.1
    return round(min(1.0, score), 2)


def main():
    print("📰 ARGUS-Trader NEWS ANALYZER v6.3")
    print(f"🌐 Переводчик: {'✅' if TRANSLATE_AVAILABLE else '❌'}")
    print("=" * 60)

    all_news = []
    seen_hashes = set()

    for feed in FEEDS:
        items = fetch_feed(feed)
        print(f"📡 {feed['name']}: {len(items)} заголовков (вес {feed.get('weight', 1.0)})")

        for item in items:
            h = _hash(item["title"])
            if h in seen_hashes:
                continue
            seen_hashes.add(h)

            score, bull, bear = analyze_sentiment(item["title"])
            fresh = freshness_weight(item.get("pub_date"))
            fake = fake_score(item["title"], item["source_weight"])

            item["sentiment"] = score
            item["bull_weight"] = bull
            item["bear_weight"] = bear
            item["freshness"] = fresh
            item["fake_score"] = fake
            item["collected_at"] = datetime.now(timezone.utc).isoformat()
            item["effective_weight"] = round(
                item["source_weight"] * fresh * (1.0 - fake), 3
            )

            original = item["title"]
            item["title_original"] = original

            if feed["lang"] == "en" or is_english(original):
                item["title"] = translate_cached(original)

            all_news.append(item)

    if not all_news:
        print("❌ Новостей не собрано")
        notify("❌ <b>ARGUS News:</b> не удалось собрать новости.")
        return

    print("\n🔍 Перекрёстная проверка...")
    for i, n1 in enumerate(all_news):
        kw1 = keywords(n1["title_original"])
        if len(kw1) < 3:
            n1["cross_check"] = 1
            continue
        count = 1
        for j, n2 in enumerate(all_news):
            if i == j:
                continue
            kw2 = keywords(n2["title_original"])
            if len(kw1 & kw2) >= 3:
                count += 1
        n1["cross_check"] = count

    total_weight = sum(n["effective_weight"] for n in all_news)
    if total_weight > 0:
        avg_sentiment = sum(n["sentiment"] * n["effective_weight"] for n in all_news) / total_weight
    else:
        avg_sentiment = 0.0

    bullish = sum(1 for n in all_news if n["sentiment"] > 0.2)
    bearish = sum(1 for n in all_news if n["sentiment"] < -0.2)
    neutral = len(all_news) - bullish - bearish

    fake_count = sum(1 for n in all_news if n["fake_score"] > 0.4)
    cross_confirmed = sum(1 for n in all_news if n["cross_check"] >= 3)
    single_source = sum(1 for n in all_news if n["cross_check"] == 1)

    if avg_sentiment > 0.25:
        mood = "🟢 БЫЧЬЕ"
    elif avg_sentiment < -0.25:
        mood = "🔴 МЕДВЕЖЬЕ"
    else:
        mood = "🟡 НЕЙТРАЛЬНОЕ"

    clean_news = [n for n in all_news if n["fake_score"] < 0.4]
    top_bull = sorted(
        [n for n in clean_news if n["sentiment"] > 0.2],
        key=lambda x: x["sentiment"] * x["effective_weight"],
        reverse=True
    )[:5]
    top_bear = sorted(
        [n for n in clean_news if n["sentiment"] < -0.2],
        key=lambda x: x["sentiment"] * x["effective_weight"]
    )[:5]

    by_source = {}
    for n in all_news:
        src = n["source"]
        if src not in by_source:
            by_source[src] = {"total": 0, "bull": 0, "bear": 0}
        by_source[src]["total"] += 1
        if n["sentiment"] > 0.2:
            by_source[src]["bull"] += 1
        elif n["sentiment"] < -0.2:
            by_source[src]["bear"] += 1

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_news": len(all_news),
        "avg_sentiment": round(avg_sentiment, 3),
        "mood": mood,
        "bullish_count": bullish,
        "bearish_count": bearish,
        "neutral_count": neutral,
        "fake_count": fake_count,
        "cross_confirmed": cross_confirmed,
        "single_source": single_source,
        "by_source": by_source,
        "top_bullish": top_bull,
        "top_bearish": top_bear,
    }

    save(SENTIMENT_FILE, result)

    history = load(NEWS_HISTORY_FILE, {"days": []})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history["days"] = [d for d in history.get("days", []) if d.get("date") != today]
    history["days"].append({
        "date": today,
        "sentiment": round(avg_sentiment, 3),
        "total": len(all_news),
        "fake": fake_count,
    })
    if len(history["days"]) > 90:
        history["days"] = history["days"][-90:]
    save(NEWS_HISTORY_FILE, history)

    save_translate_cache()

    print("=" * 60)
    print(f"📊 Всего: {len(all_news)}")
    print(f"🎭 Настроение: {mood}")
    print(f"📈 Сентимент: {avg_sentiment:+.3f}")
    print(f"   🟢 {bullish} | 🔴 {bearish} | 🟡 {neutral}")
    print(f"🔍 Перекрёстно подтверждено (3+): {cross_confirmed}")
    print(f"⚠️ Один источник: {single_source}")
    print(f"🚨 Подозрительных (fake): {fake_count}")

    if top_bull:
        print("\n🟢 Топ бычьих:")
        for n in top_bull[:3]:
            print(f"   + {n['title'][:80]}")

    if top_bear:
        print("\n🔴 Топ медвежьих:")
        for n in top_bear[:3]:
            print(f"   - {n['title'][:80]}")
    print("=" * 60)


if __name__ == "__main__":
    main()