📄 crypto/README.md — готовый файл

Копируй целиком в crypto/README.md. При обрыве чата — скидываешь этот файл новому AI, и он продолжает с того же места.

```markdown
# 🪙 ARGUS-Trader — Модуль рыночных данных

**Версия:** 0.1 (проектирование)
**Статус:** структура готова, код не написан
**Родитель:** ARGUS Core

---

## 🎯 Миссия модуля

Собирать **качественные рыночные данные** BTC и ETH для последующего анализа,
поиска закономерностей и предсказания движения цены.

**Принцип №1:** качество данных важнее всего. Лучше 3 месяца чистого сбора, чем год мусора.

**Принцип №2:** данные не теряются никогда. Fallback, backfill, cross-check, бэкап.

**Принцип №3:** не смешивать источники без причины. Один донор даёт 95% данных,
остальные — только добирают недостающее.

---

## 🏛 Архитектура — 3 уровня источников

```
┌─────────────────────────────────────────────┐
│  УРОВЕНЬ 1: ДОНОР (95% данных)              │
│  Сейчас: OKX                                │
│  Позже:  Binance (нужен VPS в EU/Asia)      │
│  Даёт: OHLCV, funding, OI, LS, taker        │
└─────────────────────────────────────────────┘
                    ↓ если упала метрика
┌─────────────────────────────────────────────┐
│  УРОВЕНЬ 2: FALLBACK (5% данных)            │
│  Bitget → Gate → MEXC → KuCoin              │
│  Каждая метрика независимо                  │
│  Берём ТОЛЬКО недостающее                   │
└─────────────────────────────────────────────┘
                    ↓ сверка, не смешивание
┌─────────────────────────────────────────────┐
│  УРОВЕНЬ 3: АГРЕГАТОР                       │
│  CoinGecko (сверка цены, mcap, доминация)   │
│  CoinMarketCap (дубль, если надо)           │
└─────────────────────────────────────────────┘
```

**Почему OKX основной:** работает из GitHub Actions (США), отдаёт все нужные метрики.

**Почему Binance не сейчас:** 451 Restricted Location — блокирует США. Нужен VPS в Европе.

**Почему CoinGecko:** независимая точка сверки. Если OKX даёт $61 500 а CoinGecko $61 700 — сигнал аномалии.

---

## 📊 Что собираем — 10 метрик + сверка

### 🔵 Группа A: Цена и объём

| # | Метрика | Что даёт | Приоритет 1 | Fallback |
|---|---|---|---|---|
| 1 | OHLCV 1h | Основа анализа | OKX | Bitget, Gate, KuCoin, MEXC |
| 2 | OHLCV 1d | Тренд | OKX | Bitget, Gate, KuCoin, MEXC |

### 🔵 Группа B: Деривативы

| # | Метрика | Что даёт | Приоритет 1 | Fallback |
|---|---|---|---|---|
| 3 | Funding rate | Настроение рынка | OKX | Bitget, Gate, KuCoin |
| 4 | Open Interest | Деньги в позициях | OKX | Bitget, Gate |
| 5 | Long/Short ratio | Кто доминирует | OKX | Bitget, Gate |
| 6 | Taker buy/sell | Агрессия покупателей | OKX | Gate |

### 🔵 Группа C: Контекст рынка (CoinGecko)

| # | Метрика | Что даёт | Источник |
|---|---|---|---|
| 7 | Market Cap | Общая стоимость | CoinGecko |
| 8 | BTC Dominance | Доля BTC | CoinGecko |
| 9 | Total Volume 24h | Общий объём | CoinGecko |
| 10 | USD price check | Сверка цены | CoinGecko |

### 🔵 Группа D: Сверка между биржами

| # | Что | Зачем |
|---|---|---|
| 11 | Cross-check OKX vs CoinGecko | Детект аномалий |
| 12 | Cross-check OKX vs Bitget | Аномалии между биржами |

### ⏳ Позже (не в текущей фазе)

- Liquidations (через WebSocket)
- On-chain метрики (Etherscan)
- Sentiment (Twitter/Reddit)

---

## 📁 Структура папки `crypto/`

```
crypto/
│
├── README.md                    ← ЭТОТ ФАЙЛ (передаётся в новый чат)
├── config.py                    ← Все константы: символы, лимиты, retention
├── schema.sql                   ← Полная схема БД (12 таблиц)
├── db.py                        ← Обёртка psycopg (только соединение)
│
├── collect/                     ← ЭТАП 1: СБОР
│   ├── exchanges.py             ← Клиенты: OKX, Bitget, Gate, KuCoin, MEXC, CoinGecko
│   ├── priority.py              ← Таблица приоритетов метрик
│   ├── validator.py             ← Проверка данных (JSON, ключи, диапазоны, timestamp)
│   ├── pipeline.py              ← Главный сборщик: fallback chain + запись в БД
│   └── cross_check.py           ← Сверка цен между биржами
│
├── enrich/                      ← ЭТАП 2: СЫРЬЁ → ЗНАНИЯ
│   ├── features.py              ← Признаки (change_pct, волатильность, MA, RSI)
│   ├── patterns.py              ← 0/1 графики и n-граммы
│   ├── events.py                ← Детектор событий (движение > N%)
│   └── causal.py                ← Lead indicators (что предшествовало)
│
├── learn/                       ← ЭТАП 3: ML
│   ├── model.py                 ← Train + predict (LightGBM)
│   └── signals.py               ← Сигналы в Telegram
│
├── report/                      ← ЭТАП 4: ОТЧЁТЫ
│   ├── morning.py               ← Утренний отчёт 9:00 UTC
│   └── anomaly.py               ← Алерт при аномалии
│
├── maintenance/                 ← СЛУЖЕБНОЕ
│   ├── backfill.py              ← Дозагрузка пропусков
│   ├── retention.py             ← Удаление сырья > 90 дней
│   └── backup.py                ← CSV-бэкап раз в неделю
│
└── brain/                       ← ЭТАП 5 (позже): ДИРИЖЁР
    └── controller.py            ← Автономный запуск задач
```

**Итого:** 18 файлов + README. Один файл = одна задача.

---

## 🗄 Схема БД (краткая карта)

### RAW (сырьё, retention 90 дней)
- `raw_candles` — OHLCV 1h (BTC, ETH)
- `raw_funding_rates` — funding каждые 8h
- `raw_open_interest` — OI каждый час
- `raw_long_short` — LS ratio
- `raw_taker_flow` — taker buy/sell
- `raw_liquidations` — ликвидации (позже)

### AGGREGATED (навсегда)
- `candles_daily` — 1d свечи
- `market_context` — CoinGecko метрики

### DERIVED (навсегда)
- `features_hourly` — 50+ признаков на каждый час
- `price_patterns` — 0/1 графики
- `events` — зафиксированные движения
- `causal_links` — lead indicators

### ML (навсегда)
- `predictions` — предсказания
- `ml_models` — модели

### AUDIT (служебные)
- `collect_log` — журнал сбора
- `cross_check` — сверка цен
- `anomaly_log` — аномалии
- `retention_log` — журнал удалений

**Полная схема** — в `crypto/schema.sql`.

---

## 🛡 Принципы качества данных

### 1. Идемпотентность (PRIMARY KEY)

Каждая строка имеет уникальный ключ:
- `candles`: `(symbol, timeframe, timestamp)`
- `funding`: `(symbol, timestamp)`
- `oi`: `(symbol, timestamp)`

Если данные повторятся — `ON CONFLICT DO NOTHING`. Дубликатов не будет.

### 2. Валидация перед записью

Каждая запись проверяется:
- Ответ — JSON? (не HTML 503)
- Все ключи есть?
- Цены > 0?
- Изменение < 30% за час? (защита от выбросов)
- Timestamp свежий (не старше 2 часов)?
- Timestamp в UTC (не наивный datetime)?

**Не прошло — в карантин (`rejected_data`), не в основную таблицу.**

### 3. Fallback chain по метрике

Для каждой метрики — свой приоритетный список бирж.
Если OKX упал на funding — идём к Bitget. Если Bitget тоже — к Gate.

### 4. Cross-check между источниками

Раз в час:
- Берём цену с OKX → в `candles`
- Берём цену с CoinGecko → сравнение
- Если разница > 0.5% → `anomaly_log` + алерт в Telegram

**Это не смешивание — это детектор аномалий.**

### 5. Backfill раз в сутки

Каждый день в 03:00:
- Проверяем целостность за прошлые 24 часа
- Ожидаем 24 свечи 1h, 1 funding, 24 OI, 24 LS
- Если < — догружаем через fallback chain

### 6. Retention 90 дней

Раз в месяц:
- Удаляем из `raw_*` записи старше 90 дней
- Агрегаты (`candles_daily`, `features_hourly`) — **не удаляются никогда**

**Что это даёт:** сырьё не пухнет, знания растут медленно.

### 7. Бэкап раз в неделю

Каждое воскресенье:
- Экспорт всех агрегатов в CSV
- Пуш в отдельный репозиторий (или S3)
- **Если Supabase умрёт — данные целы**

### 8. Immutable append-only

Никогда не UPDATE. Только INSERT.
Если нужна правка — новая строка + `superseded_by` колонка.

### 9. Колонка `source` в каждой таблице

Всегда видно, откуда пришла запись. Если биржа деградировала — знаем, какие данные сомнительны.

### 10. Журнал `collect_log`

Каждый запуск сбора: что собрали, сколько, откуда, статус, ошибки.
Без журнала — слепые.

---

## 📐 Правила работы с данными

| Правило | Описание |
|---|---|
| **Одна свеча = одна строка** | Не дублируем между биржами |
| **UTC TIMESTAMPTZ** | Никогда naive datetime |
| **Единая нормализация символов** | BTCUSDT (Binance) = BTC-USDT-SWAP (OKX) = BTC/USDT (Bybit) |
| **Никогда не UPDATE** | Только INSERT |
| **`source` для аудита** | Кто дал данные |
| **Каждый час — сбор** | Cron в GitHub Actions |
| **Раз в сутки — backfill** | Проверка целостности |
| **Раз в месяц — retention** | Удаление старого сырья |
| **Раз в неделю — бэкап** | CSV в отдельный репо |

---

## 📅 Фазы разработки

### ✅ Фаза 0: Разведка (сделано)
- `exchange_probe.py` — проверка доступности бирж
- Результат: OKX ✅, Bitget ✅, Gate ✅, KuCoin ✅
- Binance ❌ (451), Bybit ❌ (403) — геоблок GitHub Actions

### 🔄 Фаза 1: Фундамент сбора (следующая)

**Цель:** данные копятся, ничего не теряется.

Что делаем:
1. `config.py` — константы
2. `schema.sql` — 12 таблиц
3. `db.py` — обёртка psycopg
4. `collect/exchanges.py` — клиенты OKX, Bitget, Gate, KuCoin, CoinGecko
5. `collect/priority.py` — таблица приоритетов
6. `collect/validator.py` — валидация
7. `collect/pipeline.py` — fallback + запись
8. `collect/cross_check.py` — сверка
9. GitHub Actions: `crypto_collect.yml` (каждый час)

**Результат:** BTC + ETH, 10 метрик, качественный сбор.

### 📍 Фаза 2: Сырьё → Знания (1 неделя)
- `enrich/features.py` — 50+ признаков
- `enrich/patterns.py` — 0/1 графики
- `enrich/events.py` — детектор событий
- `enrich/causal.py` — lead indicators

### 📍 Фаза 3: ML (2-3 недели)
- `learn/model.py` — LightGBM
- `learn/signals.py` — алерты

### 📍 Фаза 4: Отчёты (1 неделя)
- `report/morning.py` — утренний отчёт 9:00 UTC
- `report/anomaly.py` — алерты аномалий

### 📍 Фаза 5: Дирижёр (позже)
- `brain/controller.py` — автономия

### 📍 Фаза 6: VPS + Binance (позже)
- Поднять VPS в Европе
- Добавить Binance как основной донор
- GitHub Actions + VPS = 2 источника сбора

---

## 🎯 Что даёт эта архитектура

| Аспект | Значение |
|---|---|
| **Надёжность** | Fallback chain из 4-5 бирж |
| **Качество** | Валидация + cross-check + карантин |
| **Целостность** | Backfill + PRIMARY KEY |
| **Экономия** | ~2 МБ/мес при 1h сборе BTC+ETH |
| **Масштаб** | Supabase Free хватит на 30+ лет |
| **Восстановление** | CSV-бэкап раз в неделю |
| **Аудит** | `source`, `collect_log`, `rejected_data` |

---

## 🧠 Промпт для нового чата

**Если этот чат закончился — скопируй текст ниже и вставь в новый диалог.**

---

```
Привет! Продолжаем разработку ARGUS-Trader — модуль рыночных данных 
для проекта ARGUS.

Контекст:
- Проект ARGUS — автономный ИИ-агент (см. GUIDE.md в корне репо)
- Модуль crypto/ — отдельная папка для трейдинг-данных
- Собираем данные BTC и ETH для предсказания движения цены

Что уже есть:
- Разведчик бирж (probe/exchange_probe.py) — проверил доступность
- Работают: OKX (основной), Bitget, Gate, KuCoin, MEXC, CoinGecko
- Не работают из GitHub Actions: Binance (451), Bybit (403) — геоблок
- Позже поднимем VPS для Binance

Архитектура (3 уровня):
1. OKX — донор 95% данных
2. Bitget → Gate → MEXC → KuCoin — fallback по метрикам
3. CoinGecko — сверка цен

Принципы качества:
- Идемпотентность через PRIMARY KEY
- Валидация перед записью (JSON, ключи, диапазоны, UTC timestamp)
- Fallback chain по метрике (не по бирже!)
- Cross-check между источниками
- Backfill раз в сутки
- Retention сырья 90 дней
- Бэкап CSV раз в неделю
- Immutable append-only
- Колонка source в каждой таблице

Структура crypto/ — в README.md модуля (прилагаю).

Моя текущая задача: [ВСТАВЬ СЮДА ЧТО ДЕЛАЕМ].

Строго соблюдай правила из GUIDE.md и этого README.md.
Не упрощай архитектуру. Не смешивай источники без причины.
```

---

## 📌 Ключевые файлы для контекста (если новый чат)

1. **Этот файл** — `crypto/README.md`
2. **GUIDE.md** в корне репо — общие правила ARGUS
3. **`scripts/requirements.txt`** — зависимости (torch, psycopg добавим)
4. **`data/exchange_probe.json`** — отчёт разведчика

---

## 🚦 Статус: готовность к Фазе 1

**Что согласовано:**
- ✅ Источники: OKX (донор) + Bitget/Gate/MEXC/KuCoin (fallback) + CoinGecko (сверка)
- ✅ Метрики: OHLCV 1h/1d, funding, OI, LS, taker + mcap/dominance
- ✅ Схема БД (12 таблиц)
- ✅ Принципы качества (10 правил)
- ✅ Структура папок (18 файлов)
- ✅ План фаз (0-6)

**Что делаем следующим:**
1. `crypto/config.py` — константы
2. `crypto/schema.sql` — 12 таблиц
3. `crypto/db.py` — обёртка psycopg
4. `crypto/collect/exchanges.py` — 6 клиентов
5. `crypto/collect/priority.py` — таблица
6. `crypto/collect/validator.py` — валидация
7. `crypto/collect/pipeline.py` — сбор
8. `.github/workflows/crypto_collect.yml` — 🦉