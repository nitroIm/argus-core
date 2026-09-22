# 🪙 ARGUS-TRADER — GUIDE v4

Модуль рыночных данных проекта ARGUS
Обновлено: 2026-09-22
Статус: v4 (отчёты + графики + аудио + пульт)

─────────────────────
1. МИССИЯ
─────────────────────

Собирать качественные рыночные
данные BTC/ETH для поиска
закономерностей и предсказания
движения цены.

Принципы:
• Качество > скорость
• Данные не теряются никогда
  (fallback + backfill +
   cross-check + backup)
• Один донор даёт 95%
• Дубликаты → PRIMARY KEY
• Битые данные → карантин

─────────────────────
2. АРХИТЕКТУРА
─────────────────────

GitHub Actions (ЛОГИКА)
  cron: collect / enrich /
        detect / report
  секреты: ARGUS_DB_URL,
           TELEGRAM_*, GH_PAT
        ↓ ↑
Supabase (ДАННЫЕ)
  18 таблиц
  Session Pooler (IPv4!)
  eu-west-1 (Ирландия)
        ↓ ↑
VPS Wispbyte (БОТ)
  bot_host.py (aiogram)
  слушает Telegram 24/7
        ↓ ↑
Telegram (ИНТЕРФЕЙС)

Разделение:
  Логика → Actions
  Данные → Supabase
  Пульт  → VPS
  Юзер   → Telegram

─────────────────────
3. ИСТОЧНИКИ — 3 УРОВНЯ
─────────────────────

УРОВЕНЬ 1 — ДОНОР (95%)
  OKX
  работает из GitHub Actions
  отдаёт всё: OHLCV, funding,
  OI, LS, taker

УРОВЕНЬ 2 — FALLBACK (5%)
  Bitget → Gate → KuCoin → MEXC
  каждая метрика независимо
  берём ТОЛЬКО недостающее

УРОВЕНЬ 3 — СВЕРКА
  CoinGecko
  cross-check при расхождении
  > 0.5%

Почему не Binance:
  451 Restricted Location (США)
  Позже: VPS в EU → вернём.

Почему fallback по метрике:
  OKX упал на funding → Bitget
  OKX упал на OI → Bitget

─────────────────────
4. МЕТРИКИ — 6 + КОНТЕКСТ
─────────────────────

# | Метрика          | Таблица
--|------------------|--------------
1 | OHLCV 1h         | candles
2 | OHLCV 1d         | candles
3 | Funding rate 8h  | funding_rates
4 | Open Interest    | open_interest
5 | Long/Short ratio | long_short
6 | Taker flow       | taker_flow
+ | Market context   | market_context

Монеты: BTC, ETH (пока).

Позже: liquidations (WS),
on-chain, sentiment.

─────────────────────
5. СХЕМА БД — 18 ТАБЛИЦ
─────────────────────

RAW (retention 90 дней):
  candles
  funding_rates
  open_interest
  long_short_ratio
  taker_flow

CONTEXT:
  market_context

DERIVED (навсегда):
  features_hourly
  price_patterns
  events
  causal_links

ML:
  predictions
  ml_models

AUDIT:
  collect_log
  rejected_data
  cross_check
  anomaly_log
  retention_log

Схема: crypto/schema.sql

─────────────────────
6. СТРУКТУРА crypto/
─────────────────────

crypto/
├── README.md
├── config.py
├── schema.sql
├── db.py
├── requirements.txt
│
├── collect/
│   ├── exchanges.py
│   ├── priority.py
│   ├── validator.py
│   ├── pipeline.py
│   └── cross_check.py
│
├── enrich/
│   ├── runner.py
│   ├── features.py
│   ├── patterns.py
│   ├── levels.py
│   ├── events.py
│   ├── causal.py
│   └── correlate.py
│
├── learn/
│   ├── model.py
│   └── signals.py
│
├── report/
│   ├── charts.py
│   ├── morning.py
│   └── weekly.py
│
├── maintenance/
│   ├── backfill.py
│   ├── retention.py
│   └── backup.py
│
└── brain/
    └── controller.py

─────────────────────
7. FEATURES — 13+
─────────────────────

change_pct        % движения
range_pct         волатильность
body_pct          тело / range
upper_wick_pct    верх. хвост
lower_wick_pct    ниж. хвост
volume_ratio_24h  объём / средний
volatility_24h    std за 24ч
volatility_7d     std за 7д
change_4h         накопленное
change_24h        накопленное
change_7d         накопленное
+ MA / RSI / др.

Таблица: features_hourly

─────────────────────
8. PATTERNS — 0/1 + MARKOV
─────────────────────

Бинарный график:
  1 = close > open (рост)
  0 = падение

Markov:
  P(1|1)  продолжение роста
  P(0|1)  разворот вниз
  P(1|0)  отскок вверх
  P(0|0)  продолжение падения

Чтение:
  P(1|0) > 0.58 → reversion
  P(1|0) < 0.42 → momentum

N-граммы:
  1111 / 1011 / 0011
  что идёт СЛЕДУЮЩИМ

Первые наблюдения:
  BTC: 1111 → ↑ 64% (N=14)
  ETH: 1111 → ↑ 67% (N=12)
  BTC: P(1|0)=0.60 → отскок
  ETH: P(1|0)=0.56

⚠️ N маленькие. Через месяц 100+.

Таблица: price_patterns

─────────────────────
9. LEVELS — АДАПТИВНЫЕ
─────────────────────

pick_step_size(price) — от цены:
  $85,000 → major 10k, mid 5k
  $2,700  → major 100, mid 50
  $850    → major 100, mid 50
  $0.85   → major 0.1, mid 0.05

Покрытие: ±70% от цены.

Support/Resistance:
  локальные экстремумы
  с 2+ касаниями

Volume profile: топ-5 уровней

─────────────────────
10. EVENTS — 7 ТИПОВ
─────────────────────

rise_1h        +1.5% за час
fall_1h        -1.5% за час
rise_4h        +3% за 4ч
fall_4h        -3% за 4ч
new_high_7d    макс за 7д
new_low_7d     мин за 7д
volume_spike   объём > 3× от 24ч

Таблица: events

─────────────────────
11. CAUSAL — LEAD INDICATORS
─────────────────────

Для каждого события собирает:
  features за 1ч / 4ч / 24ч ДО
  funding rate
  OI change
  LS ratio
  ближайший уровень
  паттерн 0/1

Таблица: causal_links

─────────────────────
12. CORRELATE — ПРАВИЛА
─────────────────────

Ищет «если X → Y».

Типы:
  простые:  funding<-0.005 → UP
  двойные:  OI>+2% AND LS>1.5
  тройные:  funding<0 AND
            LS<1.0 AND OI↓

Порог: ≥ 3 сэмпла, conf ≥ 55%

Первые наблюдения:
  BTC: OI>+2% → UP 80% (N=5)
  BTC: funding>0.005 → 67% (N=12)
  ETH: funding>0.005 → 60% (N=5)

⚠️ Это наблюдения. Ждём N=50+.

─────────────────────
13. ОТЧЁТЫ — 3 ШТУКИ
─────────────────────

1) УТРЕННИЙ РЫНОК
   07:00 UTC (09:00 Калининград)
   цены, уровни, паттерны,
   сценарий, новости
   + альбом 6 графиков

2) НЕДЕЛЬНЫЙ
   Вс 05:00 UTC
   статистика, БД, события,
   корреляции, паттерны,
   уровни, аномалии,
   сентимент + графики

3) ВЕЧЕРНИЙ ТЕХОТЧЁТ
   18:00 UTC (в планах)
   workflows OK/упали,
   что добавилось

Правило: один отчёт
вместо 100 алертов.

─────────────────────
14. ГРАФИКИ — 3 ТИПА
─────────────────────

1) СВЕЧИ (BTC / ETH)
   зел./красн. + support/
   resist пунктиром
   + объём внизу

2) ПАТТЕРН 0/1
   столбики: 1↑, 0↓
   50 часов истории
   подпись: up/down,
   max streak, топ n-gram

3) MARKOV МАТРИЦА
   2×2 from DOWN/UP → UP/DOWN
   значения 0.0 — 1.0
   подпись: P(1|0), P(1|1)
   + интерпретация

Итого: 6 графиков в альбоме.

Файл: crypto/report/charts.py

─────────────────────
15. АУДИОКНИГИ
─────────────────────

Файл: scripts/personal_audio.py

PDF → PyPDF2 → gTTS → mp3
режет на сегменты по 45 мин
отправляет в Telegram
mp3 удаляется после отправки
PDF остаётся в personal_books/

VPS требования:
  pip install gTTS PyPDF2
  apt install ffmpeg

Команды:
  /audio          список
  /audio имя      сделать RU
  /audio имя --en из EN

Через Actions (personal_audio.yml)
VPS не нагружается.

─────────────────────
16. ПУЛЬТ — bot_host.py
─────────────────────

Меню:
  📚 Мои книги   🎓 Train
  💬 Спросить    🪙 Крипто
  📊 Графики     📈 Статус
  ⚙️ Настройки

Разделы:
  📚 Мои книги → список
     + 🎧 Аудио + 🗑 Удалить
  🎓 Train    → Очередь /
     Обучено / Запуск
  💬 Спросить  → инструкция /ask
  🪙 Крипто   → Collect /
     Enrich / Detect / Отчёт
  📊 Графики  → 6 кнопок
  📈 Статус   → снимок систем
  ⚙️ Настройки → заглушка

Кнопка «Назад» везде.

Техника:
  read_json()   GitHub API
  read_binary() PNG
  list_dir()    список
  delete_file() удаление

─────────────────────
17. WORKFLOWS + CRON
─────────────────────

Файл                    | Cron
------------------------|-----------
crypto_collect.yml      | 0 * * * *
crypto_enrich.yml       | 15 * * * *
crypto_detect.yml       | 30 * * * *
crypto_reporter.yml     | 0 5 * * 0
morning_market.yml      | 0 7 * * *
personal_audio.yml      | dispatch

Порядок каждый час:
  :00  collect
  :15  enrich
  :30  detect

Отчёты:
  07:00 UTC (09:00 КЛГ) утро
  05:00 UTC вс          неделя

⚠️ Cron GitHub задерживается
   до 15 мин. На VPS — точность.

─────────────────────
18. КАЧЕСТВО ДАННЫХ
─────────────────────

1. Идемпотентность
   PRIMARY KEY:
     candles: (sym, tf, ts)
     funding: (sym, ts)
   ON CONFLICT DO NOTHING

2. Валидация перед записью:
   JSON? ключи? цены>0?
   изменение < 30%/час?
   ts свежий (< 2ч)?
   ts в UTC?
   Не прошло → rejected_data

3. Fallback ПО МЕТРИКЕ
   (не по бирже!)

4. Cross-check источников
   > 0.5% → anomaly_log

5. Backfill раз в сутки
   проверка за 24ч

6. Retention 90 дней
   только raw_*
   агрегаты — НАВСЕГДА

7. Бэкап CSV раз в неделю
   в отдельный репо

8. Immutable append-only
   только INSERT, без UPDATE

9. Колонка source
   в каждой таблице

10. Журнал collect_log
    что, сколько, откуда, статус

─────────────────────
19. КРИТИЧНЫЕ ПРАВИЛА
─────────────────────

1. НЕ удалять таблицы
2. НЕ менять PRIMARY KEY
3. Смена донора через
   priority.py, не хардкод
4. Retention сырья 90 дней
5. Только INSERT, без UPDATE
6. Всё UTC (TIMESTAMPTZ)
7. Секреты — GitHub Secrets
8. Бот на VPS, не в Actions
9. crypto/data/*.json —
   оперативные (runner.py)
10. enrich/collect workflows
    нужен contents: write
11. Один workflow — один
    процесс (через runner.py)
12. Строки кода ≤ 55 символов
13. Логи на английском

─────────────────────
20. СЕКРЕТЫ GITHUB
─────────────────────

ARGUS_DB_URL        Supabase
                    (Session Pooler)
TELEGRAM_BOT_TOKEN  @ARGUS
TELEGRAM_CHAT_ID    чат
GH_PAT              Personal Token
OPENROUTER_API_KEY  на будущее

─────────────────────
21. ЧАСТЫЕ ПРОБЛЕМЫ
─────────────────────

Симптом           → Фикс
------------------|-------------------
Pipeline 8+ мин   → --mode=incremental
Network unreach   → Session Pooler
CoinGecko 429     → кэш 10 мин
multiple PK       → один PK
Данные не идут    → запустить вручную
Ложный «успех»    → проверять added
JSON не коммит.   → contents: write
Утренний пустой   → enrich коммитит?
Строки рвутся     → ≤ 55 символов

─────────────────────
22. ЛИМИТЫ GITHUB ACTIONS
─────────────────────

Приватный репо: 2000 мин/мес
Наш прогон: 4м × 720 = 2880 ❌

Варианты:
A. Публичный репо → безлимит
B. Cron 4ч → 720 мин ✅
C. Докупать минуты

Сейчас: репо публичные → ок.

─────────────────────
23. ДОРОЖНАЯ КАРТА
─────────────────────

✅ Фаза 0: разведка бирж
✅ Фаза 1: фундамент сбора
✅ Фаза 2: признаки, паттерны,
          события, causal
✅ Фаза 3: утренний + графики
✅ Фаза 4: недельный v5
✅ Аудиокниги, пульт v3.1

📍 Фаза 5 (текущая):
   • Тест аудиокниг
   • Вечерний техотчёт
   • Доработка пульта
   • anomaly.py (манипуляции)

📍 Фаза 6: ML
   • LightGBM (500+ свечей)
   • Similarity search
   • Режимы рынка (KMeans)

📍 Фаза 7: автономия
   • brain/controller.py

📍 Фаза 8: VPS + Binance
   • 2-й донор
   • точный cron

─────────────────────
24. ПРОМПТ ДЛЯ НОВОГО ЧАТА
─────────────────────

---
Привет! Продолжаем ARGUS-Trader —
модуль рыночных данных ARGUS.

Контекст:
• ARGUS — автономный ИИ-агент
• crypto/ — данные для трейдинга
• BTC + ETH, поиск закономерностей

Архитектура:
1. OKX — донор 95%
2. Bitget → Gate → KuCoin → MEXC
   — fallback
3. CoinGecko — сверка

Работают из GitHub:
  OKX, Bitget, Gate, KuCoin,
  MEXC, CoinGecko
Не работают:
  Binance (451), Bybit (403)

Принципы:
• PRIMARY KEY
• Валидация (JSON, UTC)
• Fallback ПО МЕТРИКЕ
• Cross-check
• Backfill раз в сутки
• Retention 90 дней
• CSV бэкап раз в неделю
• Append-only
• Колонка source
• Журнал collect_log

БД: 18 таблиц
 RAW: candles, funding_rates,
      open_interest,
      long_short_ratio,
      taker_flow
 CONTEXT: market_context
 DERIVED: features_hourly,
      price_patterns, events,
      causal_links
 ML: predictions, ml_models
 AUDIT: collect_log,
      rejected_data, cross_check,
      anomaly_log, retention_log

Структура crypto/ — в README.

ПРАВИЛА (см. GUIDE §19):
• Не удалять таблицы
• Не менять PK
• Только INSERT
• UTC везде
• Строки ≤ 55
• Файлы ЦЕЛИКОМ

Текущая задача: [ВСТАВЬ]

Не упрощай архитектуру.
Не смешивай источники.
---

─────────────────────
Конец ARGUS-Trader GUIDE v4
─────────────────────