# ============================================================
# ARGUS-Trader — NEWS ANALYZER v13.1 (PATH UPDATE)
# ------------------------------------------------------------
# v13.1: Путь сохранения изменен на crypto/data/news/
# v13.0:
#  - Anti-fraud = позитив.
#  - JPMorgan / JPM Coin / внедряет = позитив (принятие).
#  - Исправлено размытие сентимента.
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
# ИЗМЕНЕНИЕ: Теперь данные пишутся в папку news
DATA_DIR = CRYPTO_ROOT / "data" / "news"

DATA_DIR.mkdir(parents=True, exist_ok=True)

FEEDS = [
    {"name": "Cointelegraph", "url": "https://cointelegraph.com/rss",                    "lang": "en", "weight": 1.3},
    {"name": "Decrypt",       "url": "https://decrypt.co/feed",                          "lang": "en", "weight": 1.1},
    {"name": "Bitcoin.com",   "url": "https://news.bitcoin.com/feed/",                   "lang": "en", "weight": 1.0},
    {"name": "CryptoSlate",   "url": "https://cryptoslate.com/feed/",                    "lang": "en", "weight": 1.0},
    {"name": "CryptoPotato",  "url": "https://cryptopotato.com/feed/",                   "lang": "en", "weight": 1.0},
    {"name": "Bitcoinist",    "url": "https://bitcoinist.com/feed/",                     "lang": "en", "weight": 0.9},
    {"name": "ForkLog",       "url": "https://forklog.com/feed",                         "lang": "ru", "weight": 1.2},
]

CRYPTO_PATTERNS = [
    r"\b(bitcoin|btc|ethereum|eth|crypto|cryptocurrency|blockchain|defi|nft|web3|altcoin|stablecoin|token|exchange|wallet|mining|halving|airdrop|metamask|binance|coinbase|kraken|okx|bybit|kucoin|huobi|ftx)\b",
    r"\b(rwa|real world asset|tokenization|tokenisation|smart contract|cbdc|layer 2|l2|rollup|zero-knowledge|zk-proof|dao|digital currency|digital asset|distributed ledger|dlts|custody|etf|spot etf|staking|restaking|lst|lrt|yield|yield farming|gamefi|metaverse|ordinals|inscriptions|brc-20|erc-20|erc-721|dex|cex|amm|liquidity|whale|whales)\b",
    r"\b(solana|sol|cardano|ada|xrp|ripple|dogecoin|doge|tether|usdt|usdc|polkadot|dot|litecoin|ltc|shiba|shib|avalanche|avax|chainlink|link|polygon|matic|tron|trx|toncoin|ton|uniswap|uni|cosmos|atom|aptos|apt|arbitrum|arb|optimism|op|near|injective|sui|aave|maker|mkr|curve|crv|sandbox|sand|decentraland|mana|terra|luna|ustc|pepe|bonk|wif|floki|stellar|xlm|monero|xmr|ethereum classic|etc|bitcoin cash|bch|filecoin|fil|hedera|hbar|internet computer|icp|vechain|vet|fantom|ftm|theta|axie|axs|eos|flow|gala|the graph|grt|elrond|egld|klaytn|klay|chiliz|chz|quant|qnt|cronos|cro|okb|leo|casper|csp)\b",
    r"\b(биткоин|биткойн|эфириум|эфир|криптовалюта|крипта|токен|альткоин|блокчейн|биржа|майнинг|кошелек|солана|кардано|догикоин|рипл|бинанс|тейзер|полкадот|лайткоин|шиба|аваланч|чейнлинк|полигон|трон|тонкоин|монета|актив|децентрализация|цифровой актив|смарт-контракт|токенизация|цифровая валюта|стейкинг|майнер|киты)\b",
    r"\b(microstrategy|blackrock|grayscale|fidelity|vanguard|jpmorgan|goldman sachs|morgan stanley|sec|cftc|gensler|etf|spot etf|bitcoin etf|saylor|sbf|cz|zhao)\b"
]

SPAM_PATTERNS = [
    r"\b(lottery|casino|betting|sportsbook|football|basketball|soccer|nba|nfl|iphone|android|movie|actor|actress|celebrity|weather|horoscope|recipe|cooking|diet)\b",
    r"\b(лотерея|казино|ставки|спорт|футбол|баскетбол|айфон|андроид|кино|сериал|актер|знаменитость|погода|гороскоп|рецепт)\b"
]

BULLISH_WEIGHTS = {
    "moon": 2.0, "surge": 1.5, "rally": 1.5, "breakout": 1.5, "soar": 1.5,
    "gain": 1.0, "rise": 1.0, "jump": 1.0, "high": 1.0, "growth": 1.0,
    "adoption": 1.2, "approve": 1.5, "partnership": 1.2, "launch": 1.0,
    "record": 1.2, "institutional": 1.2, "etf": 1.2, "bull": 1.2, "positive": 1.0,
    "рост": 1.0, "бычий": 1.2, "прибыль": 1.0, "подъём": 1.2, "рекорд": 1.2,
    "прорыв": 1.5, "принятие": 1.2, "одобрен": 1.5, "партнёрство": 1.2,
    "запуск": 1.0, "позитив": 1.0, "upgrade": 1.2, "update": 0.8, "win": 1.2, "won": 1.2,
    "inflow": 1.8, "inflows": 1.8, "pours": 1.5, "invests": 1.2, "investment": 1.0,
    "accumulate": 1.2, "accumulation": 1.2, "buy the dip": 1.5, "bought": 1.0,
    "capital flight to": 1.5, "whale buys": 1.5,
    "приток": 1.8, "вливает": 1.5, "инвестирует": 1.2, "накапливает": 1.2,
    "выкупают": 1.2, "закупаются": 1.2, "киты покупают": 1.5,
    "pro-crypto": 1.5, "bipartisan": 1.2, "bill passes": 1.8, "favorable": 1.2,
    "поддержка": 1.2, "двухпартийный": 1.2, "закон принят": 1.8,
    "внедряет": 1.2, "приходит": 1.2, "инструменты": 0.8, "привлекли": 1.5,
}

BEARISH_WEIGHTS = {
    "crash": 2.0, "dump": 1.5, "plunge": 1.5, "collapse": 1.5, "fear": 1.2,
    "drop": 1.0, "fall": 1.0, "low": 1.0, "panic": 1.5, "negative": 1.0,
    "warning": 1.2, "ban": 1.5, "hack": 1.5, "scam": 1.5, "fraud": 1.5,
    "lawsuit": 1.2, "liquidation": 1.2, "bankruptcy": 1.5, "bear": 1.2,
    "падение": 1.0, "медвежий": 1.2, "обвал": 1.5, "провал": 1.2, "запрет": 1.5,
    "взлом": 1.5, "мошенничество": 1.5, "иск": 1.2, "регуляц": 1.2,
    "ликвидац": 1.2, "банкротств": 1.5, "паник": 1.5, "негатив": 1.0,
    "предупрежд": 1.2, "exploit": 1.5, "delay": 1.2, "reject": 1.5,
    "postpone": 1.5, "postponed": 1.5, "cancel": 1.5, "canceled": 1.5,
    "lost": 1.5, "lose": 1.5, "loss": 1.5, "losses": 1.5, "bleed": 1.5, "bleeding": 1.5,
    "thread": 1.0, "halted": 1.5, "halt": 1.5, "shrink": 1.2, "shrank": 1.2,
    "convicted": 1.8, "guilty": 1.8, "sentenced": 1.5, "red": 0.8,
    "потеряли": 1.5, "убыток": 1.5, "сократились": 1.2, "остановила": 1.5,
    "висит на тонкой нити": 2.0, "осужден": 1.8, "виновен": 1.8, "красный": 0.8,
    "outflow": 1.8, "outflows": 1.8, "withdraw": 1.5, "withdrawal": 1.5,
    "pull out": 1.5, "sell-off": 1.5, "selloff": 1.5, "flee": 1.5, 
    "capital flight": 1.8, "whale sells": 1.5, "selling pressure": 1.2,
    "отток": 1.8, "выводят": 1.5, "забирают": 1.2, "распродажа": 1.5,
    "бегство капитала": 1.8, "киты продают": 1.5, "давление продавцов": 1.2, "сливают": 1.2,
    "anti-crypto": 1.8, "crackdown": 1.8, "enforcement": 1.5, "hostile": 1.5,
    "против": 1.2, "жесткие меры": 1.8, "принудительные": 1.5, "враждебный": 1.5
}

CONTEXT_RULES = [
    (r"anti[- ]fraud|combat\s+fraud|борьба\s+с\s+мошенничеств|против\s+мошенничества", 2.0),
    (r"(jpm|jpmorgan)\s+(coin|ethereum|eth|blockchain)", 1.5),
    (r"(внедряет|приходит|запускает)\s+(инструменты|на|в)", 1.0),
    (r"(etf|fund|funds)\s+(lost|loss|outflow|shed|bleed)", -2.5),
    (r"(hangs?|hanging)\s+by\s+a\s+thread", -2.0),
    (r"halt(ed|s)?\s+(blockchain|network|chain)", -2.0),
    (r"convicted|guilty\s+of", -2.0),
    (r"shrink|shrank|shrunken", -1.5),
    (r"sec\s+(opens|approves|greenlights|allows|clears|permits)", 2.0),
    (r"sec\s+chair\s+(pushes|backs|supports)", 1.2),
    (r"sec\s+(sues|charges|blocks|rejects|bans|delays)", -2.0),
    (r"cftc\s+(sues|charges|blocks|rejects|bans)", -1.5),
    (r"(senate|congress|house)\s+(passes|approves|backs|supports)\s+(crypto|bitcoin|blockchain)", 2.0),
    (r"(senate|congress|house)\s+(rejects|blocks|opposes|cracks down on)\s+(crypto|bitcoin|blockchain)", -2.0),
    (r"etf\s+(inflows?|approval|approves|greenlight)", 1.5),
    (r"etf\s+outflows?", -1.5),
    (r"(adopts?|adoption\s+of)\s+bitcoin", 1.5),
    (r"country\s+adopts?\s+crypto", 1.5),
    (r"crypto\s+(ban|bans|banning)", -2.0),
    (r"all[- ]time\s+high", 1.8),
    (r"all[- ]time\s+low", -1.8),
    (r"new\s+high", 1.3),
    (r"new\s+low", -1.3),
    (r"(institutional|big|whale)\s+(money|capital|investors)\s+(pours|flows|enters|buy)", 1.8),
    (r"(investors|capital|funds)\s+(flee|exit|withdraw|pull out|leave)", -1.8),
    (r"liquidation[s]?\s+(cascade|wave|surge)", -1.5),
    (r"(hacked|hack|exploit)\s+(for|of|at)", -1.8),
    (r"stolen\s+(funds|crypto|bitcoin)", -1.8),
    (r"(risk|risks)\s+of\s+(correction|pullback|crash)", -1.2),
]

CLICKBAIT_PATTERNS = [
    r"!!!+", r"\?!!", r"\b(shocking|unbelievable|you won't believe|must see|urgent)\b",
    r"\b(шок|срочно|не поверите|вы не поверите)\b", r"(🚀|🔥|💎|🌙|⚡){2,}",
    r"\b(AI|Bot|bot)\s*(says|predicts|reveals)", r"\b(top|best|worst)\s+\d+\b",
]

# ИЗМЕНЕНИЕ: Пути теперь указывают на папку news
SENTIMENT_FILE = DATA_DIR / "news_sentiment.json"
NEWS_HISTORY_FILE = DATA_DIR / "news_history.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def notify(text: str):
    if not BOT_TOKEN or not CHAT_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception: pass

def get_robust_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ARGUS-NewsBot/1.0)"})
    return session

SESSION = get_robust_session()

def load(path, default=None):
    if not path.exists(): return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return default if default is not None else {}

def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def _hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()[:16]

def clean_text(text: str) -> str:
    if not text: return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(' "\'«»""„""')
    return text.strip()

def normalize_title(title: str) -> str:
    text = title.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def keywords(text: str, min_len: int = 4) -> set:
    stop = {"with", "that", "from", "this", "will", "have", "what", "your", "который", "которая", "этого", "чтобы", "будет", "может"}
    words = normalize_title(text).split()
    return {w for w in words if len(w) >= min_len and w not in stop}

def is_crypto_related(title: str) -> bool:
    t = title.lower()
    for pattern in CRYPTO_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE): return True
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, t, re.IGNORECASE): return False
    return False

def fetch_feed(feed):
    items = []
    try:
        r = SESSION.get(feed["url"], timeout=20)
        r.raise_for_status()
        text = r.text
        blocks = re.findall(r"<item\b[^>]*>(.*?)</item>", text, re.IGNORECASE | re.DOTALL)
        if not blocks: blocks = re.findall(r"<entry\b[^>]*>(.*?)</entry>", text, re.IGNORECASE | re.DOTALL)
        for b in blocks[:30]:
            title_match = re.search(r"<title\b[^>]*>(.*?)</title>", b, re.IGNORECASE | re.DOTALL)
            if not title_match: continue
            raw_title = title_match.group(1).strip()
            title = clean_text(raw_title)
            if len(title) < 15: continue
            if not is_crypto_related(title): continue
            url_match = re.search(r"<link\b[^>]*>(.*?)</link>", b, re.IGNORECASE | re.DOTALL)
            if not url_match: url_match = re.search(r'<link\b[^>]*href="([^"]+)"', b, re.IGNORECASE)
            url = ""
            if url_match:
                url = url_match.group(1).strip()
                url = url.replace("<![CDATA[", "").replace("]]>", "")
            pub_date = None
            for tag in ["pubDate", "published", "updated", "dc:date"]:
                m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}>", b, re.IGNORECASE | re.DOTALL)
                if m:
                    date_str = m.group(1).strip()
                    try:
                        from email.utils import parsedate_to_datetime
                        pub_date = parsedate_to_datetime(date_str)
                        if pub_date.tzinfo is None: pub_date = pub_date.replace(tzinfo=timezone.utc)
                        break
                    except Exception: pass
            items.append({
                "title": title, "url": url, "source": feed["name"],
                "source_weight": feed.get("weight", 1.0), "lang": feed["lang"],
                "pub_date": pub_date.isoformat() if pub_date else None,
            })
    except Exception as e: print(f"⚠️ {feed['name']} недоступен: {e}")
    return items

def analyze_sentiment(title):
    text = title.lower()
    bull = 0.0
    bear = 0.0
    for pattern, weight in CONTEXT_RULES:
        if re.search(pattern, text):
            if weight > 0: bull += weight
            else: bear += abs(weight)
    for w, weight in BULLISH_WEIGHTS.items():
        if w in text: bull += weight
    for w, weight in BEARISH_WEIGHTS.items():
        if w in text: bear += weight
    
    total = bull + bear
    if total == 0: return 0.0, 0, 0
    score = (bull - bear) / total
    score = max(-1.0, min(1.0, score))
    return round(score, 3), round(bull, 2), round(bear, 2)

def freshness_weight(pub_date_iso):
    if not pub_date_iso: return 1.0
    try:
        pub = datetime.fromisoformat(pub_date_iso)
        if pub.tzinfo is None: pub = pub.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - pub
        if age < timedelta(hours=1): return 1.5
        elif age < timedelta(hours=6): return 1.0
        elif age < timedelta(hours=24): return 0.8
        else: return 0.5
    except Exception: return 1.0

def fake_score(title, source_weight):
    score = 0.0
    for pattern in CLICKBAIT_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE): score += 0.25
    letters = [c for c in title if c.isalpha()]
    if letters:
        caps = sum(1 for c in letters if c.isupper())
        if caps / len(letters) > 0.3: score += 0.2
    emoji_count = len(re.findall(r"[\U0001F300-\U0001FAFF]", title))
    if emoji_count > 3: score += 0.15
    if source_weight < 1.0: score += 0.1
    return round(min(1.0, score), 2)

def main():
    print("📰 ARGUS-Trader NEWS ANALYZER v13.1")
    print("=" * 60)
    all_news = []
    seen_hashes = set()

    for feed in FEEDS:
        items = fetch_feed(feed)
        print(f"📡 {feed['name']}: {len(items)} подходящих заголовков")
        for item in items:
            h = _hash(item["title"])
            if h in seen_hashes: continue
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
            item["effective_weight"] = round(item["source_weight"] * fresh * (1.0 - fake), 3)
            item["title_original"] = item["title"]
            all_news.append(item)

    if not all_news:
        print("❌ Новостей не собрано")
        return

    print("\n🔍 Перекрёстная проверка...")
    for i, n1 in enumerate(all_news):
        kw1 = keywords(n1["title_original"])
        if len(kw1) < 3:
            n1["cross_check"] = 1
            continue
        count = 1
        for j, n2 in enumerate(all_news):
            if i == j: continue
            kw2 = keywords(n2["title_original"])
            if len(kw1 & kw2) >= 3: count += 1
        n1["cross_check"] = count

    bull_news = [n for n in all_news if n["sentiment"] > 0.0]
    bear_news = [n for n in all_news if n["sentiment"] < 0.0]
    
    total_bull_weight = sum(n["effective_weight"] for n in bull_news)
    total_bear_weight = sum(n["effective_weight"] for n in bear_news)
    
    active_weight = total_bull_weight + total_bear_weight
    if active_weight > 0:
        avg_sentiment = (total_bull_weight - total_bear_weight) / active_weight
    else: 
        avg_sentiment = 0.0

    bullish = sum(1 for n in all_news if n["sentiment"] > 0.2)
    bearish = sum(1 for n in all_news if n["sentiment"] < -0.2)
    neutral = len(all_news) - bullish - bearish
    fake_count = sum(1 for n in all_news if n["fake_score"] > 0.4)
    cross_confirmed = sum(1 for n in all_news if n["cross_check"] >= 3)
    
    if avg_sentiment > 0.15: mood = "🟢 БЫЧЬЕ"
    elif avg_sentiment < -0.15: mood = "🔴 МЕДВЕЖЬЕ"
    else: mood = "🟡 НЕЙТРАЛЬНОЕ"

    clean_news = [n for n in all_news if n["fake_score"] < 0.4]
    top_bull = sorted([n for n in clean_news if n["sentiment"] > 0.2], key=lambda x: x["sentiment"] * x["effective_weight"], reverse=True)[:5]
    top_bear = sorted([n for n in clean_news if n["sentiment"] < -0.2], key=lambda x: x["sentiment"] * x["effective_weight"])[:5]

    by_source = {}
    for n in all_news:
        src = n["source"]
        if src not in by_source: by_source[src] = {"total": 0, "bull": 0, "bear": 0}
        by_source[src]["total"] += 1
        if n["sentiment"] > 0.2: by_source[src]["bull"] += 1
        elif n["sentiment"] < -0.2: by_source[src]["bear"] += 1

    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_news": len(all_news), "avg_sentiment": round(avg_sentiment, 3),
        "mood": mood, "bullish_count": bullish, "bearish_count": bearish, "neutral_count": neutral,
        "fake_count": fake_count, "cross_confirmed": cross_confirmed,
        "by_source": by_source, "top_bullish": top_bull, "top_bearish": top_bear,
    }

    save(SENTIMENT_FILE, result)
    print("=" * 60)
    print(f"📊 Всего: {len(all_news)}")
    print(f"🎭 Настроение: {mood}")
    print(f"📈 Сентимент: {avg_sentiment:+.3f}")

    if top_bull:
        print("\n🟢 Топ бычьих:")
        for n in top_bull[:3]: print(f"   + {n['title_original'][:80]}\n     {n['url']}")

    if top_bear:
        print("\n🔴 Топ медвежьих:")
        for n in top_bear[:3]: print(f"   - {n['title_original'][:80]}\n     {n['url']}")
    print("=" * 60)

if __name__ == "__main__":
    main()