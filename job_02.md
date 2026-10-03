
         

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