📋 РАПОРТ ARGUS — 25.09.2026

---

🎯 ГЛАВНЫЙ РЕЗУЛЬТАТ ДНЯ

ML-модель нашла торговый сигнал. Edge +0.0526 — впервые положительный.

```
accuracy = 0.6184
baseline = 0.5658
edge     = +0.0526  ← сигнал есть
```

Сигналы сейчас:

· BTCUSDT: BUY [strong] — 79.5%
· ETHUSDT: BUY [strong] — 75.0%

---

✅ СДЕЛАНО СЕГОДНЯ

1. External Market (DXY/SPX/GOLD)

· Таблица external_market создана
· crypto/collect/external.py — Yahoo Finance, без ключа
· Первый сбор: 89 строк (DXY 38, SPX 11, GOLD 37)
· Cron 20 * * * * создан

2. Features v4.4

· Daily: change_1d, change_3d, trend_up
· Calendar: hour_of_day, day_of_week
· 5 новых колонок в features_hourly
· 378 строк, все заполнены

3. ML Pipeline (полный цикл)

dataset.py v2 — 25 признаков + external через lookup
train.py v3 — prev/ + is_unbalance
predict.py v2 — читает external
export.py v2 — CSV + model + meta
signals.py — BUY/WAIT/SELL

Результат:

· edge +0.0526
· TN=17 (модель предсказывает DOWN, не сваливается в UP)
· 189 пар eth_btc
· prev/lgb_model.txt создан

4. Cron-job.org — 10 задач

# Задача Cron
1 Collect 0 * * * *
2 Simulator 5 * * * *
3 Enrich 15 * * * *
4 External 20 * * * *
5 Detect 30 * * * *
6 News 0 6 * * *
7 Morning 0 7 * * *
8 Evening 0 18 * * *
9 Learn 45 1 * * *
10 Data Audit вручную

Лимит 50 — запас 40 свободно.

5. Документация

· Отчёт ARGUS
· Контекст для нового чата
· Структура infra/ для VPS (заложена)

---

📊 СОСТОЯНИЕ СИСТЕМЫ

Компонент Значение
features_hourly 378
candles 1h 332+
candles 1d 188
external_market 89
ML модель edge +0.0526
Модель prev сохранена
Экспорт 2 CSV готовы
Cron задач 10 / 50

---

⏳ В РАБОТЕ

Симулятор v8.1 — залит, надо проверить запуск

ML — ждём накопления:

· 376 → 500+ строк (2-3 дня)
· Edge вырастет

---

🎯 ДАЛЬШЕ

Ближайшее:

1. Симулятор v8.1 запустить
2. Дать ML набрать 500+ строк
3. Order book imbalance — MEXC стакан в features

Среднее:

4. Threshold target (±0.3%)
5. Liquidations OKX
6. Cross-features BTC/ETH

Долгое:

7. Новые монеты (SOL, BNB)
8. VPS + Binance/Bybit
9. Автономия brain/controller

---

💾 ГДЕ ЧТО ЛЕЖИТ

```
argus-core/
├─ crypto/
│  ├─ collect/external.py        ← Yahoo
│  ├─ enrich/features.py         ← v4.4
│  ├─ learn/
│  │  ├─ dataset.py              ← v2
│  │  ├─ train.py                ← v3
│  │  ├─ predict.py              ← v2
│  │  ├─ export.py               ← v2
│  │  ├─ models/
│  │  │  ├─ lgb_model.txt
│  │  │  ├─ model_meta.json
│  │  │  └─ prev/
│  │  └─ export/
│  │     ├─ lgb_model.txt
│  │     ├─ features_hourly.csv
│  │     └─ external_market.csv
│  └─ mexc/simulator_01/runner.py ← v8.1
├─ scripts/data_audit.py          ← v3 SAFE
└─ .github/workflows/
   ├─ crypto_learn.yml
   ├─ external_market.yml
   └─ (остальные crypto_*)
```

---

⚠️ ВАЖНОЕ

Данные копятся:

· features: +48/сутки
· external: +72/сутки
· Supabase Free хватит на 16+ лет

Модель перезаписывается:

· prev — страховка (1 шаг назад)
· Данные участвуют в обучении навсегда

VPS отложен — нет денег. OKX работает, Binance/Bybit позже.

Cron на ночь:

· 03:45 КЛГ — Learn автоматом
· Утром свежая модель готова

---

🎯 МИССИЯ

ARGUS умеет:

· ✅ Читать книги (/ask)
· ✅ Собирать рыночные данные
· ✅ Находить сигналы (ML BUY/WAIT/SELL)
· ✅ Торговать виртуально (симулятор)
· ✅ Самообучаться каждую ночь

Когда ARGUS стабильно прибыльный:

· Подключим реальную торговлю
· Купим VPS
· Добавим Binance/Bybit
· Автономия (brain/controller)

---

Отличный день. Edge впервые положительный. 🦉

Готов к следующей задаче или закрываем.