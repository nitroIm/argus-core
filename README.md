Ниже — единый README. Вставляется в новый чат, всё подхватывается. Лишнее отсечено, дубликаты убраны.

```markdown
# 🦉 ARGUS — MASTER README

Autonomous Research & Generative Unified System
Обновлено: 2026-10-06

═══════════════════════════════════════════
1. МИССИЯ
═══════════════════════════════════════════

Самообучающаяся система: читает книги,
собирает данные с бирж, находит паттерны,
предсказывает цену. Растёт модуль за модулем.

Принципы:
  • Качество > скорость
  • Данные не теряются никогда
  • Один донор = 95%, fallback по метрике
  • Дубликаты → PRIMARY KEY
  • Битые данные → rejected_data
  • knowledge.json НИКОГДА не удалять

═══════════════════════════════════════════
2. РЕПОЗИТОРИИ И БАЗЫ
═══════════════════════════════════════════

Репозитории (публичные):
  nitroIm/argus-core
  nitroIm/personal-books

Два независимых крипто-контура (БД):
  DB1  ARGUS_DB_URL    → BTCUSDT, ETHUSDT
  DB2  ARGUS_DB_URL_2  → SOLUSDT, BNBUSDT
                        + Asia/Europe (некрипто)

Крипто-таблицы зеркальны в DB1/DB2
(одинаковые колонки, разные монеты).

Некрипто — только в одной базе:
  DB1: macro_metrics, onchain_metrics,
       external_market, fear_greed,
       market_context
  DB2: asia_market, asia_market_daily,
       asia_alerts, asia_patterns,
       impact_vectors

Внешние узлы:
  VPS Wispbyte  — bot_host.py, aiogram 3.x
  GitHub Actions — тяжёлое (train, collect)
  cron-job.org  — все расписания
  Supabase      — 2 проекта (DB1, DB2)

Часовой пояс отчётов: Europe/Kaliningrad.

═══════════════════════════════════════════
3. КОНТУР BOOKS (RAG + бот)
═══════════════════════════════════════════

Назначение: читает книги, отвечает /ask,
наполняет гайды для крипто-симулятора.

Пайплайн:
  books/*.pdf
    → ingest.py v7.7 → knowledge.json
    → train_embeddings.py v3.4
    → build_index.py v3.4 → faiss.index
    → search.py / ask.py → Telegram

Автономия (поиск новых книг):
  explorer.py v5 → scout_candidates.json
    → proposer.py v3.3 → TG (5 карточек)
    → approve:<sid>
    → approved_download.yml → approve_handler v8
    → collector.py + sources/base v4
    → books/ → следующий /train

Ключевые файлы:
  bot_host.py               aiogram, VPS
  scripts/ingest.py         v7.7
  scripts/train_embeddings.py v3.4
  scripts/build_index.py    v3.4
  scripts/search.py         v9 (keyword + FAISS)
  scripts/ask.py            RAG
  scripts/finetune.py       v1.2 (не запускается)
  scripts/explorer.py       v5 (7 источников)
  scripts/proposer.py       v3.3
  scripts/approve_handler.py v8
  scripts/sources/base.py   v4

Данные:
  data/knowledge.json         НЕ удалять
  data/chunks_for_index.json  метаданные
  data/pending_cards.json     кандидаты
  data/scout_candidates.json  найденные
  faiss.index                 13 МБ
  models/argus-embeddings/    e5-small
  books/                      очередь PDF

Состояние:
  Книг: 53
  Чанков: 8862
  Гайдов: 19 (01…19)
  FAISS: IndexFlatIP, avg_score

Гайды:
  01_rsi, 02_macd, 03_bollinger,
  04_atr, 05_volume, 06_candles,
  07_risk, 08_psychology,
  09_trend, 10_sr, 11_orderflow,
  12_crypto, 13_exchange,
  14_fibonacci, 15_patterns,
  16_divergence, 17_money_management,
  18_mexc, 19_binance

Источники скачивания:
  ✅ arXiv, Zenodo, Crossref,
     OpenAlex, DOAJ
  ⏳ Semantic Scholar (429)
  ⏳ CORE (нужен ключ)

Что работает:
  ✅ /ask находит гайды первым
  ✅ Категории исправлены (misc → trading)
  ✅ Дубль TG-ответов убран
  ✅ Скачивание PDF → books/ → commit

Что не закрыто:
  ⏳ Перевод EN→RU (нужны torch,
     transformers, sentencepiece,
     sacremoses в search.yml)
  ⏳ Fine-tuning e5-small — при 20+
     книгах (сейчас 373 пары, мало)

═══════════════════════════════════════════
4. КОНТУР CRYPTO (ML + симулятор)
═══════════════════════════════════════════

Назначение: собирает часовые данные
по 4 монетам, обучает 6 моделей,
предсказывает return %, торгует
в симуляторе MEXC.

Пайплайн (каждый час):
  collect → enrich → detect → notify
  обучение — раз в сутки (24h)
  симулятор — раз в час (:05)

ML-контур (crypto/learn/):
  dataset.py    v12  (61 фича, purge 12)
  train.py      v13  (early stopping)
  predict.py    v15  (regression-aware)
  signals.py    v2
  notify.py     v3
  runner.py     v5   (REGRESSION_VERSIONS)
  export.py     v6
  audit.py      v1
  clean_models.py v1
  train_xgb.py  v1
  train_cat.py  v1
  train_ridge.py v1
  train_mlp.py  v1
  train_lstm.py v1
  train_weights.py v3 (per-symbol)

6 моделей: lgb, xgb, cat, ridge, mlp, lstm.
Все предсказывают return % (регрессия).
Веса per-symbol в ensemble_weights.json.

Артефакты моделей:
  lgb_{sym}.txt, xgb_{sym}.json,
  cat_{sym}.cbm, ridge_{sym}.joblib,
  mlp_{sym}.joblib, lstm_{sym}.pt,
  scaler_*_{sym}.joblib,
  meta_{algo}_{sym}.json,
  ensemble_weights.json

Данные в БД (крипто, зеркально):
  candles (1h, 1d), candles_daily
  funding_rates
  open_interest
  long_short_ratio
  taker_flow
  liquidations
  orderbook_snapshots
  features_hourly (38 колонок)
  price_patterns
  events
  causal_links
  predictions
  ml_models
  collect_log, rejected_data,
  cross_check, anomaly_log,
  retention_log

Внешние рынки (DB2, некрипто):
  asia_market          — 14 рынков
  asia_market_daily
  asia_alerts
  asia_patterns        — 68 правил
  impact_vectors       — 180+ пар

14 рынков:
  NIKKEI, SHANGHAI, HANGSENG, USDCNY,
  DAX, SX5E, FTSE, EURUSD,
  VIX, NASDAQ, US10Y,
  USDJPY, KOSPI, TAIEX

Симулятор (crypto/mexc/simulator_01/):
  runner.py v9  (внутри explorer)
  state/portfolio.json
  state/positions.json
  state/trades.json
  state/weights.json

  Баланс: $39.84
  Сделок: 13 (6W/7L)
  Все LONG — SHORT не работает

Explorer внутри симулятора (10 источников):
  ml, news, events, causal, levels,
  patterns, correlations, db2_patterns,
  db2_vectors, anomaly

Collectors (crypto/collect/):
  pipeline.py v10
  external.py v3
  macro.py v2
  spot_prices.py v4
  priority.py v3
  binance_history.py v3 (Binance Vision)
  verify_history_binance.py v2

Enrich (crypto/enrich/):
  features.py v9
  runner.py v2
  patterns.py, levels.py,
  events.py, causal.py, correlate.py

Detect (crypto/detect/):
  anomaly.py v4

Infrastructure:
  db.py  v5  (reconnect)
  db2.py v3  (reconnect)
  config.py v6

═══════════════════════════════════════════
5. WORKFLOWS (GitHub Actions)
═══════════════════════════════════════════

Крипто:
  crypto_learn.yml       v8   (6 моделей)
  crypto_notify.yml      v3   (torch CPU)
  crypto_detect.yml      v3
  crypto_enrich.yml      v5b
  crypto_collect.yml     v3
  crypto_global.yml      v1
  feature_audit.yml      v1
  binance_vision.yml     v1
  binance_history.yml    v1
  clean_models.yml       v1
  external_once.yml      v1
  simulator_01.yml

Books:
  ingest.yml
  ask.yml
  search.yml
  approved_download.yml
  explorer.yml
  proposer.yml
  benchmark.yml
  finetune.yml (не запускается)

═══════════════════════════════════════════
6. CRON-JOB.ORG
═══════════════════════════════════════════

  Collect       0 * * * *
  Simulator     5 * * * *
  Global        10 * * * *
  Enrich        15 * * * *
  External      20 * * * *
  Detect        30 * * * *
  Notify        45 * * * *
  News          0 6 * * *
  Morning       0 7 * * *
  Evening       0 18 * * *
  Learn         45 1 * * *

Все задачи: POST + Bearer GH_PAT,
Body: {"ref":"main"}.

═══════════════════════════════════════════
7. СЕКРЕТЫ (GitHub Secrets)
═══════════════════════════════════════════

  ARGUS_DB_URL         DB1
  ARGUS_DB_URL_2       DB2
  GH_PAT
  MEXC_API_KEY, MEXC_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  TG_CHANNEL_ID
  OPENROUTER_AP*
  VK_GROUP_ID, VK_TOKEN
  + CORE_API_KEY (получить)

═══════════════════════════════════════════
8. СХЕМА БД — DB1 (23 таблицы)
═══════════════════════════════════════════

Крипто:
  candles (symbol, timeframe, timestamp)
  candles_daily (symbol, timestamp)
  funding_rates (symbol, timestamp)
  open_interest (symbol, timestamp)
  long_short_ratio (symbol, timestamp)
  taker_flow (symbol, timestamp)
  liquidations (symbol, ts, side, price, qty)
  orderbook_snapshots (symbol, timestamp)

Некрипто (только DB1):
  market_context (timestamp)
  external_market (symbol, timestamp)
  macro_metrics (symbol, timestamp)
  fear_greed (timestamp)
  onchain_metrics (symbol, timestamp)

Derived:
  features_hourly (38 колонок, PK sym+ts)
  price_patterns
  events
  causal_links

ML:
  predictions
  ml_models

Audit:
  collect_log, rejected_data,
  cross_check, anomaly_log,
  retention_log

═══════════════════════════════════════════
9. СХЕМА БД — DB2
═══════════════════════════════════════════

Крипто — зеркало DB1 (те же 8 таблиц
candles…orderbook_snapshots + features/
patterns/events/causal/predictions/
ml_models + audit).

Некрипто (только DB2):
  asia_market (symbol, timestamp)
  asia_market_daily (symbol, timestamp)
  asia_alerts (id SERIAL)
  impact_vectors (id SERIAL)
  asia_patterns (id SERIAL)

═══════════════════════════════════════════
10. КРИТИЧНЫЕ ПРАВИЛА
═══════════════════════════════════════════

1.  Строки в коде ≤55 символов
2.  Файлы даются ЦЕЛИКОМ
3.  Логи на английском
4.  datetime.now(timezone.utc),
    utcnow() ЗАПРЕЩЁН
5.  pathlib от SCRIPT_DIR.parent
6.  Не выдумывать имена колонок —
    проверять information_schema
7.  knowledge.json НЕ удалять
8.  DB1/DB2 не смешивать
9.  SQL UPDATE/DELETE — только
    по явному запросу
10. Все batch-запросы где можно
11. Cron — только cron-job.org
12. Не трогать данные в БД без
    явного разрешения
13. train.py делает prev/ перед
    перезаписью
14. USE_EXTERNAL=False
    (DXY/SPX/GOLD шумят)
15. Asia/Europe в ML пока НЕ
    включаем — только алерты

═══════════════════════════════════════════
11. СИСТЕМА ВЕСОВ
═══════════════════════════════════════════

ensemble_weights.json (per-symbol):
  BTC  — cat=1.00
  ETH  — все отрицательные,
         allowed=False
  SOL  — lstm=0.41, cat=0.26,
         mlp=0.16, ridge=0.08,
         lgb=0.05, xgb=0.04
  BNB  — cat=0.52, xgb=0.42,
         lgb=0.06

learn_weights.py (для explorer):
  w_new = w_old × (1 + 0.2 ×
          (hit_rate − 0.5))
  Границы [0.3, 2.0], N >= 5
  Пишет state/weights.json

═══════════════════════════════════════════
12. ЧТО РАБОТАЕТ / ЧТО НЕТ
═══════════════════════════════════════════

Работает:
  ✅ 6 моделей учатся (~10 мин)
  ✅ Веса пересчитываются per-symbol
  ✅ BTC/SOL/BNB торгуются
  ✅ DB1/DB2 разделение везде
  ✅ Books /ask находит гайды
  ✅ Explorer + 10 источников
  ✅ Симулятор LONG
  ✅ Азиатские алерты в TG

Не работает / в работе:
  ⏳ ETHUSDT в блоке (все 6 отриц.)
  ⏳ SHORT в симуляторе
  ⏳ learn.py — веса голосов
  ⏳ context.py — модуль DB2
  ⏳ books_reader.py — RAG для
    симулятора
  ⏳ Orderbook SOL/BNB (нет
    сборщика)
  ⏳ Binance Vision — качается
  ⏳ Перевод EN→RU в Books

═══════════════════════════════════════════
13. ОТКРЫТЫЕ ЗАДАЧИ
═══════════════════════════════════════════

СРОЧНО:
  1. Дождаться Binance Vision
  2. Загрузчик parquet → БД
     (ON CONFLICT DO NOTHING)
  3. Пересборка features_hourly
  4. Переобучение моделей
  5. Починить crypto_detect.yml
     (Line 58: 'run' defined twice)

БЛИЖАЙШЕЕ:
  6. SHORT в симуляторе
  7. learn.py — веса explorer
  8. context.py — модуль DB2

СРЕДНЕЕ:
  9. Multi-timeframe (4h/15m)
  10. Cross-asset в features
  11. Order book imbalance
  12. Regime detection

ДАЛЕКО:
  13. LSTM/Transformer
  14. VPS + Binance/Bybit
  15. Автономия (brain)

═══════════════════════════════════════════
14. ПРОМПТ ДЛЯ НОВОГО ЧАТА
═══════════════════════════════════════════

Привет! Продолжаем ARGUS.

Репо:
  nitroIm/argus-core (публичный)
  nitroIm/personal-books (публичный)

Два контура:
1. BOOKS — бот + RAG (53 книги,
   8862 чанка, 19 гайдов, FAISS 13 МБ)
2. CRYPTO — 6 моделей ML + симулятор MEXC

БД:
  DB1: BTC/ETH + macro/onchain/fear/
       external/market_context
  DB2: SOL/BNB + Asia/Europe
  Крипто-таблицы зеркальны.

Модели: lgb/xgb/cat/ridge/mlp/lstm.
Все — регрессия return %.
ensemble_weights.json per-symbol.

Симулятор MEXC v9: $39.84, 13 сделок,
только LONG. Explorer 10 источников.

КРИТИЧНЫЕ ПРАВИЛА:
1. Строки ≤55
2. Файлы целиком
3. Логи на английском
4. datetime.now(timezone.utc)
5. pathlib от SCRIPT_DIR.parent
6. Не выдумывать колонки
7. knowledge.json не удалять
8. DB1/DB2 не смешивать
9. SQL UPDATE/DELETE только
   по запросу
10. USE_EXTERNAL=False
11. Asia/Europe в ML не включаем
12. Cron только cron-job.org

ТЕКУЩАЯ ЗАДАЧА: [ВСТАВИТЬ]
```

Готово. Вставляй в новый чат — подхватит всё.