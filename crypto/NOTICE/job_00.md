Какие рынки добавить (по приоритету)

Приоритет 1 — критично для модели

```
Европа:
  DAX, Euro Stoxx 50, FTSE, EUR/USD
  ← твой часовой пояс (UTC+2)
  ← Европа влияет на крипту утром

США (продолжение):
  NASDAQ, VIX, US10Y, US2Y
  ← главный драйвер крипты вечером
  ← VIX = страх, US10Y = доходность облигаций

USD/JPY — связка Азия ↔ глобал
```

Приоритет 2 — важно

```
Сырьё: Brent, Copper
  ← Copper = индикатор промышленности Китая
KOSPI (Korea), TAIEX (Taiwan)
  ← прямые соседи Китая, сильнее связаны
US2Y/US10Y spread
  ← инверсия кривой = сигнал рецессии
```

Приоритет 3 — позже

```
Options (Deribit):
  IV, skew, OI
Stablecoin flows
  ← приток USDT/USDC = деньги в крипту
On-chain расширение:
  Whales, exchange in/out
Google Trends
```

Не нужно (уже есть или не поможет):

· DXY, SPX, Gold — собираются в DB1 (external_market)
· Fear & Greed — есть
· On-chain hash rate — есть

Что я бы поставил следующим шагом: Европу
(DAX, EUR/USD) + VIX + USD/JPY. Они в том же
формате Yahoo, как Asia — 30 минут работы.

---

📋 ОТЧЁТ ARGUS (сквозной, за период 24.09 → 01.10.2026)

```markdown
# 🦉 ARGUS — СКВОЗНОЙ ОТЧЁТ
Период: 24.09 → 01.10.2026
Репо: nitroIm/argus-core, nitroIm/personal-books

═══════════════════════════════════════════
1. АРХИТЕКТУРА
═══════════════════════════════════════════

Два независимых контура:

BOOKS (бот + RAG):
  bot_host.py (VPS) + GitHub Actions
  Скрипты: scripts/ingest.py, ask.py и др.
  Данные: data/knowledge.json, faiss.index
  Статус: /ask работает, faiss собран (7.78 МБ)

CRYPTO (обучение + торговля):
  GitHub Actions + Supabase
  Данные: 2 базы — argus-db, argus-global-data
  Статус: ML учится, симулятор торгует

═══════════════════════════════════════════
2. BOOKS-КОНТУР (24.09 → 25.09)
═══════════════════════════════════════════

✅ data_audit.py v3 — SAFE, чистка с бэкапами
✅ ingest.py v7.5 — защита, дедуп
✅ faiss.index — 7.78 МБ собран
✅ /ask в боте работает
⚠️ data_audit.py и ingest.py v7.5 — НЕ залиты,
   но готовы
⚠️ Data Audit упал на 33-й попытке (баг с ">"
   в Telegram) — не критично

═══════════════════════════════════════════
3. CRYPTO-КОНТУР: ML (24.09 → 01.10)
═══════════════════════════════════════════

Эволюция модели:
  24.09: 330 строк, edge 0.0
  25.09: 378 строк, edge +0.0526 (пик)
  26.09: threshold эксперименты (откат)
  28.09: v5, 33 признака, edge +0.0388
  30.09: edge +0.064, autotune +0.136
  01.10: 670 строк, v5 работает

Pipeline:
  dataset.py v5 → train.py v5 → predict.py v3
  → signals.py → notify.py (TG)
  autotune.py v3 (best_params.json)
  rollback.py (откат модели из prev/)

Модель:
  LightGBM, 33 признака
  Top: consecutive_up, lower_wick_pct,
       bb_width_pct, hour_of_day, ema9_dist

Сигналы работают:
  SELL BTC [strong] conf 54.8% (29.09)
  BUY ETH [strong] conf 65.4% (29.09)
  Anti-spam: 6ч

Features (38 колонок):
  OHLCV-производные, wick/body, volume_ratio
  change_1d/3d/4h/24h/7d, trend_up
  funding_rate, funding_trend
  oi_change_pct, ls_ratio, taker_ratio
  ema9/21/50_dist, macd, macd_signal
  bb_upper/lower/width, dist_high/low_24h
  consecutive_up, session
  hour_of_day, day_of_week
  next_change_pct, next_direction
  computed_at

Events (225):
  было 36 → стало 225 (events.py v2)
  Новые: funding_spike_pos/neg,
  oi_spike, ls_long/short_extreme,
  rsi_overbought/oversold

Correlate v2 (01.10):
  fix directional (rise/fall)
  3 правила найдены (BTC 2, ETH 1)

═══════════════════════════════════════════
4. CRYPTO-КОНТУР: СБОР ДАННЫХ
═══════════════════════════════════════════

Первая база (argus-db, 23 таблицы):
  candles ~680, funding ~240
  features_hourly 670 (38 колонок)
  events 225
  external_market 168 (DXY/SPX/GOLD)
  orderbook_snapshots ~10
  onchain_metrics, macro_metrics, fear_greed

Вторая база (argus-global-data, 11 таблиц):
  🆕 НОВЫЙ УЗЕЛ (01.10)
  candles (SOL/BNB) — 10+
  funding, open_interest (SOL/BNB)
  asia_market — 150 (NIKKEI/SHANGHAI/HANGSENG/USDCNY)
  asia_alerts — лог алертов >2%
  impact_vectors — 40 пар (Asia ↔ крипта)
  asia_patterns — 0 (мало данных)
  candles_daily, asia_market_daily — под агрегацию
  collect_log, retention_log

═══════════════════════════════════════════
5. СИМУЛЯТОР MEXC
═══════════════════════════════════════════

runner.py v8.1:
  Проверка свежести analysis (3ч) и свечей (2ч)
  Cooldown 2ч после стопа
  Sanity: stop < price < target
  UUID, fallback get_price
  Стоп не режется ATR

Состояние:
  Balance $50.06
  Сделок 1 (WIN +$0.0612)
  Win rate 100%

═══════════════════════════════════════════
6. УЗЕЛ GLOBAL (01.10, новая работа)
═══════════════════════════════════════════

Задача: расширить модель данными из
внешних рынков, изолированно.

Файлы:
  crypto/global/db2.py — соединение с DB2
  crypto/global/schema.sql — 11 таблиц
  crypto/global/collect_sol_bnb.py v2
    → SOL/BNB: candles + funding + OI
    → фикс: OKX отдавал 720, обрезаем до 5
  crypto/global/collect_asia.py
    → Yahoo: NIKKEI, SHANGHAI, HANGSENG, USDCNY
  crypto/global/notify_asia.py
    → алерт в TG при |change| > 2%
    → anti-spam: один timestamp = один алерт
    → МОЛЧИТ, если движений нет ✅
  crypto/global/asia_patterns.py v2.2
    → lead-lag корреляции (1/2/3/6/12h)
    → rolling window 30 дней
    → impact_vectors (числовая матрица)
    → MIN_CORR_SAMPLES = 20
    → asof-join ±30 мин
  crypto/global/retention.py
    → 90 дней: сырьё → дневные агрегаты
    → НЕ запускаем пока (данных <90 дней)
  .github/workflows/crypto_global.yml
    → ручной + все task=all

Cron-job.org:
  ARGUS Global — каждые :10 часа
  URL: api.github.com/.../crypto_global.yml/dispatches
  Body: {"ref":"main","inputs":{"task":"all"}}
  POST + Authorization Bearer GH_PAT
  Протестировано: 204 No Content ✅

Что нашли в процессе:
  - SHANGHAI (:30) не совпадал с BTC (:00)
    → фикс asof ±30 мин
  - USDCNY давал мусор corr=0.99 при N=6
    → фикс MIN_CORR_SAMPLES=20
  - OKX rubik отдавал 720 OI вместо 5
    → фикс fresh_only()

Результат:
  40 impact_vectors записано
  Сильные связи:
    SHANGHAI → BTC (lag 6h) corr=-0.308
    SHANGHAI → ETH (lag 6h) corr=-0.338
    USDCNY → BTC (lag 12h) corr=-0.257
  Паттерны условные: 0 (мало данных, накопится)

═══════════════════════════════════════════
7. CRON-JOB.ORG (11 задач)
═══════════════════════════════════════════

  Collect      0 * * * *
  Simulator    5 * * * *
  Global       10 * * * *    ← новая
  Enrich       15 * * * *
  External     20 * * * *
  Detect       30 * * * *
  Notify       45 * * * *
  News         0 6 * * *
  Morning      0 7 * * *
  Evening      0 18 * * *
  Learn        45 1 * * *
  Autotune     вручную (раз в неделю)

═══════════════════════════════════════════
8. СЕКРЕТЫ (GitHub repo)
═══════════════════════════════════════════

  ARGUS_DB_URL        — DB1 (основная)
  ARGUS_DB_URL_2      — DB2 (global узел)
  GH_PAT
  MEXC_API_KEY, MEXC_API_SECRET
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
  TG_CHANNEL_ID
  OPENROUTER_AP*
  VK_GROUP_ID, VK_TOKEN

═══════════════════════════════════════════
9. КРИТИЧНЫЕ ПРАВИЛА
═══════════════════════════════════════════

1. Строки в коде ≤55 символов
2. Файлы целиком (телефон)
3. Логи на английском
4. datetime.now(timezone.utc), utcnow() — НЕТ
5. pathlib от SCRIPT_DIR.parent
6. External (DXY/SPX/GOLD) в ML НЕ включаем
   (USE_EXTERNAL=False) — шумит
7. Asia в features ML НЕ включаем
8. Не выдумывать колонки — проверять
   information_schema
9. При ошибке копипаста — заливать файл заново
10. train делает prev/ перед перезаписью

═══════════════════════════════════════════
10. В ОЧЕРЕДИ
═══════════════════════════════════════════

СРОЧНО:
1. Наблюдать Asia (завтра)
2. Проверить алерты >2% в TG

БЛИЖАЙШИЕ ДНИ:
3. Новые рынки в DB2:
   - DAX, Euro Stoxx, EUR/USD (Европа)
   - VIX, NASDAQ, US10Y (США)
   - USD/JPY, KOSPI, TAIEX
4. SOL, BNB → дополнительные монеты
   (XRP, DOGE — опционально)
5. Anti-spam v2 — не менять направление 2ч

ЧЕРЕЗ 3-4 ДНЯ:
6. Multi-timeframe (4h/15m) в features
7. Cross-asset (eth_btc_ratio, btc_returns_lag1)
8. Order book imbalance в features

ЧЕРЕЗ 1-2 НЕДЕЛИ:
9. Ensemble (LightGBM + XGBoost + CatBoost)
10. Regime detection (KMeans)
11. Trailing stop в симуляторе
12. Kelly Criterion

ДАЛЕКО:
13. LSTM/Transformer (нужно 10k+ строк)
14. VPS + Binance/Bybit
15. Автономия (brain/controller)

═══════════════════════════════════════════
11. ПРОМПТ ДЛЯ НОВОГО ЧАТА
═══════════════════════════════════════════

Привет! Продолжаем ARGUS.

Репо:
  nitroIm/argus-core (публичный)
  nitroIm/personal-books (публичный)

Два контура:
1. BOOKS — бот + RAG, /ask работает
2. CRYPTO — обучение ML + симулятор

СОСТОЯНИЕ (01.10.2026):
- features_hourly: 670 строк, 38 колонок
- ML: LightGBM v5, 33 признака, edge +0.064
- Симулятор v8.1: $50.06, 1 WIN
- События: 225
- Correlate v2: 3 правила
- DB2 (global): SOL/BNB + Asia + patterns
- impact_vectors: 40 (lead-lag)
- Cron: 11 задач (Global на :10)
- retention.py: лежит, ждёт 90 дней

КРИТИЧНЫЕ ПРАВИЛА:
1. Строки ≤55
2. Файлы целиком
3. Логи на английском
4. datetime.now(timezone.utc)
5. pathlib SCRIPT_DIR.parent
6. USE_EXTERNAL=False (DXY/SPX шумят)
7. Asia в ML не включаем — только алерты
8. Не выдумывать колонки
9. train делает prev/
10. Asia_alert: не спамить, только >2%

ТЕКУЩАЯ ЗАДАЧА: [ВСТАВИТЬ]
```



