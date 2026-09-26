📋 ОТЧЁТ ARGUS — 26.09.2026

---

✅ ЧТО СДЕЛАНО

1. Расширение Collect (новые источники)

Добавлено 4 новых коллектора:

· Orderbook (MEXC) — стакан топ-50, imbalance, spread
· Onchain (Mempool.space) — hash rate + difficulty BTC
· Macro (Yahoo) — 10Y Treasury Yield
· Fear & Greed (alternative.me) — индекс страха/жадности

Collect работает полностью: 24 строки за последний прогон, 0 ошибок.

2. Вторая база Supabase (argus-global-data)

· Создана в отдельной организации (обход лимита 2 проекта)
· Тест записи из GitHub — успешно
· Миграция 143 строк — успешно
· Откат: решили не разделять пока (данных мало)

3. ML-эксперименты

Три попытки threshold:

· 0.00% → edge +0.0526 (лучший)
· 0.15% → edge -0.0212
· 0.30% → edge 0.0000

Вывод: threshold работает только на 700+ строках. Откатили на v2.

Train v4: регуляризация (max_depth, lambda_l1/l2, min_data_in_leaf) — для устойчивости на малых данных.

4. Rollback — кнопка в Actions

· crypto/learn/rollback.py + workflow
· Один клик → откат модели из prev/

5. Notify — сигналы в TG

· crypto/learn/notify.py
· Отправляет BUY/SELL при conf > 0.30
· Anti-spam: не повторяет 6ч
· Cron 45 * * * * — создан

---

📊 СОСТОЯНИЕ СИСТЕМЫ

Первая база (argus-db) — основная

Таблица Строк
candles 614
funding 230
features_hourly 424
onchain_metrics 1
macro_metrics 14
fear_greed 1
external_market 121
orderbook_snapshots 6

Вторая база (argus-global-data)

· Пустая (откатили миграцию)
· Живёт на будущее
· ARGUS_DB_URL_2 в Secrets

ML

· Модель откачена к v3 (prev)
· Edge +0.05 (был на 376 строках)
· Ждём накопления до 700+

Симулятор MEXC

· Сделка 1: WIN +$0.0612
· Balance: $50.06
· Win rate: 100%

---

🎯 ЧТО РАБОТАЕТ

· ✅ Collect (9 источников)
· ✅ Enrich (6 шагов)
· ✅ Features (26 колонок)
· ✅ ML pipeline (train → predict → signals → notify)
· ✅ Симулятор v8.1
· ✅ Cron (10 задач)

---

⏳ В ОЧЕРЕДИ

Ближайшее:

1. Дать ML накопить 700+ строк (2-3 дня)
2. Retrain с threshold при достаточных данных

Среднее:

3. Cross-features BTC/ETH
4. Similarity search
5. Autotune LightGBM

Позже:

6. VPS (когда деньги)
7. Binance/Bybit

---

💡 ВЫВОДЫ ДНЯ

Хорошо:

· Collect расширен на 4 источника
· ML показал edge +0.05 (потом упал из-за threshold)
· Вторая база готова

Уроки:

· Не спешить с threshold. Только после 700+ строк.
· Вторую базу не разделять пока данных мало.
· Меньше метаний. Каждое изменение — тестировать.

---

🎯 ДАЛЬШЕ

Завтра:

1. Проверить ночной Learn — обновилась ли модель
2. Проверить notify — ушли ли сигналы в TG
3. Если features > 470 — можно retrain

Через 3 дня:

· 700+ строк → threshold снова пробуем

Через неделю:

· 1000+ строк → edge стабилен

---

Отличный день. Система расширена, но нужна стабилизация. 🦉

Закрываем?