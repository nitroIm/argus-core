📋 ОТЧЁТ ARGUS — 25.09.2026

---

✅ СДЕЛАНО СЕГОДНЯ

Crypto — Features

1. crypto/enrich/features.py v4.4

· Расчёт daily-признаков: change_1d, change_3d, trend_up
· Calendar-признаки: hour_of_day, day_of_week
· SQL INSERT обновлён под 5 новых колонок
· Работает: 374 строки, все колонки заполнены

2. SQL — 5 новых колонок в features_hourly:

```sql
change_1d FLOAT
change_3d FLOAT
trend_up SMALLINT
hour_of_day SMALLINT
day_of_week SMALLINT
```

Crypto — External Market

3. Таблица external_market в Supabase
4. crypto/collect/external.py v1 — Yahoo Finance

· DXY, SPX, Gold
· Без ключа, 1h интервал, 2 дня истории
· ON CONFLICT DO UPDATE

5. .github/workflows/external_market.yml — ручной + cron
6. Первый запуск: 80 строк (DXY 36, SPX 9, GOLD 35)

Crypto — ML

7. crypto/learn/train.py v3

· prev/ — страховка перед перезаписью
· is_unbalance=True — попытка убрать bias
· Confusion matrix в meta

8. Результаты обучения:

· 372 строки, train 297 / test 75
· accuracy 0.56, baseline 0.56, edge 0.0
· Вывод: на 297 строках паттерна нет. Нужно 500+

9. Top features:

```
change_4h        41.16
lower_wick_pct   29.92
change_1d        14.22  ← новый
ls_ratio          7.19
funding_rate      6.09
```

10. crypto/learn/export.py — работает

· Кладёт в crypto/learn/export/: модель + meta + CSV + README

Crypto — Симулятор

11. crypto/mexc/simulator_01/runner.py v8.1

· Проверка stop/target через 1h свечи Supabase (а не только 1m MEXC)
· Time exit в плюсе (не трогает минус)
· После закрытия — сразу ищет новый сигнал

Cron

12. Создано:

· ARGUS External: 20 * * * *
· ARGUS Learn: 45 1 * * *

Итого 9 задач в cron-job.org. Лимит 50 — запас огромный.

---

📊 СОСТОЯНИЕ СИСТЕМЫ

Показатель Значение
features_hourly 374
candles 1h 332+
candles 1d 188
external_market 80
Симулятор v -8 `.1 работает
eth ML модель
ML prev сохранена
Экспорт готов
Cron задач 9 / 50

---

🎯 ЧТО ДАЛЬШЕ

Ближайшее:

1. Features + external — 4 колонки:
   · dxy_change_pct
   · spx_change_pct
   · gold_change_pct
     _btc_ratio`
2. ML — ждать накопления. 500+ строк. Через 3-4 дня.
3. ML — улучшить таргет (когда данных хватит):
   · Не >0, а >+0.3% (значимое движение)
   · Или горизонт 4h вместо 1h

Среднее:

4. Order book imbalance — стакан MEXC → в features
5. Liquidations — OKX endpoint → в features

Долгое:

6. On-chain метрики
7. News sentiment в features
8. Автономия (brain/controller)
9. Свой сервер / VPS

---

⚠️ ВАЖНЫЕ ФАКТЫ

Данные не затираются:

· features_hourly растёт +48/сутки
· Старые строки участвуют в обучении навсегда
· Модель пересчитывается на ВСЕХ данных каждый раз

Supabase Free: 500 МБ → хватит на годы

· ~2.5 МБ/месяц
· Retention 90 дней не нужен пока

Модель портируема:

· crypto/learn/export/ — чемодан для миграции
· Работает где угодно без переобучения

Модель prev:

· 1 текущая + 1 предыдущая
· Откат: скопировать prev/lgb_model.txt → models/

Стратегия ML:

· 300 строк = шум, edge 0
· 500+ = первые сигналы
· 1000+ = рабочая модель
· 2000+ = стабильно

---

📁 КЛЮЧЕВЫЕ ФАЙЛЫ

```
crypto/
├─ collect/
│  ├─ external.py           ← Yahoo DXY/SPX/Gold
│  └─ pipeline.py
├─ enrich/
│  └─ features.py           ← v4.4
├─ learn/
│  ├─ dataset.py
│  ├─ train.py              ← v3
│  ├─ evaluate.py
│  ├─ predict.py
│  ├─ signals.py
│  ├─ runner.py
│  ├─ export.py
│  ├─ models/
│  │  ├─ lgb_model.txt
│  │  ├─ model_meta.json
│  │  └─ prev/
│  ├─ export/
│  │  ├─ lgb_model.txt
│  │  ├─ model_meta.json
│  │  ├─ features_hourly.csv
│  │  └─ README.md
│  ├─ last_predictions.json
│  └─ last_signals.json
└─ mexc/
   └─ simulator_01/
      ├─ runner.py          ← v8.1
      └─ state/
         ├─ portfolio.json
         ├─ positions.json
         ├─ trades.json
         └─ cooldowns.json
```

---

🔑 КЛЮЧЕВЫЕ ИМЕНА / ПАРАМЕТРЫ

Таблицы Supabase:

· candles, funding_rates, open_interest, long_short_ratio, taker_flow, market_context
· features_hourly, price_patterns, events, causal_links
· external_market ← новая
· collect_log, rejected_data, cross_check, anomaly_log

Колонки features_hourly (актуально, 26 штук):
symbol, timestamp, change_pct, range_pct, body_pct, upper_wick_pct, lower_wick_pct, volume_ratio_24h, volatility_24h, volatility_7d, change_4h, change_24h, change_7d, change_1d, change_3d, trend_up, hour_of_day, day_of_week, funding_rate, funding_trend, oi_change_pct, ls_ratio, taker_ratio, next_change_pct, next_direction, computed_at

Cron (Europe/Kaliningrad):

· Collect 0 * * * *
· Simulator 5 * * * *
· Enrich 15 * * * *
· External 20 * * * *
· Detect 30 * * * *
· News 0 6 * * *
· Morning 0 7 * * *
· Evening 0 18 * * *
· Learn 45 1 * * *

Секреты GitHub:
ARGUS_DB_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, GH_PAT, MEXC_API_KEY, MEXC_API_SECRET

---

🧠 КОНТЕКСТ ДЛЯ НОВОГО ЧАТА

Если чат закончится — скопируй в начало нового:

```
Продолжаем ARGUS. Два контура:
1. Books (argus-core) — /ask работает, faiss собран
2. Crypto — Supabase, ML pipeline, симулятор

Состояние (25.09.2026):
- features_hourly: 374 строки, 26 колонок
- ML: LightGBM, edge 0.0 (мало данных)
- Симулятор v8.1: работает
- External market: DXY/SPX/Gold собираются
- Cron: 9 задач в cron-job.org
- ML models/export: готовы

Текущая задача: дать ML набрать 500+ строк,
потом улучшить таргет и добавить features:
dxy, spx, gold, eth_btc, order book, liquidations.
```

---

Всё сохранено. Завтра продолжим с features + external колонки. 🦉