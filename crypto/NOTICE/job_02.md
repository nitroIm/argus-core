РЕПОРТ ARGUS — 03.10.2026

---

🔴 СИМУЛЯТОР (crypto/mexc/simulator_01)

runner.py: v9.3 → v9.8

· v9.4 — слиппедж SHORT был в обратную сторону (давал фантомную прибыль). Теперь: LONG открытие +slip, закрытие −slip; SHORT открытие −slip, закрытие +slip
· v9.5 — SOL/BNB читаются из DB2, BTC/ETH из DB1. Была причина few candles (0) — runner искал всё в DB1
· v9.6 — автопоиск db2.py где бы он ни лежал (был ImportError)
· v9.7 — ATR-fallback: если уровень далеко, ставим стоп по ATR, а не отменяем сделку. ETH SHORT перестал резаться
· v9.8 — кулдауны чистятся автоматически (файл cooldowns.json больше не пухнет)

Результат: ETH SHORT открылся, симулятор работает.

explorer.py: v3 → v4

· Уважает action=WAIT от ML-модели. Раньше explorer голосовал даже когда модель говорила «не уверена»

---

🟠 ML-КОНТУР (crypto/learn)

runner.py: v2 → v3

· Обучение (train + evaluate) — раз в сутки, если модель старше 24ч
· Predict + signals — каждый час
· Добавлен STEP 5: learn_weights — обновление весов explorer на закрытых сделках

learn_weights.py — СОЗДАН (новый файл)

· Читает trades.json, только трейды с explorer_breakdown
· Считает hits/misses по каждому источнику (10 источников)
· Формула: w_new = w_old × (1 + 0.2 × (hit_rate − 0.5))
· Границы веса [0.3, 2.0], порог N>=5
· Пишет weights.json → explorer подхватывает

Крон переключён с «раз в сутки» на каждый час через cron-job.org.

---

🌏 ГЛОБАЛЬНЫЙ КОНТУР (crypto/global)

collect_asia.py: v3 → v5

· v4: RANGE=5d→30d, MAX_ROWS=48→300, SAVEPOINT per row
· v5: batch insert — с 20 минут упало до 9 секунд (было 12600 round-trip'ов, стало ~14)
· Fallback на SAVEPOINT если batch падает

asia_patterns.py: v2.4 → v2.6

· v2.5 — MIN_SAMPLES 3→5 (выровняли с фильтром explorer, иначе правила игнорировались)
· v2.6 — фикс гэпов: точки с разрывом >2ч отбрасываются (выходные/межсессии больше не мусорят корреляции)
· Параметризованный SQL, log_run в конце
· Результат: 46 правил (было 26), gap points dropped: 220

notify_asia.py: v1 → v2

· v2: смотрит только свежие движения (2 часа), тянет правила из asia_patterns
· Формат в TG: [DOWN] Nikkei −2.34% + прогноз BTC/ETH + целевая цена + точность + N

crypto_global.yml — порядок шагов

· Было: notify → patterns (уведомления по старым правилам)
· Стало: patterns → notify (по свежим)

---

🔵 БАЗЫ ДАННЫХ И ИНФРАСТРУКТУРА

db.py / db2.py: v3/v2 → v4/v2

· Переподключение при разрыве (drop dead conn → reconnect)
· is_configured() guard — не падает с неясной ошибкой psycopg
· application_name — видно в мониторинге Supabase кто подключён

collect_sol_bnb.py: v2 → v3

· SAVEPOINT per row — одна битая строка не валит всю пачку
· log_run пишет partial если данные не полностью

---

🟢 MEXC-УТИЛИТЫ (read-only, для просмотра)

mexc_orders.py v3 → v4

· None (ошибка API) отделён от [] (пусто)

mexc_trades.py v2 → v3

· days=30 → days=7 (лимит MEXC)
· count считается только по валидным строкам, добавлен skipped

mexc_account.py v2 → v3

· Warning если тикер для монеты не найден
· STABLES = {"USDT", "USDC"} вынесено

---

📊 ТЕКУЩЕЕ СОСТОЯНИЕ БД

DB1 (ARGUS_DB_URL):

· BTC/ETH — candles, features_hourly, funding, OI, LS, taker
· ML-модель обучена (accuracy=0.5137)
· Крон учит/предсказывает каждый час

DB2 (ARGUS_DB_URL_2):

· SOL/BNB: candles 58, funding 11, OI 773
· features_hourly — НЕТ
· long_short_ratio, taker_flow — НЕТ
· Asia market: 14 рынков по ~100-300 точек за 30 дней
· asia_patterns: 46 правил
· impact_vectors: 182 корреляции

---

📋 ЧТО ОСТАЛОСЬ (план на завтра)

DB2 (SOL/BNB) — построить ML-контур:

1. Bulk-загрузчик свечей SOL/BNB за 500-1000 часов (есть или писать)
2. CREATE TABLE features_hourly в DB2 — SQL
3. Адаптировать features.py под DB2 (без ls_ratio/taker_ratio/1d свечей)
4. train_db2.py, predict_db2.py, signals_db2.py
5. learn_weights_db2.py
6. Отдельный workflow crypto_learn_db2.yml
7. Возможно — long_short_ratio, taker_flow для DB2 (нужен источник)

Разобрать ещё файлы:

· crypto/enrich/* — causal, correlate, events, levels, patterns, features, runner
· crypto/report/* — charts, risk
· brain.py, retention.py

---

🟡 НЕ КРИТИЧНО (отложено)

· client.py — Content-Type для POST/DELETE (симулятор их не шлёт)
· mexc_market.py — порог 60% захардкожен
· Правило ≤55 символов — косметика
· db2.py не используется в explorer.py (там свой коннект)
· signal_db2_vectors — почти не влияет на score
· CORE_API_KEY, COINGECKO_API_KEY не настроены
· brain.py — отключён

---

✅ ГЛАВНОЕ

1. Симулятор торгует. ETH SHORT открылся после фиксов.
2. ML учится. Модель + веса explorer + цикл обратной связи.
3. Азиатский контур замкнут. 14 рынков → 46 правил → TG-алерты.
4. Базы разделены. DB1 (BTC/ETH) и DB2 (SOL/BNB) не пересекаются.
5. Скорость выросла. collect_asia с 20 мин до 9 сек.

Завтра — DB2 ML-контур. Он сложнее — там нет таблиц features, нет LS/taker, мало свечей. Но структура та же.

Отдыхай. Завтра с утра — продолжим.