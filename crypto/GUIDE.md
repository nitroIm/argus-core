
# 🪙 ARGUS-TRADER — GUIDE v4

Обновлено: 2026-09-22
Статус: v4 (отчёты + графики +
аудио + пульт)

━━━━━━━━━━━━━━━━━━━━
1. МИССИЯ
━━━━━━━━━━━━━━━━━━━━

Сбор качественных рыночных данных
BTC/ETH → поиск закономерностей
→ предсказание цены.

**Принципы:**
• Качество > скорость
• Данные не теряются никогда
• Один донор = 95%
• Дубликаты → PRIMARY KEY
• Битые данные → карантин

━━━━━━━━━━━━━━━━━━━━
2. АРХИТЕКТУРА
━━━━━━━━━━━━━━━━━━━━

**GitHub Actions** — логика
  cron: collect/enrich/
        detect/report

**Supabase** — данные
  18 таблиц
  Session Pooler (IPv4!)
  eu-west-1

**VPS Wispbyte** — бот
  bot_host.py, 24/7

**Telegram** — интерфейс

Связка:
Actions ↔ Supabase ↔ VPS ↔ TG

━━━━━━━━━━━━━━━━━━━━
3. ИСТОЧНИКИ — 3 УРОВНЯ
━━━━━━━━━━━━━━━━━━━━

**1) ДОНОР (95%)**
OKX — работает из GitHub,
отдаёт всё.

**2) FALLBACK (5%)**
Bitget → Gate → KuCoin → MEXC
по каждой метрике отдельно.

**3) СВЕРКА**
CoinGecko. Расхождение >0.5%
→ anomaly_log.

**Не работают:**
Binance (451), Bybit (403).
Позже VPS в EU → вернём.

━━━━━━━━━━━━━━━━━━━━
4. МЕТРИКИ
━━━━━━━━━━━━━━━━━━━━

| # | Метрика        | Таблица
|---|----------------|----------
| 1 | OHLCV 1h       | candles
| 2 | OHLCV 1d       | candles
| 3 | Funding 8h     | funding
| 4 | Open Interest  | open_int
| 5 | Long/Short     | long_short
| 6 | Taker flow     | taker
| + | Market context | mkt_ctx

Монеты: BTC, ETH.
Позже: liquidations, on-chain.

━━━━━━━━━━━━━━━━━━━━
5. СХЕМА БД — 18 ТАБЛИЦ
━━━━━━━━━━━━━━━━━━━━

**RAW (90 дней):**
candles, funding_rates,
open_interest, long_short_ratio,
taker_flow

**CONTEXT:**
market_context

**DERIVED (навсегда):**
features_hourly, price_patterns,
events, causal_links

**ML:**
predictions, ml_models

**AUDIT:**
collect_log, rejected_data,
cross_check, anomaly_log,
retention_log

Схема: crypto/schema.sql

━━━━━━━━━━━━━━━━━━━━
6. СТРУКТУРА crypto/
━━━━━━━━━━━━━━━━━━━━

```

crypto/
├─ README.md
├─ config.py
├─ schema.sql
├─ db.py
├─ requirements.txt
│
├─ collect/
│  ├─ exchanges.py
│  ├─ priority.py
│  ├─ validator.py
│  ├─ pipeline.py
│  └─ cross_check.py
│
├─ enrich/
│  ├─ runner.py
│  ├─ features.py
│  ├─ patterns.py
│  ├─ levels.py
│  ├─ events.py
│  ├─ causal.py
│  └─ correlate.py
│
├─ learn/
│  ├─ model.py
│  └─ signals.py
│
├─ report/
│  ├─ charts.py
│  ├─ morning.py
│  └─ weekly.py
│
├─ maintenance/
│  ├─ backfill.py
│  ├─ retention.py
│  └─ backup.py
│
└─ brain/
└─ controller.py

```

━━━━━━━━━━━━━━━━━━━━
7. FEATURES — 13+
━━━━━━━━━━━━━━━━━━━━

change_pct         % движения
range_pct          волатильность
body_pct           тело/range
upper_wick_pct     верх. хвост
lower_wick_pct     ниж. хвост
volume_ratio_24h   объём/сред.
volatility_24h     std 24ч
volatility_7d      std 7д
change_4h          накопл.
change_24h         накопл.
change_7d          накопл.
+ MA / RSI / др.

Таблица: features_hourly

━━━━━━━━━━━━━━━━━━━━
8. PATTERNS — 0/1 + MARKOV
━━━━━━━━━━━━━━━━━━━━

**Бинарный график:**
1 = close > open
0 = падение

**Markov:**
P(1|1) продолжение роста
P(0|1) разворот вниз
P(1|0) отскок вверх
P(0|0) продолжение падения

**Чтение:**
P(1|0) >0.58 → reversion
P(1|0) <0.42 → momentum

**N-граммы:** 1111 / 1011 / 0011
что идёт СЛЕДУЮЩИМ.

**Наблюдения (101 свеча):**
BTC: 1111 → ↑64% (N=14)
ETH: 1111 → ↑67% (N=12)
BTC: P(1|0)=0.60 → отскок
ETH: P(1|0)=0.56

⚠️ N маленькие. Через месяц 100+.

Таблица: price_patterns

━━━━━━━━━━━━━━━━━━━━
9. LEVELS
━━━━━━━━━━━━━━━━━━━━

pick_step_size(price):
$85k → major 10k, mid 5k
$2.7k → major 100, mid 50
$850 → major 100, mid 50
$0.85 → major .1, mid .05

Покрытие: ±70% от цены.
S/R — лок. экстремумы, 2+ касания.
Volume profile — топ-5.

━━━━━━━━━━━━━━━━━━━━
10. EVENTS — 7 ТИПОВ
━━━━━━━━━━━━━━━━━━━━

rise_1h        +1.5% / час
fall_1h        -1.5% / час
rise_4h        +3% / 4ч
fall_4h        -3% / 4ч
new_high_7d    макс 7д
new_low_7d     мин 7д
volume_spike   >3× от 24ч

Таблица: events

━━━━━━━━━━━━━━━━━━━━
11. CAUSAL — LEAD
━━━━━━━━━━━━━━━━━━━━

Для каждого события:
• features 1ч/4ч/24ч ДО
• funding rate
• OI change
• LS ratio
• ближайший уровень
• паттерн 0/1

Таблица: causal_links

━━━━━━━━━━━━━━━━━━━━
12. CORRELATE — ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━

Ищет «если X → Y».

**Типы:**
funding<-0.005 → UP
OI>+2% AND LS>1.5
funding<0 AND LS<1.0 AND OI↓

Порог: ≥3 сэмпла, conf ≥55%.

**Наблюдения:**
BTC OI>+2% → 80% (N=5)
BTC funding>0 → 67% (N=12)
ETH funding>0 → 60% (N=5)

⚠️ Наблюдения, не сигналы.
Ждём N=50+.

━━━━━━━━━━━━━━━━━━━━
13. ОТЧЁТЫ — 3 ШТУКИ
━━━━━━━━━━━━━━━━━━━━

**1) Утренний рынок**
07:00 UTC = 09:00 КЛГ
цены, уровни, паттерны,
сценарий, новости
+ альбом 6 графиков

**2) Недельный**
Вс 05:00 UTC
статистика, БД, события,
корреляции, уровни,
аномалии + графики

**3) Вечерний техотчёт**
18:00 UTC (в планах)
workflows OK/упали

Правило: один отчёт,
не 100 алертов.

━━━━━━━━━━━━━━━━━━━━
14. ГРАФИКИ — 3 ТИПА
━━━━━━━━━━━━━━━━━━━━

**1) Свечи** (BTC/ETH)
зел./красн. + S/R + объём

**2) Паттерн 0/1**
столбики 1↑/0↓, 50 часов
up/down, streak, n-gram

**3) Markov 2×2**
from DOWN/UP → UP/DOWN
0.0—1.0 + P(1|0), P(1|1)

Итого 6 графиков в альбоме.
Файл: crypto/report/charts.py

━━━━━━━━━━━━━━━━━━━━
15. АУДИОКНИГИ
━━━━━━━━━━━━━━━━━━━━

Файл: scripts/personal_audio.py

PDF → PyPDF2 → gTTS → mp3
сегменты по 45 мин
отправка в TG
mp3 удаляется, PDF остаётся

**VPS:**
pip install gTTS PyPDF2
apt install ffmpeg

**Команды:**
/audio         список
/audio имя     RU
/audio имя --en из EN

Через Actions, VPS не грузится.

━━━━━━━━━━━━━━━━━━━━
16. ПУЛЬТ — bot_host.py
━━━━━━━━━━━━━━━━━━━━

**Меню:**
📚 Мои книги   🎓 Train
💬 Спросить    🪙 Крипто
📊 Графики     📈 Статус
⚙️ Настройки

**Разделы:**
📚 Книги → список + 🎧 + 🗑
🎓 Train → очередь/обучено/пуск
💬 Спросить → инструкция /ask
🪙 Крипто → collect/enrich/
            detect/отчёт
📊 Графики → 6 кнопок
📈 Статус → снимок
⚙️ Настройки → заглушка

«Назад» везде.

**Техника:**
read_json()   GitHub API
read_binary() PNG
list_dir()    список
delete_file() удаление

━━━━━━━━━━━━━━━━━━━━
17. WORKFLOWS + CRON
━━━━━━━━━━━━━━━━━━━━

| Файл                | Cron
|---------------------|---------
| crypto_collect.yml  | 0 * * * *
| crypto_enrich.yml   | 15 * * * *
| crypto_detect.yml   | 30 * * * *
| crypto_reporter.yml | 0 5 * * 0
| morning_market.yml  | 0 7 * * *
| personal_audio.yml  | dispatch

Порядок каждый час:
:00 collect
:15 enrich
:30 detect

Отчёты:
07:00 UTC (09:00 КЛГ) утро
05:00 UTC вс          неделя

⚠️ Cron GH задерживается
   до 15 мин. На VPS — точно.

━━━━━━━━━━━━━━━━━━━━
18. КАЧЕСТВО ДАННЫХ
━━━━━━━━━━━━━━━━━━━━

**1) Идемпотентность**
candles: (sym,tf,ts)
funding: (sym,ts)
ON CONFLICT DO NOTHING

**2) Валидация**
JSON? ключи? цены>0?
изм. <30%/час?
ts свежий (<2ч)? UTC?
Не прошло → rejected_data

**3) Fallback ПО МЕТРИКЕ**

**4) Cross-check** >0.5% → alert

**5) Backfill** раз в сутки

**6) Retention** 90 дней
только raw_*,
агрегаты — навсегда

**7) Бэкап CSV** раз в неделю

**8) Append-only** только INSERT

**9) Колонка source**

**10) Журнал collect_log**

━━━━━━━━━━━━━━━━━━━━
19. КРИТИЧНЫЕ ПРАВИЛА
━━━━━━━━━━━━━━━━━━━━

1. НЕ удалять таблицы
2. НЕ менять PK
3. Смена донора через
   priority.py, не хардкод
4. Retention 90 дней
5. Только INSERT
6. Всё UTC (TIMESTAMPTZ)
7. Секреты — GH Secrets
8. Бот на VPS, не в Actions
9. crypto/data/*.json —
   оперативные
10. enrich/collect — нужен
    contents: write
11. Один wf — один процесс
12. Строки кода ≤55
13. Логи на английском

━━━━━━━━━━━━━━━━━━━━
20. СЕКРЕТЫ GITHUB
━━━━━━━━━━━━━━━━━━━━

ARGUS_DB_URL       Supabase
TELEGRAM_BOT_TOKEN @ARGUS
TELEGRAM_CHAT_ID   чат
GH_PAT             PAT
OPENROUTER_API_KEY будущее

━━━━━━━━━━━━━━━━━━━━
21. ЧАСТЫЕ ПРОБЛЕМЫ
━━━━━━━━━━━━━━━━━━━━

| Симптом          | Фикс
|------------------|----------------
| Pipeline 8+ мин  | incremental
| Network unreach  | Session Pooler
| CoinGecko 429    | кэш 10 мин
| multiple PK      | один PK
| Данные не идут   | запуск вручную
| Ложный «успех»   | проверять added
| JSON не коммит.  | contents: write
| Утро пустое      | enrich коммитит?
| Строки рвутся    | ≤55 символов

━━━━━━━━━━━━━━━━━━━━
22. ЛИМИТЫ ACTIONS
━━━━━━━━━━━━━━━━━━━━

Приватный: 2000 мин/мес
Наш: 4м×720 = 2880 ❌

A. Публичный → безлимит
B. Cron 4ч → 720 ✅
C. Докупать минуты

Сейчас репо публичные → ок.

━━━━━━━━━━━━━━━━━━━━
23. ДОРОЖНАЯ КАРТА
━━━━━━━━━━━━━━━━━━━━

✅ Фаза 0: разведка
✅ Фаза 1: сбор
✅ Фаза 2: признаки+паттерны
✅ Фаза 3: утренний+графики
✅ Фаза 4: недельный
✅ Аудио, пульт

📍 Фаза 5 (текущая)
  • Тест аудио
  • Вечерний техотчёт
  • Доработка пульта
  • anomaly.py

📍 Фаза 6: ML
  • LightGBM (500+ свечей)
  • Similarity search
  • KMeans режимы

📍 Фаза 7: автономия
  • brain/controller

📍 Фаза 8: VPS+Binance

━━━━━━━━━━━━━━━━━━━━
24. ПРОМПТ ДЛЯ НОВОГО ЧАТА
━━━━━━━━━━━━━━━━━━━━

---
Привет! Продолжаем ARGUS-Trader.

**Контекст:**
• ARGUS — автономный ИИ-агент
• crypto/ — данные трейдинга
• BTC+ETH, поиск закономерностей

**Источники:**
1. OKX — 95%
2. Bitget→Gate→KuCoin→MEXC
3. CoinGecko — сверка

**Не работают:**
Binance (451), Bybit (403)

**Принципы:**
PRIMARY KEY, валидация,
fallback ПО МЕТРИКЕ,
cross-check, backfill,
retention 90д, CSV-бэкап,
append-only, source,
collect_log

**БД 18 таблиц:**
RAW: candles, funding_rates,
open_interest, long_short,
taker_flow
CONTEXT: market_context
DERIVED: features_hourly,
price_patterns, events,
causal_links
ML: predictions, ml_models
AUDIT: collect_log,
rejected_data, cross_check,
anomaly_log, retention_log

**ПРАВИЛА (GUIDE §19):**
не удалять таблицы,
не менять PK,
только INSERT,
UTC везде,
строки ≤55,
файлы ЦЕЛИКОМ.

**Текущая задача:** [ВСТАВЬ]

Не упрощай архитектуру.
Не смешивай источники.
---

━━━━━━━━━━━━━━━━━━━━
Конец ARGUS-Trader GUIDE v4
━━━━━━━━━━━━━━━━━━━━
## 🔧 25. ЖУРНАЛ СЕССИИ — ARGUS-Trader
_Дата: 2026-09-23_

Что делали, что починили, где мы сейчас.

────────────────────
ЧТО СДЕЛАНО
────────────────────

**Crypto — сбор и enrich:**

- Сбор идёт по cron-job.org:
  - `:00` Collect
  - `:15` Enrich
  - `:30` Detect
- Убран `schedule:` из YAML — только внешний
  cron (GitHub внутренний тормозил).
- Общая очередь `argus-crypto-pipeline`
  для всех трёх workflow. Одновременно
  выполняется только один.
- `git pull --rebase -X theirs` — авто-
  разрешение конфликтов в JSON.
- Проверены 7 файлов enrich:
  `causal`, `correlate`, `events`,
  `features`, `levels`, `patterns`,
  `runner` — все рабочие.

**Supabase:**

- `funding_rate` в `features_hourly`
  заполняется (было NULL).
- `features_hourly`: 270 строк,
  уникальных пар 270 → дублей 0.
- Схема БД проверена, актуальные
  имена колонок — ниже.

────────────────────
ЧТО ИСПРАВЛЕНО
────────────────────

**1. `features.py` — колонка `rate`**

В таблице `funding_rates` колонка
называется **`rate`** (не `funding_rate`).
Строка в `fetch_funding`:
`SELECT timestamp, rate FROM funding_rates`

**2. `features.py` — ON CONFLICT**

Было `DO NOTHING` → строки не
обновлялись, `funding_rate` оставался
NULL. Стало `DO UPDATE SET ...` —
существующие строки обновляются.

**3. Двойные триггеры**

Были и `schedule:` в YAML, и
cron-job.org. Enrich/Detect срабатывали
2× за час, Collect — 1×. Убрали
`schedule:` — теперь только внешний cron.

**4. Concurrency**

Collect/Enrich/Detect конфликтовали
на коммите JSON. Поставили общую
группу `argus-crypto-pipeline` + `-X theirs`.

────────────────────
СХЕМА БД (АКТУАЛЬНАЯ)
────────────────────

**`funding_rates`:**
`symbol, timestamp, rate, source,
inserted_at`

**`features_hourly`:**
`symbol, timestamp,
change_pct, range_pct, body_pct,
upper_wick_pct, lower_wick_pct,
volume_ratio_24h,
volatility_24h, volatility_7d,
change_4h, change_24h, change_7d,
funding_rate, funding_trend,
oi_change_pct, ls_ratio, taker_ratio,
next_change_pct, next_direction,
computed_at`

**PK `features_hourly`:**
`(symbol, timestamp)`

**Единицы funding:**
- `funding_rates.rate` — сырое (0.0000536)
- `features_hourly.funding_rate` — %
  (0.005360870)
- `causal_links.funding_rate` — сырое

Это не баг: отчёты читают %
из features, правила — сырое из causal.
При ML надо будет унифицировать.

────────────────────
ИНФРАСТРУКТУРА
────────────────────

**GitHub Actions:**
- `crypto_collect.yml` → `pipeline.py`
- `crypto_enrich.yml` → `enrich/runner.py`
- `crypto_detect.yml` → `detect/anomaly.py`
- `evening_report.yml` — не залит ещё
- `morning_market_report.yml` — работает
- `crypto_reporter.yml` — работает

**cron-job.org — 3 задачи:**

| Название | Cron |
|---|---|
| ARGUS Collect | `0 * * * *` |
| ARGUS Enrich | `15 * * * *` |
| ARGUS Detect | `30 * * * *` |

**У каждой задачи:**
- Method: POST
- URL: `.../crypto_X.yml/dispatches`
- Headers: `Authorization: Bearer ghp_...`,
  `Accept: application/vnd.github+json`,
  `Content-Type: application/json`
- Body: `{"ref":"main"}`

⚠️ Headers иногда слетают — проверяй
ТЕСТОВЫМ ЗАПУСКОМ (ждём 204).

────────────────────
ЛИМИТЫ SUPABASE FREE
────────────────────

| Ресурс | Лимит | Используем |
|---|---|---|
| БД | 500 МБ | ~5 МБ |
| Egress | 10 ГБ/мес | ~0.8 ГБ |

**Прогноз:** при текущем темпе
~370 строк/день, 500 МБ хватит
на **5-11 лет**.

Retention и партиционирование —
не нужны ещё годы. Мониторить раз
в месяц:
`SELECT pg_size_pretty(
  pg_database_size('postgres'));`

Если >300 МБ — вводим retention.

────────────────────
ЧТО ЕЩЁ NULL В features_hourly
────────────────────

Заполняются **отдельно** (не features.py):

- `oi_change_pct` — из `open_interest`
- `ls_ratio` — из `long_short_ratio`
- `taker_ratio` — из `taker_flow`
- `funding_trend` — тренд funding
- `next_change_pct` — целевая ML
- `next_direction` — целевая ML
- `computed_at` — время расчёта

Это следующий блок работы.

────────────────────
ЧТО ДАЛЬШЕ ПО ПЛАНУ
────────────────────

**Ближайшее (когда вернёмся):**

1. Заполнить `oi_change_pct`, `ls_ratio`,
   `taker_ratio` в features_hourly
2. `computed_at` — просто DEFAULT NOW()
3. `next_change_pct` + `next_direction`
   для ML

**Потом:**

4. Similarity search (похожие окна)
5. Доработка графиков
6. Вечерний техотчёт — залить
7. ML LightGBM (при 500+ свечах)

**Финальное:**

8. brain/controller.py — автономия
9. VPS в EU + Binance как 2-й донор

────────────────────
ПРАВИЛА — НЕ ВЫДУМЫВАТЬ
────────────────────

⚠️ Если не знаешь имени колонки —
спроси. Не гадать.

**Проверено фактически:**

- `funding_rates` → `rate`
- `features_hourly` → `funding_rate`
- `causal_links` → `funding_rate`
- PK features: `(symbol, timestamp)`
- Timestamp с tz (UTC)

**Перед SQL-запросом:**
```sql
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'X';
## 🔧 26. ЖУРНАЛ СЕССИИ — Crypto v3 (2026-09-23)

Что делали, что работает, что на паузе.

────────────────────
ОТЧЁТЫ — ЧТО ЕСТЬ
────────────────────

**Утренний отчёт** (`crypto/report/morning.py` v5):
- Запуск: 09:00 КЛГ (07:00 UTC)
- Workflow: `morning_market_report.yml`
- Spot цена с MEXC/Binance/Bybit/OKX (fallback)
- ATR(14) + стоп-уровни (узкий/широкий)
- RSI(14) с интерпретацией
- Funding + OI с трендом
- Уровни support/resistance
- Блок «Торговые возможности» (риски)
- 10 графиков в одном альбоме
  (свечи+EMA, RSI, funding, OI, pattern × BTC/ETH)

**Вечерний техотчёт** (`crypto/report/evening.py` v2.2):
- Запуск: 20:00 КЛГ (18:00 UTC)
- Workflow: `evening_report.yml`
- Workflows за 24ч + last ok/fail
- Свежесть (candles/features/patterns)
- События за 24ч с деталями
- Аномалии за 24ч с деталями
- Всего в БД (10 таблиц)
- Проблемы (только реальные)

**News отчёт** (`crypto/report/news_report.py` v3):
- Запуск: 08:00 КЛГ (06:00 UTC)
- Workflow: `news.yml`
- Настроение + сентимент
- **Перевод только топ-3+3** через MyMemory
- Дедупликация (не повторяет один и тот же заголовок 7 дней)

**Недельный** (`crypto/report/weekly.py`):
- Воскресенье 05:00 UTC
- Статистика + графики

────────────────────
МОДУЛЬ РИСКОВ
────────────────────

**`crypto/report/risk.py` v1**

- Вход: $10 (POSITION_USD)
- Только LONG (spot)
- Стоп: ATR × 1.5, но не ниже support
- Цель: 2× risk, но не ниже resistance
- R:R минимум 1.5
- Trailing уровни показываются

**Логика сигнала:**
- RSI < 30 + цена у support (<1%) → LONG
- Иначе → WAIT (не показывается)

────────────────────
НОВОСТИ — ПЕРЕВОД
────────────────────

**`crypto/report/translate.py` v6**

- **MyMemory** — основной переводчик
  (бесплатно, без API-ключа)
- **Google** — fallback
- **Словарь замен** после перевода:
  Бикотинский → Bitcoin
  КЦБ → SEC, ККДТ → CFTC
  Попрос → Спрос
- Кэш: `crypto/data/news_translate_cache.json`
- **Переводим только топ-3+3** в news_report
- Analyzer (`crypto/collect/news.py` v6.5)
  работает **без перевода** — считает сентимент
  по оригиналу

**Sentiment v6.5:**
- CONTEXT_RULES (фразы):
  `sec opens/approves` → +
  `sec sues/blocks` → -
  `etf inflows` → +
  `etf outflows` → -
- Одиночные слова SEC/CFTC убраны
  (контекст важнее)

────────────────────
MEXC МОДУЛЬ
────────────────────

**Папка `crypto/mexc/`**

Файлы:
- `client.py` — базовый (HMAC SHA256)
- `account.py` — баланс (read)
- `market.py` — стакан, сделки, 24h
- `orders.py` v2 — открытые + история
- `trades.py` — история сделок

**Ключ на MEXC:**
- Права: Read + Trade (Spot + Futures)
- Без вывода (никогда)
- Без IP whitelist (GitHub меняет IP)
- ⚠️ Срок жизни без IP = 90 дней
  → создать новый через ~80 дней

**GitHub Secrets:**
- `MEXC_API_KEY`
- `MEXC_API_SECRET`

**Структура MEXC клиента:**
- Public GET (без подписи)
- Signed GET/POST/DELETE
- Timestamp + recvWindow 5000

────────────────────
СИМУЛЯТОР 01
────────────────────

**Папка `crypto/mexc/simulator_01/`**

Файлы:
- `runner.py` v1 (залит)
- `runner.py` v2 (готов, но НЕ залит)
- `state/portfolio.json`
- `state/positions.json`
- `state/trades.json`

**Параметры:**
- Стартовый баланс: $50
- Размер позиции: $10
- Max позиций: 1
- Только LONG (spot)
- Учёт комиссий (0.05% taker)
- Учёт slippage (0.05%)
- Только 2 монеты: BTCUSDT, ETHUSDT

**Изоляция:**
- НЕ трогает Supabase
- Всё в JSON внутри папки
- НЕ смешивается с основными данными

**v1 (залит):**
- Сигнал: только уровни
- Работает, но мало сигналов

**v2 (готов, НЕ залит):**
- Сигнал: голосование 2+ из 4:
  • RSI < 30
  • Markov P(1|0) > 0.55
  • Цена у support (<1.5%)
  • Correlations (N≥5, conf≥0.6)
- Стоп: ATR × 1.5
- Цель: 2× risk (макс — resistance)
- Все причины сохраняются в position
  (votes, reasons, rsi_entry, atr_entry)

**Workflow:** `simulator_01.yml`
- cron в cron-job.org: `5 * * * *`
- Уведомления в TG при open/close

────────────────────
CRON-JOB.ORG
────────────────────

Всего **9 задач:**

| Название | Cron |
|---|---|
| ARGUS Collect | `0 * * * *` |
| ARGUS Enrich | `15 * * * *` |
| ARGUS Detect | `30 * * * *` |
| ARGUS Simulator | `5 * * * *` |
| ARGUS News | `0 6 * * *` |
| ARGUS Morning | `0 7 * * *` |
| ARGUS Evening | `0 18 * * *` |

(Simulator + News + Morning могут быть
ещё не добавлены — проверить)

**Headers у всех:**
- Authorization: Bearer ghp_...
- Accept: application/vnd.github+json
- Content-Type: application/json

**Body:** `{"ref":"main"}`
**Method:** POST
**Timezone:** Europe/Kaliningrad

────────────────────
ТЕКУЩИЕ ЗНАЧЕНИЯ
────────────────────

**Схема БД (подтверждена):**

`funding_rates`:
symbol, timestamp, rate, source, inserted_at

`features_hourly` (PK: symbol+timestamp):
symbol, timestamp,
change_pct, range_pct, body_pct,
upper_wick_pct, lower_wick_pct,
volume_ratio_24h,
volatility_24h, volatility_7d,
change_4h, change_24h, change_7d,
funding_rate, funding_trend,
oi_change_pct, ls_ratio, taker_ratio,
next_change_pct, next_direction,
computed_at

`collect_log`:
id, job_name, metric, symbol,
started_at, finished_at,
records_added, source_used,
fallback_count, status, error

`open_interest`:
symbol, timestamp, oi, oi_value,
source, inserted_at

**Пороги:**
- CROSS_DIFF_PCT = 1.2% (было 0.5)
- MIN_SAMPLES (correlate) = 3
- RSI oversold = 30
- RSI overbought = 70

────────────────────
ЧТО РАБОТАЕТ
────────────────────

- ✅ 10 таблиц в Supabase
- ✅ Collect каждый час (13+ запусков/сутки)
- ✅ Enrich с funding (PK защищает от дублей)
- ✅ Detect (pump/cross/wash/stop_hunt)
- ✅ Утренний + вечерний + news отчёты
- ✅ Графики в альбоме (10 шт)
- ✅ MEXC client + account + market
- ✅ Симулятор 01 v1 (пустой, ждёт сигнала)

────────────────────
ЧТО В ОЧЕРЕДИ
────────────────────

1. **Залить `runner.py` v2** симулятора
2. **NULL-колонки** в features:
   `oi_change_pct`, `ls_ratio`, `taker_ratio`
3. **`next_direction`, `next_change_pct`**
   для ML (заполнять при следующей свече)
4. **Similarity search** — похожие окна
5. **ML LightGBM** — 500+ свечей
6. **MEXC order book imbalance** —
   признак для features
7. **Симулятор 02** — сравнение стратегий
8. **Retention 90 дней** для сырья

────────────────────
ТЕХНИЧЕСКИЕ ЗАМЕТКИ
────────────────────

**При правке YAML/Python:**
- Строки ≤ 55 символов (телефон)
- Длинные Python-строки рвутся при копипасте
- В f-строках не используем сложные выражения
- Импорты разбивать по одному на строку

**При правке cron-job.org:**
- Клонировать задачу от Enrich
- Менять только Title / URL / Cron
- Headers/Body не трогать
- Всегда ТЕСТОВЫЙ ЗАПУСК (204 = OK)
- Если 404 — слетели Headers

**При SQL-запросах:**
- По одному (SQL Editor склеивает)
- Имена колонок — сначала
  information_schema.columns
- Не выдумывать — проверять

**Воркфлоу с cron-job:**
- `schedule:` в YAML закомментирован
- Только внешний cron-job.org
- Иначе двойные запуски

**Concurrency:**
- crypto_collect/enrich/detect → общая
  группа `argus-crypto-pipeline`
- Симулятор — своя `argus-simulator-01`
- News — своя `argus-news`

────────────────────
СОЗНАТЕЛЬНО НЕ ДЕЛАЕМ
────────────────────

- ❌ Автоторговля (до 2-3 месяцев)
- ❌ Ордера на MEXC (только чтение)
- ❌ Слияние симулятора с основными данными
- ❌ ML на < 500 свечах
- ❌ Retention (данные не пухнут)
- ❌ Реальный PnL симулятора → в БД
  (всё в JSON)

────────────────────