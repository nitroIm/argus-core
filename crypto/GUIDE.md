Понял. Слишком разреженно, куча пустых строк, читать неудобно. Сделал компактно и структурно.

```markdown
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
```

Что исправил:

• Разделители ───── → ━━━ (жирнее, виднее)
• Убрал лишние пустые строки между элементами
• Все длинные описания → в столбик или компактно
• Заголовки секций теперь с жирным **
• Таблицы компактнее, без лишних ----
• Дерево в code fence ``` — не разваливается
• Правила качества — компактный список 1..10
• Промпт для чата — со жирными подзаголовками, легче читается

Должно лечь ровно на телефон, ничего не вылазит. 🦉