📋 ARGUS — ОТЧЁТ ЗА 24.09.2026

---

📚 BOOKS (argus-core)

Что сделано:

1. scripts/data_audit.py v3 — SAFE версия
   · Отчёт без изменений
   · --fix — чистка с бэкапами
   · НЕ трогает faiss.index
   · НЕ удаляет chunks
   · Ротация бэкапов (3 свежих)
   · Чистит: HTML, entity, bad_chars, long_word
   · Удаляет дубли книг в books[] и summary[]
2. scripts/ingest.py v7.5 — защита
   · Чистит мусор ДО нарезки
   · Дедуп по filename + file_hash
   · Старые данные не трогает
3. .github/workflows/data_audit.yml
   · Ручной запуск (fix=no / fix=yes)
   · Cron убран
   · Бэкапы не коммитятся
4. Найден data/faiss.index — 7.78 МБ
5. Запуск Train Model — пересобирает faiss (после сбоя /ask)

---

💰 CRYPTO

Features:

1. crypto/enrich/features.py v4.3
   · Добавлены: oi_change_pct, funding_trend, ls_ratio, taker_ratio
   · Добавлены: next_change_pct, next_direction
   · Добавлен: computed_at
   · Fix OI: fallback oi_value → oi
   · Все логи ASCII-safe
2. 5 новых колонок в Supabase:
   · change_1d, change_3d, trend_up
   · hour_of_day, day_of_week
   · (расчёт — задача на завтра)

MEXC модуль:

3. crypto/mexc/client.py v2
   · Retry 3× на 5xx/timeout
   · POST/DELETE через body
   · Проверка code != 0
   · Ключи в __init__
   · recvWindow 10000
4. crypto/mexc/account.py v2 — try/except
5. crypto/mexc/market.py v2 — топ-10 стакана, sanity bid<ask
6. crypto/mexc/orders.py v3 — фильтр FILLED, orderId
7. crypto/mexc/trades.py v2 — fees по asset

Симулятор:

8. crypto/mexc/simulator_01/runner.py v7
   · Проверка свежести analysis (3ч)
   · Проверка свежести свечей (2ч)
   · Cooldown 2ч после стопа
   · Sanity check: stop < price < target
   · UUID вместо len()
   · Fallback get_price
   · Стоп НЕ режется ATR (если support далеко — отказ)
   · Фильтр rules по свежести

ML (новое):

9. crypto/learn/ — создана папка
10. crypto/learn/dataset.py
    · Загрузка X, y из features_hourly
    · Time-split (train=старые, test=новые)
    · FEATURE_COLS (21 признак)
11. crypto/learn/train.py — LightGBM обучение
12. crypto/learn/evaluate.py — метрики + baseline
13. crypto/learn/predict.py — предсказание на последнюю свечу
14. crypto/learn/signals.py — BUY/WAIT/SELL
15. crypto/learn/runner.py — оркестратор
16. crypto/learn/export.py — экспорт для миграции
17. .github/workflows/crypto_learn.yml
    · Ручной запуск
    · train → evaluate → predict → signals → export
    · Commit артефактов
18. crypto/requirements.txt
    ·        · lightgbm==4.5.0
    ·        · scikit-learn==1.5.2

---

📊 РЕЗУЛЬТАТЫ ML

Первый прогон:

```
rows loaded: 330
split: train=264 test=66
accuracy: 0.5303
baseline: 0.5303
edge:     -0.0000
```

Top features:

```
change_4h:        84.62
volatility_24h:   60.37
body_pct:         58.80
volume_ratio_24h: 49.77
change_pct:       39.80
```

Предсказания:

```
BTC: prob_up=0.63 → WAIT [weak]
ETH: prob_up=0.57 → WAIT [weak]
```

Вывод: 330 строк мало. Нужно 500+ для edge.

---

✅ ЧТО РАБОТАЕТ

· Collect: +774 строк/сутки
· Enrich: 6/6 шагов
· Detect: 7 аномалий/24ч
· Features: 330 строк, все колонки заполнены
· Candles 1h: 332
· Candles 1d: 188
· Balance next_direction: 147/181 (45%/55%)
· Симулятор v7: крутится
· ML pipeline: train→predict→signals
· /ask в боте: работает

---

📝 ЗАМЕТКА — ЧТО НУЖНО ДЛЯ ПРОГНОЗА

Критично:

· Daily features (change_1d, change_3d, trend_up)
· Календарные (hour_of_day, day_of_week)

Важно:

· Order book imbalance
· Liquidations
· Корреляции (DXY, SPX)

Позже:

· News sentiment в features
· On-chain метрики
· Макро-календарь

---

🎯 ЗАВТРА

1. Features v4.4 — расчёт daily + calendar колонок
2. Проверить export после Crypto Learn
3. Дать модели набрать данных (500+)
4. Daily features → точнее прогноз

---

💾 СТРАТЕГИЯ МИГРАЦИИ

· Модель lgb_model.txt — переносима
· Папка crypto/learn/export/ — «чемодан» для переезда
· Supabase Free: 500 МБ, хватит на годы
· Retention 90 дней — на будущее, не срочно
· Свой сервер/VPS — когда торговать

---

День закрыт. Всё работает. Ничего не сломано. 🦉