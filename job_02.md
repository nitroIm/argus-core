Файл scripts/explorer.py v5 (целиком)

```python
# ============================================================
# ARGUS — АВТОНОМНЫЙ ИССЛЕДОВАТЕЛЬ (v5)
# v5: fix arXiv timeout 60s, Semantic Scholar пауза 2s
# v4: + OpenAlex, CORE, DOAJ
# ============================================================

import os
import sys
import json
import time
import random
import requests
from datetime import datetime, timezone
from pathlib import Path

CONFIG = {
    "max_topics_per_run": 5,
    "max_candidates_per_topic": 4,
    "max_size_mb": 90,
    "weights": {"gaps": 0.6, "random": 0.3, "trends": 0.1},
    "base_topics": [
        "algorithmic trading", "quantitative finance",
        "machine learning", "cryptocurrency",
        "technical analysis", "market microstructure",
        "philosophy", "quantum mechanics",
        "psychology", "economics",
    ],
}

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
DATA_DIR = REPO_ROOT / "data"

OBS_FILE = DATA_DIR / "observation.json"
EXPLORE_LOG = DATA_DIR / "explore_log.json"
SEEN_FILE = DATA_DIR / "explore_seen.json"
CANDIDATES_FILE = DATA_DIR / "scout_candidates.json"

CORE_API_KEY = (os.getenv("CORE_API_KEY") or "").strip()

TIMEOUT_ARXIV = 60
TIMEOUT_DEFAULT = 20
SEMANTIC_PAUSE = 2.0


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


REQUESTED_TOPIC = None
if len(sys.argv) > 1:
    REQUESTED_TOPIC = sys.argv[1].strip()
if not REQUESTED_TOPIC:
    REQUESTED_TOPIC = (
        os.environ.get("EXPLORE_TOPIC") or ""
    ).strip() or None


def decide_topics():
    obs = load_json(OBS_FILE, {})
    if REQUESTED_TOPIC:
        print(f"🎯 Запрошена тема: {REQUESTED_TOPIC}")
        return [{"topic": REQUESTED_TOPIC,
                 "reason": "запрос Actor"}]

    topics = []
    failed_words = obs.get("top_failed_words", [])
    gaps_count = int(
        CONFIG["max_topics_per_run"] * CONFIG["weights"]["gaps"]
    )
    for item in failed_words[:gaps_count]:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            word, count = item[0], item[1]
        elif isinstance(item, dict):
            continue
        else:
            continue
        if count >= 2:
            topics.append({"topic": word,
                           "reason": f"пробел: {count}"})

    random_count = int(
        CONFIG["max_topics_per_run"] * CONFIG["weights"]["random"]
    )
    pool = CONFIG["base_topics"]
    for t in random.sample(pool, min(random_count, len(pool))):
        topics.append({"topic": t, "reason": "случайная"})

    trend_count = CONFIG["max_topics_per_run"] - len(topics)
    if trend_count > 0:
        for t in random.sample(pool, min(trend_count, len(pool))):
            topics.append({"topic": t, "reason": "тренд"})

    unique = []
    names = set()
    for t in topics:
        if t["topic"] not in names:
            unique.append(t)
            names.add(t["topic"])
    return unique[:CONFIG["max_topics_per_run"]]


def search_arxiv(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "http://export.arxiv.org/api/query",
            params={"search_query": f"all:{topic}",
                    "start": 0,
                    "max_results": limit,
                    "sortBy": "relevance"},
            timeout=TIMEOUT_ARXIV,
        )
        r.raise_for_status()
        for entry in r.text.split("<entry>")[1:]:
            try:
                title = entry.split("<title>")[1]
                title = title.split("</title>")[0].strip()
                title = " ".join(title.split())
                link = entry.split("<id>")[1]
                link = link.split("</id>")[0].strip()
                arxiv_id = link.split("/abs/")[-1]
                results.append({
                    "title": title,
                    "url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
                    "source": "arxiv", "topic": topic,
                    "type": "paper",
                })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ arXiv: {e}")
    return results


def search_zenodo(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://zenodo.org/api/records",
            params={"q": topic, "size": limit,
                    "type": "publication",
                    "file_type": "pdf"},
            timeout=TIMEOUT_DEFAULT,
        )
        r.raise_for_status()
        for hit in r.json().get("hits", {}).get("hits", []):
            try:
                title = hit.get("metadata", {}).get("title", "?")
                record_id = hit.get("id")
                pdf_url, size = None, 0
                for f in hit.get("files", []):
                    if f.get("key", "").lower().endswith(".pdf"):
                        pdf_url = f["links"]["self"]
                        size = f.get("size", 0)
                        break
                if pdf_url:
                    results.append({
                        "title": title, "url": pdf_url,
                        "source": "zenodo", "topic": topic,
                        "size_mb": round(size / 1024 / 1024, 1),
                        "page_url": (
                            f"https://zenodo.org/records/"
                            f"{record_id}"
                        ),
                        "type": "book",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Zenodo: {e}")
    return results


def search_crossref(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://api.crossref.org/works",
            params={"query": topic, "rows": limit,
                    "filter": (
                        "type:journal-article,"
                        "has-full-text:true"
                    )},
            timeout=TIMEOUT_DEFAULT,
        )
        r.raise_for_status()
        for item in r.json().get("message", {}).get("items", []):
            try:
                title = item.get("title", ["?"])[0]
                pdf_url = None
                for l in item.get("link", []):
                    if l.get("content-type") == "application/pdf":
                        pdf_url = l.get("URL")
                        break
                if pdf_url:
                    results.append({
                        "title": title, "url": pdf_url,
                        "source": "crossref", "topic": topic,
                        "type": "paper",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ Crossref: {e}")
    return results


def search_semantic_scholar(topic, limit=3):
    """v5: пауза 2с перед запросом — обход 429."""
    results = []
    time.sleep(SEMANTIC_PAUSE)
    try:
        r = requests.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": topic, "limit": limit,
                    "fields": "title,openAccessPdf"},
            timeout=TIMEOUT_DEFAULT,
        )
        if r.status_code == 429:
            print("   ⏸ Semantic Scholar: 429, пропуск")
            return []
        r.raise_for_status()
        for item in r.json().get("data", []):
            pdf = item.get("openAccessPdf")
            if pdf and pdf.get("url"):
                results.append({
                    "title": item.get("title", "?"),
                    "url": pdf["url"],
                    "source": "semantic_scholar",
                    "topic": topic, "type": "paper",
                })
    except Exception as e:
        print(f"   ⚠️ Semantic Scholar: {e}")
    return results


def search_openalex(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://api.openalex.org/works",
            params={
                "search": topic,
                "per-page": limit,
                "filter": "is_oa:true",
                "mailto": "argus@example.com",
            },
            timeout=TIMEOUT_DEFAULT,
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            try:
                title = item.get("title") or "?"
                best = item.get("best_oa_location") or {}
                pdf_url = best.get("pdf_url")
                if not pdf_url:
                    locs = item.get("locations", [])
                    for loc in locs:
                        if loc.get("pdf_url"):
                            pdf_url = loc["pdf_url"]
                            break
                if pdf_url:
                    results.append({
                        "title": title,
                        "url": pdf_url,
                        "source": "openalex",
                        "topic": topic, "type": "paper",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ OpenAlex: {e}")
    return results


def search_core(topic, limit=3):
    if not CORE_API_KEY:
        return []
    results = []
    try:
        r = requests.get(
            "https://api.core.ac.uk/v3/search/works",
            params={"q": topic, "limit": limit},
            headers={
                "Authorization": f"Bearer {CORE_API_KEY}"
            },
            timeout=TIMEOUT_DEFAULT,
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            try:
                title = item.get("title", "?")
                pdf_url = item.get("downloadUrl")
                if pdf_url:
                    results.append({
                        "title": title,
                        "url": pdf_url,
                        "source": "core",
                        "topic": topic, "type": "paper",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ CORE: {e}")
    return results


def search_doaj(topic, limit=3):
    results = []
    try:
        r = requests.get(
            "https://doaj.org/api/search/articles/"
            + requests.utils.quote(topic),
            params={"pageSize": limit},
            timeout=TIMEOUT_DEFAULT,
        )
        r.raise_for_status()
        for item in r.json().get("results", []):
            try:
                bib = item.get("bibjson", {})
                title = bib.get("title", "?")
                links = bib.get("link", [])
                pdf_url = None
                for l in links:
                    if l.get("type") == "fulltext":
                        pdf_url = l.get("url")
                        break
                if pdf_url and pdf_url.lower().endswith(".pdf"):
                    results.append({
                        "title": title,
                        "url": pdf_url,
                        "source": "doaj",
                        "topic": topic, "type": "paper",
                    })
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️ DOAJ: {e}")
    return results


SOURCES = [
    ("arXiv", search_arxiv),
    ("Zenodo", search_zenodo),
    ("Crossref", search_crossref),
    ("SemanticScholar", search_semantic_scholar),
    ("OpenAlex", search_openalex),
    ("CORE", search_core),
    ("DOAJ", search_doaj),
]


def main():
    print("🧭 ARGUS EXPLORER v5")
    print("=" * 50)

    topics = decide_topics()
    print(f"\n📋 Тем: {len(topics)}")
    for t in topics:
        print(f"   • {t['topic']} ({t['reason']})")

    seen = load_json(SEEN_FILE, {"urls": [], "topics": {}})
    seen_urls = set(seen.get("urls", []))

    candidates = []
    run_seen = set()

    for topic_data in topics:
        topic = topic_data["topic"]
        print(f"\n🔍 Тема: {topic}")
        for source_name, search_fn in SOURCES:
            results = search_fn(
                topic,
                limit=CONFIG["max_candidates_per_topic"],
            )
            if results:
                print(f"   📡 {source_name}: {len(results)}")
            for r in results:
                url = r["url"]
                if url in seen_urls or url in run_seen:
                    continue
                if r.get("size_mb", 0) > CONFIG["max_size_mb"]:
                    continue
                r["found_at"] = datetime.now(
                    timezone.utc
                ).isoformat()
                r["reason"] = topic_data["reason"]
                candidates.append(r)
                seen_urls.add(url)
                run_seen.add(url)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seen["urls"] = list(seen_urls)
    seen.setdefault("topics", {})
    for t in topics:
        name = t["topic"]
        dates = set(seen["topics"].get(name, []))
        dates.add(today)
        seen["topics"][name] = sorted(list(dates))[-10:]
    save_json(SEEN_FILE, seen)

    existing = load_json(
        CANDIDATES_FILE, {"candidates": [], "pending": {}}
    )
    existing["candidates"] = (
        existing.get("candidates", []) + candidates
    )
    existing["generated_at"] = datetime.now(
        timezone.utc
    ).isoformat()
    existing["total"] = len(existing["candidates"])
    save_json(CANDIDATES_FILE, existing)

    log = load_json(EXPLORE_LOG, {"runs": []})
    log["runs"].append({
        "time": datetime.now(timezone.utc).isoformat(),
        "topics": [t["topic"] for t in topics],
        "candidates_found": len(candidates),
    })
    if len(log["runs"]) > 100:
        log["runs"] = log["runs"][-100:]
    save_json(EXPLORE_LOG, log)

    print("\n" + "=" * 50)
    print(f"✅ Новых кандидатов: {len(candidates)}")
    print(f"📊 Всего в очереди: {existing['total']}")
    print("=" * 50)


if __name__ == "__main__":
    main()
```

Что изменилось:

· TIMEOUT_ARXIV = 60 (было 20)
· SEMANTIC_PAUSE = 2.0 — пауза перед каждым запросом
· При 429 — молча пропускает вместо exception

Залей → запусти → скинь лог.

---

📋 ОТЧЁТ ЗА СЕССИЮ 02.10.2026 → 03.10.2026

```
═══════════════════════════════════════════
ARGUS — СЕССИЯ 02-03.10.2026
═══════════════════════════════════════════

РЕПО:
  nitroIm/argus-core
  nitroIm/personal-books

СЕКРЕТЫ (актуально):
  ARGUS_DB_URL, ARGUS_DB_URL_2
  GH_PAT
  MEXC_API_KEY/SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  TG_CHANNEL_ID
  OPENROUTER_AP*
  VK_GROUP_ID, VK_TOKEN
  + CORE_API_KEY (получить, добавить)
```

1. BOOKS-КОНТУР — ЧТО СДЕЛАНО СЕГОДНЯ

Цель: автоматическое скачивание книг в репо + тренировка.

Что работает:

```
Pipeline:
  explorer.py → scout_candidates.json
    ↓
  proposer.py → TG карточки (5 за раз)
    ↓
  жмёшь ✅ в TG
    ↓
  approved_download.yml → approve_handler.py
    ↓
  collector.py → base.py → PDF в books/
    ↓
  /train вручную → ingest → train → faiss
```

Файлы обновлены:

Файл Версия Что изменилось
scripts/explorer.py v3 → v5 +OpenAlex, +CORE, +DOAJ, fix arXiv/Semantic
scripts/approve_handler.py v6 → v8 Ложное «✅» убрано, «уже есть» = успех
scripts/sources/base.py v3 → v4 +User-Agent, +Content-Type, +safe_name, лимит 25→90 МБ

Что качаем:

· arXiv — научные статьи ✅
· Zenodo — OA статьи/книги ✅
· Crossref — журналы ✅
· OpenAlex — 250M+ работ ✅
· DOAJ — OA журналы ✅
· Semantic Scholar — есть пауза от 429
· CORE — ждёт ключа

Что НЕ работает:

· archive.org — 403 (блокирует GitHub Actions)
· libgen / пиратские — не используем

Состояние Books на сейчас:

```
Книг: 40
Чанков: 6705
Новых за сессию: 237 (+3 книги через arXiv)
```

Очередь кандидатов: 78

2. КРИПТО-КОНТУР (из стартового блока, без изменений сегодня)

DB1 (argus-db) — 23 таблицы:

```
candles ~680
features_hourly 670+ (38 колонок)
events 225
external_market 168
ML: LightGBM v5, 33 признака, edge +0.064
```

DB2 (argus-global-data) — 11 таблиц:

```
candles SOL/BNB
funding_rates, open_interest SOL/BNB
asia_market — 14 рынков:
  NIKKEI, SHANGHAI, HANGSENG, USDCNY
  DAX, SX5E, FTSE, EURUSD
  VIX, NASDAQ, US10Y
  USDJPY, KOSPI, TAIEX
asia_alerts — лог >2%
impact_vectors — 180+ пар
asia_patterns — 68
candles_daily, asia_market_daily
collect_log, retention_log
```

Симулятор MEXC v9 (с explorer внутри):

```
Баланс: $39.84
Сделок: 13 (6W/7L)
Все LONG — SHORT не работает
Explorer дал первый SHORT (ETH score -0.53)
```

3. ЧТО ПОНЯЛИ ПРО СИМУЛЯТОР

Проблемы:

1. Только LONG. Причина: в коде direction="LONG" зашито + build_levels считает только вверх
2. Explorer подключён слабо. Симулятор вызывает explorer.analyze(), но использует только direction (LONG/SHORT/NONE)
3. Explorer даёт NONE часто — это правильно, но симулятор не понимает что делать
4. Веса голосов не обучаются — все = 1.0 в weights.json

Что explorer уже умеет:

```
10 источников:
  ml, news, events, causal, levels,
  patterns, correlations, db2_patterns,
  db2_vectors, anomaly
```

Первый живой сигнал:

```
ETH SHORT score=-0.5313
  events raw=-0.57  ← 5× ls_long_extreme
  news raw=-0.15
  levels raw=-0.10
```

4. ЧТО ОСТАЛОСЬ СДЕЛАТЬ (приоритет)

СРОЧНО (1-2 дня)

1. SHORT в симуляторе — реально работает
   · runner.py v9 — direction из сигнала
   · build_levels() — инверсия для SHORT
   · check_stop_target — учёт SHORT
   · close_position — PnL для SHORT
2. Тест SHORT на 5-10 сделках
   · Проверить что открывается/закрывается корректно

БЛИЖАЙШЕЕ (3-5 дней)

3. learn.py — веса голосов
   · Читает trades.json
   · Считает winrate по каждому источнику
   · Обновляет state/weights.json
   · Cron: раз в сутки
4. context.py — отдельный модуль из DB2
   · Читает asia_market, impact_vectors, asia_patterns
   · Даёт score для конкретной монеты
   · Пока частично в explorer
5. books_reader.py — RAG для симулятора
   · Вызов /ask API через Books
   · Голос «Books» в explorer

СРЕДНЕЕ (1-2 недели)

6. Multi-timeframe (4h/15m) в features
7. Cross-asset (eth_btc_ratio, btc_returns_lag1)
8. Order book imbalance в features
9. Ensemble (LightGBM + XGBoost + CatBoost)
10. Regime detection (KMeans по волатильности)

ДАЛЕКОЕ (месяц+)

11. Нейросеть/LSTM — после 10k строк
12. VPS + Binance/Bybit
13. Автономия (brain/controller)

5. КРИТИЧНЫЕ ПРАВИЛА (не нарушать)

```
1. Строки в коде ≤55 символов
2. Файлы отдаются ЦЕЛИКОМ
3. Логи на английском
4. datetime.now(timezone.utc) — utcnow() запрещён
5. pathlib от SCRIPT_DIR.parent
6. USE_EXTERNAL=False (DXY/SPX шумят)
7. Asia/Europe в ML пока НЕ включаем
8. Не выдумывать имена колонок
9. train.py делает prev/ перед перезаписью
10. Notify не спамит — молчит если <2%
```

6. ЧТО ЕЩЁ НУЖНО ОТ ТЕБЯ

Для крипто:

· Дождаться завершения watch-фазы симулятора → прислать лог
· Согласовать: SHORT добавляем сейчас или после learn.py?

Для Books:

· Получить CORE_API_KEY на core.ac.uk/services/api
· Добавить в GitHub Secrets как CORE_API_KEY

Для cron:

· Настроить ARGUS Explorer / Proposer в cron-job.org (у тебя их нет)
· Или оставить вручную

7. ПРОМПТ ДЛЯ НОВОГО ЧАТА

```
Привет! Продолжаем ARGUS.

Репо:
  nitroIm/argus-core
  nitroIm/personal-books

Два контура:

BOOKS (argus-core):
  - explorer.py v5 (7 источников)
  - proposer.py v3.3 → TG карточки по 5
  - approve_handler.py v8
  - collector.py + base.py v4
  - ingest.py v7.5
  - 40 книг, 6705 чанков

CRYPTO:
  DB1: features 670, ML edge +0.064
  DB2: 14 рынков, impact_vectors 180+
  Симулятор v9, баланс $39.84
  Explorer v3 внутри симулятора — 10 источников
  Сделки: все LONG, SHORT не работает

СРОЧНО:
  1. SHORT в симуляторе (runner.py)
  2. learn.py — веса голосов
  3. context.py — модуль из DB2

КРИТИЧНЫЕ ПРАВИЛА:
  - Строки ≤55
  - Файлы целиком
  - datetime.now(timezone.utc)
  - Не выдумывать колонки

ТЕКУЩАЯ ЗАДАЧА: [вставить]
```

Сохрани отчёт в TG Saved Messages. Сможешь перенести в новый чат без потерь. 🦉